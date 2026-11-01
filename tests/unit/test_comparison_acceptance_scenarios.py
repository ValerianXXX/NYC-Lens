"""Twelve frozen tasks; explicit scopes are in the dated synthetic catalog."""
from copy import deepcopy
import csv
from importlib import import_module
from io import BytesIO, StringIO
import json
from pathlib import Path
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
from openpyxl import load_workbook
import pytest

from nyc_lens.analysis_jobs import TERMINAL, stop_active_jobs
from nyc_lens.assistant import ComparisonIntent, decision_brief
from nyc_lens.comparison import _metric_comparisons
from nyc_lens.db import db_session
from nyc_lens.demo import DEMO_ADDRESSES, seed_demo
from nyc_lens.geocoding.service import geocode_address

FIRST, SECOND = DEMO_ADDRESSES
ENGLISH = f'Compare "{FIRST}" and "{SECOND}" for a restaurant within 500m; consider competition, population and rent.'
CHINESE = f'比较“{FIRST}”和“{SECOND}”开餐厅的条件，范围500米，关注竞争、人口和租金。'


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('NYC_LENS_HOME', str(tmp_path))
    monkeypatch.delenv('NYC_LENS_DATABASE', raising=False)
    monkeypatch.setenv('NYC_LENS_AI_PROVIDER', 'disabled')
    seed_demo(tmp_path)
    with TestClient(import_module('nyc_lens.api.app').app) as client:
        yield client, tmp_path
    stop_active_jobs()


def interpret(client, text=ENGLISH, language='en'):
    response = client.post('/api/assistant/interpret', json={'text': text, 'language': language})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['automatic_execution'] is False and value['interpretation'] == 'EXPLICIT_TEXT_HELPER'
    return value


def complete(client, proposal):
    assert proposal['status'] == 'AWAITING_CONFIRMATION'
    # A distinct explicit confirmation request, never inferred from interpretation.
    response = client.post('/api/analysis-jobs', json={'operation': 'assistant_comparison',
        'request_id': uuid4().hex, 'request': {'intent': proposal['intent'], 'confirmed': True,
            'language': proposal['language'], 'original_request': proposal['original_request']}})
    assert response.status_code == 202, response.text
    identifier = response.json()['job_id']
    deadline = time.monotonic() + 150  # Observe the existing 120-second product guard plus startup/cleanup.
    while time.monotonic() < deadline:
        observation = client.get('/api/analysis-jobs/' + identifier)
        assert observation.status_code == 200, observation.text
        receipt = observation.json()
        if receipt['status'] in TERMINAL:
            break
        time.sleep(.05)
    else:
        pytest.fail('The owned analysis did not report its terminal state')
    assert receipt['status'] == 'SUCCEEDED' and receipt['database_cleanup_confirmed'], receipt
    response = client.get('/api/analysis-jobs/' + identifier + '/result')
    assert response.status_code == 200, response.text
    return response.json()


def competitor_values(result):
    return result['metric_comparisons']['direct_competitor_count']['values']


def historical_source():
    with db_session() as conn:
        conn.execute("""UPDATE dataset_snapshot SET vintage='SYNTHETIC 2011',
            retrieved_at=TIMESTAMP '2015-01-02 03:04:05', valid_from=DATE '2010-01-01', valid_to=DATE '2011-12-31'
            WHERE snapshot_id='synthetic-places-v1'""")


