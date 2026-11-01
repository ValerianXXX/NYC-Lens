"""The real two-address engine, saved evidence and export rows; fictional data."""
import json
from pathlib import Path
import runpy

import pytest

from nyc_lens.assistant import ComparisonIntent, ConfirmedComparison, execute_comparison
from nyc_lens.db import db_session
from nyc_lens.demo import DEMO_ADDRESSES, seed_demo
from nyc_lens.evidence import query_metric_evidence
from nyc_lens.reporting.comparison_export import _rows


@pytest.fixture(scope='module')
def result(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        home = tmp_path_factory.mktemp('synthetic-census-comparison')
        patch.setenv('NYC_LENS_HOME', str(home))
        patch.delenv('NYC_LENS_DATABASE', raising=False)
        patch.setenv('NYC_LENS_AI_PROVIDER', 'disabled')
        seed_demo(home)
        with db_session() as conn:
            runpy.run_path(str(Path(__file__).parents[1] / 'fixtures/census_scenario.py'))['seed_census_scenario'](conn)
            comparison = execute_comparison(conn, ConfirmedComparison(confirmed=True, intent=ComparisonIntent(
                addresses=DEMO_ADDRESSES, business_type='full_service_restaurant', priorities=['population'])))
            sites = conn.execute('SELECT site_id,result_json FROM site_query ORDER BY standardized_address').fetchall()
            yield conn, comparison, [(identifier, json.loads(value)) for identifier, value in sites]


def test_complete_and_incomplete_sites_keep_distinct_results(result):
    _, comparison, sites = result
    assert comparison['data_mode'] == 'SYNTHETIC_DEMO'
    values = comparison['metric_comparisons']
    assert [r['value'] for r in values['resident_population']['values']] == [300, None]
    assert values['median_household_income']['values'][0]['value'] == pytest.approx(60000, rel=0, abs=1e-8)
    assert values['median_household_income']['values'][1]['value'] is None
    assert [r['value'] for r in values['bachelors_or_higher_share']['values']] == [.4, None]
    assert all(r['high_to_low_rank'] is None for r in values['resident_population']['values'])
    missing = sites[1][1]['catchments']['500m']['metrics']['resident_population']
    assert any('missing' in w.lower() for w in missing['warnings'])
    assert missing['confidence'] is None and missing['margin_of_error'] is None


def test_weighted_summary_resolves_actual_population_and_geometry(result):
    conn, _, sites = result
    response = query_metric_evidence(conn, site_id=sites[0][0], metric_id='median_household_income', catchment='500m')
    assert response['total_evidence_ids'] == 6 and response['unresolved_in_page'] == 0
    assert {r['entity_type'] for r in response['evidence']} == {'census_estimate', 'geo_boundary'}
    assert {r['entity_id'] for r in response['evidence']} == {
        f'synthetic-{kind}-0-{part}' for kind in ('bg', 'pop', 'income') for part in (0, 1)}


def test_health_includes_both_geometry_layers_in_saved_binding(result):
    conn, _, sites = result
    metric = sites[0][1]['catchments']['500m']['metrics']['cdc_places_current_asthma_pct']
    assert metric['value'] == pytest.approx(20)
    response = query_metric_evidence(conn, site_id=sites[0][0], metric_id='cdc_places_current_asthma_pct', catchment='500m')
    assert response['unresolved_in_page'] == 0
    assert {r['entity_id'] for r in response['evidence']} == {
        'synthetic-bg-0-0', 'synthetic-bg-0-1', 'synthetic-tract-0', 'synthetic-tract-pop-0', 'synthetic-health-0'}


def test_export_retains_weight_sources_and_missing_field_explanation(result):
    _, comparison, _ = result
    rows = list(_rows(comparison))
    incomes = [r for r in rows if r['metric_id'] == 'median_household_income']
    refs = json.loads(incomes[0]['source_snapshots_json'])
    assert {r['source_id'] for r in refs} == {'acs_2024_5yr', 'tiger_bg_2024'}
    assert incomes[0]['evidence_bound_records'] == 6
    populations = [r for r in rows if r['metric_id'] == 'resident_population']
    assert populations[1]['value'] is None
    assert 'missing' in populations[1]['warnings'].lower()
