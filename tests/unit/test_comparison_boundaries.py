"""Frozen synthetic acceptance cases; no real geocoding or model calls."""
from copy import deepcopy
from importlib import import_module
import json

from fastapi.testclient import TestClient
import pytest

from nyc_lens.assistant import ComparisonIntent, PlanningRequest, decision_brief, interpret
from nyc_lens.comparison import _metric_comparisons
from nyc_lens.db import db_session
from nyc_lens.demo import DEMO_ADDRESSES, seed_demo


@pytest.fixture
def isolated_demo(tmp_path, monkeypatch):
    monkeypatch.setenv('NYC_LENS_HOME', str(tmp_path))
    monkeypatch.delenv('NYC_LENS_DATABASE', raising=False)
    monkeypatch.setenv('NYC_LENS_AI_PROVIDER', 'disabled')
    seed_demo(tmp_path)
    return TestClient(import_module('nyc_lens.api.app').app)


PLANNING_CASES = [
    ('NL_EN', f'Compare "{DEMO_ADDRESSES[0]}" and "{DEMO_ADDRESSES[1]}" for a restaurant, competition and rent within 500m', 'en', 'AWAITING_CONFIRMATION'),
    ('NL_ZH', f'\u4e3a\u9910\u5385\u6bd4\u8f83\u201c{DEMO_ADDRESSES[0]}\u201d\u548c\u201c{DEMO_ADDRESSES[1]}\u201d\uff0c\u5173\u6ce8\u7ade\u4e89\u548c\u79df\u91d1\uff0c500\u7c73', 'zh', 'AWAITING_CONFIRMATION'),
    ('NL_AMBIGUOUS', f'Compare restaurants at "{DEMO_ADDRESSES[0]}", "{DEMO_ADDRESSES[1]}" or "300 Third Fictional Avenue"', 'en', 'NEEDS_INPUT'),
    ('NL_DUPLICATE', f'Compare "{DEMO_ADDRESSES[0]}" and "{DEMO_ADDRESSES[0]}" for a restaurant', 'en', 'NEEDS_INPUT'),
    ('NL_UNSUPPORTED', f'Compare "{DEMO_ADDRESSES[0]}" and "{DEMO_ADDRESSES[1]}" for a dental practice', 'en', 'NEEDS_INPUT'),
]


@pytest.mark.parametrize('identifier,text,language,status', PLANNING_CASES, ids=[x[0] for x in PLANNING_CASES])
def test_planning_boundary(identifier, text, language, status, monkeypatch):
    monkeypatch.setenv('NYC_LENS_AI_PROVIDER', 'disabled')
    result = interpret(PlanningRequest(text=text, language=language))
    assert result['status'] == status
    assert result['original_request'] == text
    assert result['automatic_execution'] is False
    assert result['interpretation'] == 'EXPLICIT_TEXT_HELPER'


def metric_sites(values=(100.0, 200.0)):
    return [{
        'site_id': f'synthetic-site-{index}', 'standardized_address': f'Synthetic address {index}',
        'calculation_version_id': 'synthetic-calculation-v1',
        'catchments': {'500m': {'metrics': {'resident_population': {
            'value': value, 'unit': 'people', 'source_id': 'synthetic_census',
            'snapshot_id': 'synthetic-census-v1', 'source_year': 'SYNTHETIC 2024',
            'measure_type': 'synthetic_fixture', 'calculation_version_id': 'synthetic-calculation-v1',
            'warnings': [],
        }}}},
    } for index, value in enumerate(values)]


@pytest.mark.parametrize('field,value', [
    ('unit', 'people_per_square_km'), ('source_year', 'SYNTHETIC 2020'),
    ('source_id', 'different_synthetic_survey'), ('snapshot_id', None),
    ('calculation_version_id', 'different_calculation'),
], ids=['DIFFERENT_UNIT', 'DIFFERENT_YEAR', 'DIFFERENT_SOURCE', 'MISSING_PROVENANCE', 'DIFFERENT_METHOD'])
def test_incomparable_values_are_not_ranked_or_subtracted(field, value):
    sites = metric_sites()
    sites[1]['catchments']['500m']['metrics']['resident_population'][field] = value
    if field == 'calculation_version_id':
        sites[1]['calculation_version_id'] = value
    metric = _metric_comparisons(sites, '500m')['resident_population']
    assert all(row['high_to_low_rank'] is None for row in metric['values'])
    brief = decision_brief({'metric_comparisons': {'resident_population': metric}},
                           ComparisonIntent(addresses=[], priorities=['population']), 'en')
    assert not brief['differences']
    assert brief['missing'][0]['reason'] == 'MISSING_OR_INCOMPARABLE_SOURCE_DATA'
    assert [row['value'] for row in metric['values']] == [100.0, 200.0]