@pytest.mark.parametrize('text,language', [(ENGLISH, 'en'), (CHINESE, 'zh')], ids=['english-complete', 'chinese-complete'])
def test_confirmed_two_address_workflow_preserves_maps_evidence_missingness_and_exports(workspace, text, language):
    client, _ = workspace
    proposal = interpret(client, text, language)
    with db_session() as conn:
        assert conn.execute('SELECT count(*) FROM site_comparison').fetchone()[0] == 0
    result = complete(client, proposal)
    assert result['data_mode'] == 'SYNTHETIC_DEMO'
    assert [row['value'] for row in competitor_values(result)] == [1, 3]
    brief = result['assistant']['brief']
    assert brief['language'] == language and brief['composite_score'] is None
    assert {'resident_population', 'asking_rent'} <= {row['metric_id'] for row in brief['missing']}
    item, = [row for row in brief['differences'] if row['metric_id'] == 'direct_competitor_count']
    assert item['first_minus_second'] == -2
    for site, url in zip(result['sites'], item['evidence_urls']):
        detail = client.get('/api/site/' + site['site_id']).json()
        assert detail['catchments']['500m']['geometry']['type'] == 'Polygon'
        evidence = client.get(url).json()
        assert evidence['binding_scope'] == 'METRIC_SNAPSHOT' and evidence['unresolved_in_page'] == 0
        assert {row['snapshot_id'] for row in evidence['evidence']} == {'synthetic-places-v1'}
    endpoint = '/api/comparison/' + result['comparison_id'] + '/report?format='
    assert client.get(endpoint + 'json').json() == result
    rows = list(csv.DictReader(StringIO(client.get(endpoint + 'csv').text.lstrip('\ufeff'))))
    assert [float(row['value']) for row in rows if row['metric_id'] == 'direct_competitor_count'] == [1, 3]
    workbook = load_workbook(BytesIO(client.get(endpoint + 'xlsx').content), read_only=True, data_only=False)
    assert workbook.worksheets[1].max_row == len(rows) + 1
    workbook.close()


@pytest.mark.parametrize('text,missing', [
    (f'Restaurant near "{FIRST}"', 'two_addresses'),
    (f'Restaurant at "{FIRST}" and "{FIRST}"', 'two_addresses'),
    (f'Restaurant at "{FIRST}", "{SECOND}" and "300 Demo Avenue, Newark, NJ"', 'two_addresses'),
    (f'Coffee shop at "{FIRST}" and "{SECOND}"', 'supported_business_type'),
], ids=['one-address', 'duplicate-address', 'three-addresses', 'unsupported-business'])
def test_missing_or_unsupported_requirements_do_not_start_analysis(workspace, text, missing):
    client, _ = workspace
    result = interpret(client, text)
    assert result['status'] == 'NEEDS_INPUT' and missing in result['missing']
    with db_session() as conn:
        assert conn.execute('SELECT count(*) FROM site_query').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM site_comparison').fetchone()[0] == 0


def test_walking_requirement_remains_unresolved_after_explicit_radial_confirmation(workspace):
    client, _ = workspace
    text = f'Restaurant at "{FIRST}" and "{SECOND}"; I need a 15 minute walk and competition counts.'
    proposal = interpret(client, text)
    assert text in proposal['intent']['unmodeled_requirements']
    assert proposal['intent']['catchment'] == '500m'
    result = complete(client, proposal)
    assert result['catchments'] == ['500m']
    assert text in result['assistant']['brief']['unmodeled_requirements']


def test_ambiguous_official_format_matches_are_retained_without_choosing_the_first(workspace):
    _, _home = workspace
    matches = [{'matchedAddress': f'{number} Fictional Road, Newark, NJ',
        'coordinates': {'x': longitude, 'y': 40.735},
        'geographies': {'Counties': [{'GEOID': '34013'}]}}
        for number, longitude in [(100, -74.17), (200, -74.15)]]
    transport = httpx.MockTransport(lambda request: httpx.Response(200, request=request,
        json={'result': {'addressMatches': matches}}))
    with db_session() as conn, httpx.Client(transport=transport) as client:
        with pytest.raises(ValueError, match='ADDRESS_MATCH_AMBIGUOUS'):
            geocode_address(conn, 'Fictional Road, Newark, NJ', client=client, max_attempts=1)
        assert conn.execute("SELECT count(*) FROM address WHERE source_id='census_geocoder'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM dataset_snapshot WHERE source_id='census_geocoder'").fetchone()[0] == 1


