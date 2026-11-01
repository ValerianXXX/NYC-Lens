"""Frozen record/snapshot contracts; every input below is synthetic."""
from copy import deepcopy
import json

import pytest

from nyc_lens.assistant import ComparisonIntent, ConfirmedComparison, execute_comparison
from nyc_lens.comparison import comparison_basis
from nyc_lens.db import db_session
from nyc_lens.demo import DEMO_ADDRESSES, seed_demo
from nyc_lens.evidence import query_metric_evidence
from nyc_lens.reporting.comparison_export import _rows


@pytest.fixture
def saved_split_source(tmp_path, monkeypatch):
    monkeypatch.setenv('NYC_LENS_HOME', str(tmp_path))
    monkeypatch.delenv('NYC_LENS_DATABASE', raising=False)
    monkeypatch.setenv('NYC_LENS_AI_PROVIDER', 'disabled')
    seed_demo(tmp_path)
    with db_session() as conn:
        conn.execute("""INSERT INTO dataset_snapshot(snapshot_id,source_id,run_id,vintage,retrieved_at,source_url,license,status)
            VALUES ('synthetic-part-2','overture_places','synthetic-run','SYNTHETIC part 2',now(),
                    'synthetic://lineage/part-2','Original synthetic fixture','SUCCEEDED')""")
        # The newer source part is used by just one record at the second site.
        conn.execute("UPDATE poi SET snapshot_id='synthetic-part-2' WHERE poi_id='synthetic-poi-1-0'")
        conn.execute("UPDATE standardized_record SET snapshot_id='synthetic-part-2' WHERE entity_id='synthetic-poi-1-0'")
        # Retained history for that same identity must not become a current input.
        conn.execute("""INSERT INTO standardized_record VALUES ('synthetic-retained-old', 'poi',
            'synthetic-poi-1-0','overture_places','synthetic-places-v1','old-copy','synthetic-v0',now())""")
        conn.execute("UPDATE ingestion_run SET completed_at=now() WHERE run_id='synthetic-run'")
        comparison = execute_comparison(conn, ConfirmedComparison(confirmed=True,
            intent=ComparisonIntent(addresses=DEMO_ADDRESSES, business_type='full_service_restaurant', priorities=['competition'])))
        sites = conn.execute("SELECT site_id,result_json FROM site_query ORDER BY standardized_address").fetchall()
        yield conn, comparison, [(site, json.loads(result)) for site, result in sites]


def metric(result):
    return result['catchments']['500m']['metrics']['direct_competitor_count']


def evidence(conn, site, **kwargs):
    return query_metric_evidence(conn, site_id=site, metric_id='direct_competitor_count', catchment='500m', **kwargs)


def test_actual_input_snapshot_replaces_unrelated_newer_catalogue_anchor(saved_split_source):
    conn, _, sites = saved_split_source
    assert metric(sites[0][1])['value'] == 1
    assert metric(sites[0][1])['snapshot_id'] == 'synthetic-places-v1'
    assert metric(sites[0][1])['source_year'] == 'SYNTHETIC 2026'
    response = evidence(conn, sites[0][0])
    assert response['unresolved_in_page'] == 0
    assert response['evidence'][0]['snapshot_id'] == 'synthetic-places-v1'


def test_multisnapshot_records_are_all_resolved_without_old_identity_versions(saved_split_source):
    conn, _, sites = saved_split_source
    assert metric(sites[1][1])['value'] == 3
    response = evidence(conn, sites[1][0])
    assert response['unresolved_in_page'] == 0
    assert [(r['entity_id'], r['snapshot_id']) for r in response['evidence']] == [
        ('synthetic-poi-1-0', 'synthetic-part-2'),
        ('synthetic-poi-1-1', 'synthetic-places-v1'),
        ('synthetic-poi-1-2', 'synthetic-places-v1')]
    saved = metric(sites[1][1])['evidence_binding']
    assert saved['schema'] == 'METRIC_INPUT_BINDINGS_V1'
    assert len(saved['records']) == 3 and saved['unresolved_ids'] == []


def test_saved_bindings_do_not_follow_later_ingestion_or_repair_missing_lineage(saved_split_source):
    conn, _, sites = saved_split_source
    site = sites[1][0]
    before = evidence(conn, site)
    conn.execute("UPDATE poi SET snapshot_id='synthetic-later' WHERE poi_id='synthetic-poi-1-0'")
    conn.execute("""INSERT INTO standardized_record VALUES ('synthetic-later-row','poi','synthetic-poi-1-0',
        'overture_places','synthetic-later','later-copy','synthetic-v3',now())""")
    assert evidence(conn, site)['evidence'] == before['evidence']
    conn.execute("DELETE FROM standardized_record WHERE entity_id='synthetic-poi-1-0' AND snapshot_id='synthetic-part-2'")
    response = evidence(conn, site)
    assert response['unresolved_in_page'] == 1
    row = next(r for r in response['evidence'] if r['entity_id'] == 'synthetic-poi-1-0')
    assert row['snapshot_id'] is None and row['bound_snapshot_id'] == 'synthetic-part-2'
    assert row['lineage_status'] == 'UNRESOLVED_IN_ANALYSIS_SNAPSHOT'


