"""Legacy HTTP transports must share actual bounded jobs, not a direct engine."""
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from nyc_lens import analysis_jobs
from nyc_lens.analysis_jobs import AnalysisJobs, stop_active_jobs
from nyc_lens import analysis_execution
from nyc_lens.bounded_process import ProcessLimits
from nyc_lens.db import db_session
from nyc_lens.demo import DEMO_ADDRESSES, seed_demo

api = import_module('nyc_lens.api.app')


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('NYC_LENS_HOME', str(tmp_path))
    monkeypatch.delenv('NYC_LENS_DATABASE', raising=False)
    monkeypatch.setenv('NYC_LENS_AI_PROVIDER', 'disabled')
    seed_demo(tmp_path)
    yield tmp_path
    stop_active_jobs()


def payload(operation):
    if operation == 'site':
        return {'address': DEMO_ADDRESSES[0], 'catchments': ['500m']}
    if operation == 'comparison':
        return {'addresses': DEMO_ADDRESSES, 'catchments': ['500m'], 'comparison_catchment': '500m'}
    return {'confirmed': True, 'intent': {'addresses': DEMO_ADDRESSES,
        'business_type': 'full_service_restaurant', 'catchment': '500m', 'priorities': ['competition', 'rent']}}


ROUTES = [('site', '/api/site-analysis'), ('comparison', '/api/site-comparison'),
          ('assistant_comparison', '/api/assistant/compare')]


@pytest.mark.parametrize('operation,route', ROUTES)
def test_legacy_success_uses_owned_worker_and_idempotent_receipt(home, operation, route):
    key = uuid4().hex
    client = TestClient(api.app)
    first = client.post(route, json=payload(operation), headers={'Idempotency-Key': key})
    assert first.status_code == 200, first.text
    assert first.headers.get('X-Analysis-Job-Id') == 'analysis_' + key
    second = client.post(route, json=payload(operation), headers={'Idempotency-Key': key})
    assert second.status_code == 200 and second.json() == first.json()
    receipt = AnalysisJobs().get('analysis_' + key)
    assert receipt['status'] == 'SUCCEEDED' and receipt['process']['process_stopped']
    assert receipt['database_cleanup_confirmed']
    assert receipt['database_settings']['memory_limit'] == '512.0 MiB'
    assert receipt['process']['returncode'] == 0
    result = first.json()
    if operation != 'site':
        assert [r['value'] for r in result['metric_comparisons']['direct_competitor_count']['values']] == [1., 3.]
    with db_session() as conn:
        assert conn.execute('SELECT count(*) FROM site_query').fetchone()[0] == (1 if operation == 'site' else 2)
        assert conn.execute('SELECT count(*) FROM site_comparison').fetchone()[0] == (0 if operation == 'site' else 1)
    assert not list((home / 'data/cache/analysis-workers').glob('owned-*'))


@pytest.mark.parametrize('operation,route', ROUTES)
def test_invalid_idempotency_key_never_enters_engine(home, monkeypatch, operation, route):
    def forbidden(*args, **kwargs):
        pytest.fail('Unvalidated compatibility route entered the serving-process engine')
    for name in ('analyze_site', 'compare_sites', 'execute_comparison'):
        monkeypatch.setattr(api, name, forbidden, raising=False)
    response = TestClient(api.app).post(route, json=payload(operation), headers={'Idempotency-Key': '../invalid'})
    assert response.status_code == 422
    assert not list(AnalysisJobs().directory.glob('analysis_*.json'))


@pytest.mark.parametrize('route,bad', [
    ('/api/site-analysis', {'address': DEMO_ADDRESSES[0], 'script': 'untrusted.py'}),
    ('/api/site-analysis', {'address': 'x' * 201}),
    ('/api/site-analysis', {'address': DEMO_ADDRESSES[0], 'catchments': ['arbitrary_sql']}),
    ('/api/site-comparison', {'addresses': DEMO_ADDRESSES, 'sql': 'SELECT 1'}),
    ('/api/site-comparison', {'addresses': DEMO_ADDRESSES, 'catchments': ['500m'], 'comparison_catchment': '1km'}),
    ('/api/assistant/compare', {**payload('assistant_comparison'), 'confirmed': False}),
])
def test_invalid_compatibility_payload_never_starts_analysis(home, monkeypatch, route, bad):
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid compatibility input entered an analysis engine')
    for name in ('analyze_site', 'compare_sites', 'execute_comparison'):
        monkeypatch.setattr(api, name, forbidden, raising=False)
    response = TestClient(api.app).post(route, json=bad)
    assert response.status_code == 422
    assert not list(AnalysisJobs().directory.glob('analysis_*.json'))