def test_failed_source_is_missing_instead_of_an_observed_zero(workspace):
    client, _ = workspace
    with db_session() as conn:
        conn.execute("UPDATE dataset_snapshot SET status='FAILED' WHERE snapshot_id='synthetic-places-v1'")
    result = complete(client, interpret(client))
    assert all(row['value'] is None and row['high_to_low_rank'] is None for row in competitor_values(result))
    assert 'direct_competitor_count' in {row['metric_id'] for row in result['assistant']['brief']['missing']}


def test_old_observation_exposes_retrieval_and_reference_period_without_claiming_expiry(workspace):
    client, _ = workspace
    historical_source()
    result = complete(client, interpret(client))
    for value in competitor_values(result):
        observation = value['source_observation']
        assert observation['capture_state'] == 'CAPTURED'
        assert observation['retrieved_at'].startswith('2015-01-02T03:04:05')
        assert observation['retrieval_age_days'] > 365
        assert observation['reference_start'] == '2010-01-01' and observation['reference_end'] == '2011-12-31'
        assert observation['vintage'] == 'SYNTHETIC 2011' and value['source_year'] == 'SYNTHETIC 2011'
        assert observation['currentness'] == 'NOT_ASSESSED'


def test_mixed_units_cannot_produce_a_rank_or_decision_difference():
    inputs = []
    for index, unit in enumerate(['persons', 'households']):
        inputs.append({'site_id': f'synthetic-{index}', 'standardized_address': DEMO_ADDRESSES[index],
            'calculation_version_id': 'synthetic-version', 'catchments': {'500m': {'metrics': {'resident_population': {
                'value': 100 + index, 'unit': unit, 'source_id': 'synthetic', 'snapshot_id': 'synthetic-v1',
                'source_year': 'SYNTHETIC 2026', 'measure_type': 'synthetic_count'}}}}})
    comparisons = _metric_comparisons(inputs, '500m')
    metric = comparisons['resident_population']
    assert metric['comparability']['status'] == 'INCOMPARABLE'
    assert all(row['high_to_low_rank'] is None for row in metric['values'])
    brief = decision_brief({'metric_comparisons': comparisons}, ComparisonIntent(
        addresses=DEMO_ADDRESSES, business_type='full_service_restaurant', priorities=['population']), 'en')
    assert not brief['differences'] and brief['missing'][0]['metric_id'] == 'resident_population'


def test_saved_report_observations_do_not_change_when_the_catalog_changes(workspace):
    client, _ = workspace
    historical_source()
    result = complete(client, interpret(client))
    captured = deepcopy(competitor_values(result)[0]['source_observation'])
    with db_session() as conn:
        conn.execute("""UPDATE dataset_snapshot SET retrieved_at=now(), valid_from=NULL, valid_to=NULL,
            vintage='SYNTHETIC changed later' WHERE snapshot_id='synthetic-places-v1'""")
    endpoint = '/api/comparison/' + result['comparison_id'] + '/report?format='
    exported = client.get(endpoint + 'json').json()
    assert competitor_values(exported)[0]['source_observation'] == captured
    rows = list(csv.DictReader(StringIO(client.get(endpoint + 'csv').text.lstrip('\ufeff'))))
    item = next(row for row in rows if row['metric_id'] == 'direct_competitor_count')
    assert item['source_retrieved_at'].startswith('2015-01-02T03:04:05')
    assert item['source_reference_end'] == '2011-12-31' and item['source_currentness'] == 'NOT_ASSESSED'
    workbook = load_workbook(BytesIO(client.get(endpoint + 'xlsx').content), read_only=True, data_only=False)
    values = list(workbook.worksheets[1].values)
    records = [dict(zip(values[0], row)) for row in values[1:]]
    item = next(row for row in records if row['metric_id'] == 'direct_competitor_count')
    assert item['source_retrieved_at'].startswith('2015-01-02T03:04:05')
    assert item['source_reference_end'] == '2011-12-31'
    workbook.close()