def test_evidence_pages_retain_every_bound_record_once(saved_split_source):
    conn, _, sites = saved_split_source
    pages = [evidence(conn, sites[1][0], limit=1, offset=i) for i in range(3)]
    assert all(p['total_evidence_ids'] == 3 and p['total_evidence_records'] == 3 for p in pages)
    assert [p['next_offset'] for p in pages] == [1, 2, None]
    assert len({p['evidence'][0]['entity_id'] for p in pages}) == 3
    assert all(p['unresolved_in_page'] == 0 for p in pages)


def test_comparison_and_export_retain_all_input_source_snapshots(saved_split_source):
    _, comparison, sites = saved_split_source
    row = comparison['metric_comparisons']['direct_competitor_count']
    assert row['comparability']['status'] == 'INCOMPARABLE'
    assert all(v['high_to_low_rank'] is None for v in row['values'])
    expected = [{'source_id': 'overture_places', 'snapshot_id': 'synthetic-part-2'},
                {'source_id': 'overture_places', 'snapshot_id': 'synthetic-places-v1'}]
    assert metric(sites[1][1])['evidence_binding']['source_snapshots'] == expected
    exported = [r for r in _rows(comparison) if r['metric_id'] == 'direct_competitor_count']
    assert json.loads(exported[1]['source_snapshots_json']) == expected
    assert exported[1]['evidence_bound_records'] == 3


def test_different_mixed_snapshot_sets_cannot_share_a_rank():
    base = {'value': 4.0, 'unit': 'locations', 'source_id': 'source', 'snapshot_id': None,
            'source_year': '2026', 'measure_type': 'count', 'calculation_version_id': 'same-version'}
    a, b = deepcopy(base), deepcopy(base)
    for item, snapshots in ((a, ['a', 'b']), (b, ['a', 'c'])):
        item['evidence_binding'] = {'schema': 'METRIC_INPUT_BINDINGS_V1', 'records': [], 'unresolved_ids': [],
            'source_snapshots': [{'source_id': 'source', 'snapshot_id': s} for s in snapshots]}
    assert comparison_basis([a, b])['status'] == 'INCOMPARABLE'
    assert 'different_source_snapshots' in comparison_basis([a, b])['reasons']


def test_parts_of_one_verified_ingestion_can_remain_comparable():
    base = {'value': 4.0, 'unit': 'locations', 'source_id': 'source', 'snapshot_id': None,
            'source_year': '2026', 'measure_type': 'count', 'calculation_version_id': 'same-version'}
    values = []
    for snapshot in ('part-a', 'part-b'):
        item = deepcopy(base)
        item['evidence_binding'] = {'schema': 'METRIC_INPUT_BINDINGS_V1', 'records': [], 'unresolved_ids': [],
            'source_snapshots': [{'source_id': 'source', 'snapshot_id': snapshot}],
            'generation_ids': [{'source_id': 'source', 'run_id': 'one-completed-source-ingestion'}]}
        values.append(item)
    assert comparison_basis(values)['status'] == 'COMPARABLE'


def test_secondary_weight_source_is_retained_without_promoting_old_lineage(tmp_path, monkeypatch):
    from nyc_lens.metric_lineage import MetricLineageCapture
    from nyc_lens.metrics.engine import MetricValue
    monkeypatch.setenv('NYC_LENS_HOME', str(tmp_path))
    monkeypatch.delenv('NYC_LENS_DATABASE', raising=False)
    seed_demo(tmp_path)
    with db_session() as conn:
        conn.execute("""INSERT INTO census_estimate(census_estimate_id,geography_id,geography_type,variable,survey_period,
            estimate,source_id,snapshot_id,retrieved_at,method_version) VALUES
            ('synthetic-population','synthetic-tract','tract','B01003_001','SYNTHETIC',100,
             'acs_2024_5yr','synthetic-acs',now(),'synthetic-v1')""")
        conn.execute("""INSERT INTO standardized_record VALUES ('synthetic-population-lineage','census_estimate',
            'synthetic-population','acs_2024_5yr','synthetic-acs','population','synthetic-v1',now())""")
        value = MetricValue('synthetic_weighted_metric', 2.0, source_id='overture_places',
            snapshot_id='synthetic-places-v1', evidence_ids=['synthetic-poi-0-0', 'synthetic-population'])
        captured = MetricLineageCapture(conn).capture(value)
        assert captured['source_snapshots'] == [
            {'source_id': 'acs_2024_5yr', 'snapshot_id': 'synthetic-acs'},
            {'source_id': 'overture_places', 'snapshot_id': 'synthetic-places-v1'}]
        assert captured['unresolved_ids'] == []


def test_lineage_without_an_active_typed_input_stays_unresolved(tmp_path, monkeypatch):
    from nyc_lens.metric_lineage import MetricLineageCapture
    from nyc_lens.metrics.engine import MetricValue
    monkeypatch.setenv('NYC_LENS_HOME', str(tmp_path))
    monkeypatch.delenv('NYC_LENS_DATABASE', raising=False)
    seed_demo(tmp_path)
    with db_session() as conn:
        conn.execute("""INSERT INTO standardized_record VALUES ('history-only','poi','history-only',
            'overture_places','synthetic-places-v1','history','synthetic-v0',now())""")
        value = MetricValue('synthetic_metric', 1.0, source_id='overture_places',
            snapshot_id='synthetic-places-v1', evidence_ids=['history-only'])
        captured = MetricLineageCapture(conn).capture(value)
        assert captured['records'] == [] and captured['unresolved_ids'] == ['history-only']
