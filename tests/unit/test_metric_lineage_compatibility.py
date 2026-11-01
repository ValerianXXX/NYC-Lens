"""Additional failure/legacy checks; separate from the nine frozen contracts."""
from copy import deepcopy
import json

import pytest

from nyc_lens.comparison import comparison_basis
from nyc_lens.evidence import query_metric_evidence
from nyc_lens.metric_lineage import MetricLineageCapture
from nyc_lens.metrics.engine import MetricValue
from test_metric_input_lineage import saved_split_source


@pytest.mark.parametrize('change', [
    "UPDATE ingestion_run SET status='FAILED'",
    "UPDATE ingestion_run SET completed_at=NULL",
    "UPDATE ingestion_run SET source_id='different-source'",
])
def test_unverified_inputs_cannot_support_same_snapshot_ranks(saved_split_source, change):
    conn, _, _ = saved_split_source
    conn.execute(change)
    value = MetricValue('count', 1, source_id='overture_places',
        snapshot_id='synthetic-places-v1', evidence_ids=['synthetic-poi-0-0'])
    binding = MetricLineageCapture(conn).capture(value)
    assert binding['records'] and not binding['unresolved_ids']
    assert binding['generation_ids'] == []
    assert binding['unverified_source_snapshots'] == [
        {'source_id': 'overture_places', 'snapshot_id': 'synthetic-places-v1'}]
    row = {'value': 1, 'unit': 'locations', 'source_id': 'overture_places',
        'snapshot_id': 'synthetic-places-v1', 'source_year': 'SYNTHETIC',
        'measure_type': 'count', 'calculation_version_id': 'same', 'evidence_binding': binding}
    basis = comparison_basis([row, deepcopy(row)])
    assert basis['status'] == 'INCOMPARABLE'
    assert 'UNVERIFIED_INPUT_SNAPSHOTS' in basis['reasons']


def test_legacy_result_does_not_acquire_current_record_bindings(saved_split_source):
    conn, _, sites = saved_split_source
    site, result = sites[0]
    result['catchments']['500m']['metrics']['direct_competitor_count'].pop('evidence_binding')
    conn.execute('UPDATE site_query SET result_json=?::JSON WHERE site_id=?', [json.dumps(result), site])
    conn.execute("UPDATE poi SET snapshot_id='newer-active' WHERE poi_id='synthetic-poi-0-0'")
    response = query_metric_evidence(conn, site_id=site, metric_id='direct_competitor_count', catchment='500m')
    assert response['binding_version'] == 'LEGACY_SNAPSHOT_SCOPE'
    assert response['binding_scope'] == 'METRIC_SNAPSHOT'
    assert response['unresolved_in_page'] == 0
    assert [row['snapshot_id'] for row in response['evidence']] == ['synthetic-places-v1']