@pytest.mark.parametrize('route', [r for _, r in ROUTES] + ['/api/assistant/interpret'])
def test_legacy_body_is_bounded_before_json_parsing(home, route):
    response = TestClient(api.app).post(route, content=b' ' * 262_145, headers={'Content-Type': 'application/json'})
    assert response.status_code == 413
    assert response.json()['detail']['started'] is False
    assert not list(AnalysisJobs().directory.glob('analysis_*.json'))


def test_timeout_is_saved_recoverable_and_not_automatically_retried(home, monkeypatch):
    monkeypatch.setattr(analysis_execution, 'WORKER_SCRIPT', Path(__file__).parents[1] / 'fixtures/held_analysis_worker.py')
    original = analysis_jobs.execute_bounded_analysis
    def bounded(*args, **kwargs):
        return original(*args, **kwargs, process_limits=ProcessLimits(wall_seconds=10))
    monkeypatch.setattr(analysis_jobs, 'execute_bounded_analysis', bounded)
    # The baseline synchronous route must fail immediately instead of entering
    # the uncontrolled long query used by the bounded worker fixture.
    monkeypatch.setattr(api, 'execute_comparison', lambda *a, **k: pytest.fail('Legacy route bypassed bounded job'), raising=False)
    client, key = TestClient(api.app), uuid4().hex
    response = client.post('/api/assistant/compare', json=payload('assistant_comparison'), headers={'Idempotency-Key': key})
    assert response.status_code == 504, response.text
    detail = response.json()['detail']
    assert detail['job_id'] == 'analysis_' + key and detail['status'] == 'TIMED_OUT'
    receipt = AnalysisJobs().get(detail['job_id'])
    assert receipt['process']['process_stopped'] and receipt['database_cleanup_confirmed']
    assert list(receipt['saved_states'].values()) == ['SUCCEEDED', 'TIMED_OUT', 'TIMED_OUT']
    repeated = client.post('/api/assistant/compare', json=payload('assistant_comparison'), headers={'Idempotency-Key': key})
    assert repeated.status_code == 504 and repeated.json() == response.json()
    changed = payload('assistant_comparison')
    changed['original_request'] = 'Different user intent'
    assert client.post('/api/assistant/compare', json=changed, headers={'Idempotency-Key': key}).status_code == 422
    with db_session() as conn:
        assert conn.execute('SELECT count(*) FROM site_comparison').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM site_query').fetchone()[0] == 2


def test_actual_worker_failure_category_is_readable_without_upstream_error_text(home, monkeypatch):
    monkeypatch.setattr(analysis_execution, 'WORKER_SCRIPT', Path(__file__).parents[1] / 'fixtures/network_failure_worker.py')
    key = uuid4().hex
    response = TestClient(api.app).post('/api/assistant/compare', json=payload('assistant_comparison'),
        headers={'Idempotency-Key': key})
    assert response.status_code == 503
    detail = response.json()['detail']
    assert detail['code'] == 'EXTERNAL_SERVICE_UNAVAILABLE' and detail['status'] == 'FAILED'
    assert detail['job_id'] == 'analysis_' + key and detail['database_cleanup_confirmed'] is True
    assert 'local database is not damaged' in detail['message_en']
    assert 'synthetic-upstream-sensitive-text' not in response.text
    receipt = AnalysisJobs().get(detail['job_id'])
    assert receipt['process']['process_stopped'] and receipt['process']['returncode'] == 1
    assert set(receipt['saved_states'].values()) == {'NOT_CREATED'}
    assert 'synthetic-upstream-sensitive-text' not in AnalysisJobs()._path(detail['job_id']).read_text()