def test_tied_values_use_competition_ranks_and_other_site_midrank():
    metric = _metric_comparisons(metric_sites((200.0, 200.0, 100.0)), '500m')['resident_population']
    assert [row['high_to_low_rank'] for row in metric['values']] == [1, 1, 3]
    assert [row['within_batch_high_value_percentile'] for row in metric['values']] == [75.0, 75.0, 0.0]
    tied = _metric_comparisons(metric_sites((100.0, 100.0)), '500m')['resident_population']
    assert [row['within_batch_high_value_percentile'] for row in tied['values']] == [50.0, 50.0]


def compare(client):
    intent = ComparisonIntent(addresses=DEMO_ADDRESSES, business_type='full_service_restaurant',
                              priorities=['competition', 'population', 'rent'])
    response = client.post('/api/assistant/compare', json={'intent': intent.model_dump(), 'confirmed': True})
    assert response.status_code == 200, response.text
    return response.json()


def test_missing_source_is_unknown_not_zero(isolated_demo):
    with db_session() as conn:
        conn.execute("UPDATE dataset_snapshot SET status='FAILED'")
    result = compare(isolated_demo)
    assert all(row['value'] is None for row in result['metric_comparisons']['direct_competitor_count']['values'])
    assert not result['assistant']['brief']['differences']
    assert result['assistant']['brief']['composite_score'] is None


def test_later_source_refresh_cannot_rebind_saved_metric_evidence(isolated_demo):
    result = compare(isolated_demo)
    evidence_url = result['assistant']['brief']['differences'][0]['evidence_urls'][0]
    original = isolated_demo.get(evidence_url).json()
    assert {row['snapshot_id'] for row in original['evidence']} == {'synthetic-places-v1'}
    with db_session() as conn:
        conn.execute("""INSERT INTO dataset_snapshot(snapshot_id, source_id, run_id, vintage, retrieved_at, source_url, license, status)
            VALUES ('synthetic-places-v2', 'overture_places', 'synthetic-run', 'SYNTHETIC 2027', now(),
                    'synthetic://nyc-lens/later-v2', 'Synthetic only', 'SUCCEEDED')""")
        conn.execute("""INSERT INTO standardized_record VALUES
            ('later-lineage', 'poi', 'synthetic-poi-0-0', 'overture_places', 'synthetic-places-v2',
             'later-source-record', 'synthetic-v2', now())""")
    refreshed = isolated_demo.get(evidence_url)
    assert refreshed.status_code == 200
    assert {row['snapshot_id'] for row in refreshed.json()['evidence']} == {'synthetic-places-v1'}
    report = isolated_demo.get(f"/api/comparison/{result['comparison_id']}/report?format=json")
    assert report.status_code == 200
    saved = report.json()
    assert saved['snapshot_set'] == result['snapshot_set']
    assert saved['metric_comparisons'] == result['metric_comparisons']


def test_unconfirmed_or_oversized_requests_never_start_analysis(isolated_demo):
    payload = {'intent': {'addresses': DEMO_ADDRESSES, 'business_type': 'full_service_restaurant'}, 'confirmed': False}
    assert isolated_demo.post('/api/assistant/compare', json=payload).status_code == 422
    oversized = deepcopy(payload)
    oversized['confirmed'] = True
    oversized['intent']['unmodeled_requirements'] = ['x' * 4001]
    assert isolated_demo.post('/api/assistant/compare', json=oversized).status_code == 422
    with db_session() as conn:
        assert conn.execute('SELECT count(*) FROM site_query').fetchone()[0] == 0
