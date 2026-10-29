from importlib import import_module
from io import BytesIO
import csv
import math

from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest

from nyc_lens.assistant import ComparisonIntent, ConfirmedComparison, execute_comparison
from nyc_lens.comparison import _metric_comparisons
from nyc_lens.db import db_session
from nyc_lens.demo import seed_demo, DEMO_ADDRESSES


@pytest.fixture
def saved_comparison(tmp_path, monkeypatch):
    monkeypatch.setenv('NYC_LENS_HOME', str(tmp_path))
    monkeypatch.delenv('NYC_LENS_DATABASE', raising=False)
    monkeypatch.setenv('NYC_LENS_AI_PROVIDER', 'disabled')
    seed_demo(tmp_path)
    with db_session() as conn:
        result = execute_comparison(conn, ConfirmedComparison(confirmed=True,
            intent=ComparisonIntent(addresses=DEMO_ADDRESSES, business_type='full_service_restaurant', priorities=['competition'])))
    return TestClient(import_module('nyc_lens.api.app').app), result


def test_missing_or_invalidated_catalogue_does_not_hide_its_state(saved_comparison):
    client, result = saved_comparison
    url = result['assistant']['brief']['differences'][0]['evidence_urls'][0]
    with db_session() as conn:
        conn.execute("UPDATE dataset_snapshot SET status='FAILED' WHERE snapshot_id='synthetic-places-v1'")
    response = client.get(url).json()
    assert response['binding_scope'] == 'METRIC_SNAPSHOT'
    assert response['evidence'][0]['snapshot_status_at_lookup'] == 'FAILED'
    assert response['evidence'][0]['raw_file_binding'] == 'UNAVAILABLE'
    with db_session() as conn:
        conn.execute("DELETE FROM standardized_record WHERE entity_id='synthetic-poi-0-0'")
    response = client.get(url).json()
    assert response['unresolved_in_page'] == 1
    assert response['evidence'][0]['lineage_status'] == 'UNRESOLVED_IN_ANALYSIS_SNAPSHOT'
    assert response['evidence'][0]['snapshot_id'] is None


def test_evidence_pagination_and_identifiers_remain_bounded(saved_comparison):
    client, result = saved_comparison
    url = result['assistant']['brief']['differences'][0]['evidence_urls'][0]
    for suffix in ['&limit=251', '&offset=1000001', '&offset=-1']:
        assert client.get(url + suffix).status_code == 422
    assert client.get(url.replace('direct_competitor_count', 'x' * 101)).status_code == 422
    assert client.get(url.replace('500m', 'unbounded')).status_code == 422


def test_csv_and_xlsx_keep_comparison_basis_and_missingness(saved_comparison):
    client, result = saved_comparison
    root = f"/api/comparison/{result['comparison_id']}/report?format="
    csv_result = client.get(root + 'csv')
    assert csv_result.status_code == 200
    rows = list(csv.DictReader(csv_result.text.lstrip('\ufeff').splitlines()))
    competitors = [row for row in rows if row['metric_id'] == 'direct_competitor_count']
    assert [float(row['value']) for row in competitors] == [1, 3]
    assert all(row['comparability'] == 'COMPARABLE' for row in competitors)
    assert all(row['calculation_version_id'] == result['calculation_version_id'] for row in competitors)
    absent = [row for row in rows if row['metric_id'] == 'resident_population']
    assert all(row['value'] == '' and row['high_to_low_rank'] == '' for row in absent)
    xlsx_result = client.get(root + 'xlsx')
    assert xlsx_result.status_code == 200
    workbook = load_workbook(BytesIO(xlsx_result.content), read_only=True, data_only=False)
    detail = list(workbook.worksheets[1].values)
    headers = detail[0]
    assert {'comparability', 'calculation_version_id', 'comparison_limits'} <= set(headers)
    saved = [dict(zip(headers, row)) for row in detail[1:]]
    assert [row['value'] for row in saved if row['metric_id'] == 'direct_competitor_count'] == [1, 3]
    workbook.close()


def test_nonfinite_or_boolean_values_cannot_acquire_a_rank():
    for bad in [float('nan'), float('inf'), True]:
        results = [{'site_id': str(i), 'standardized_address': 'Synthetic', 'calculation_version_id': 'synthetic-v1',
                    'catchments': {'500m': {'metrics': {'resident_population': {
                        'value': value, 'unit': 'people', 'source_id': 'synthetic', 'snapshot_id': 'snapshot-v1',
                        'source_year': 'SYNTHETIC 2024', 'measure_type': 'synthetic_fixture'}}}}}
                   for i, value in enumerate([bad, 200.0])]
        metric = _metric_comparisons(results, '500m')['resident_population']
        assert metric['values'][0]['value'] is None
        assert metric['values'][0]['warnings'] == ['INVALID_NUMERIC_VALUE']
        assert all(row['high_to_low_rank'] is None for row in metric['values'])
