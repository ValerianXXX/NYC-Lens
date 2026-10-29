"""Read-only evidence lookup bound to the saved analysis, shared by Web and MCP."""
from __future__ import annotations

import json
from typing import Any

import duckdb

from .analyze import CATCHMENT_SPECS
from .util import json_dumps


def query_metric_evidence(conn: duckdb.DuckDBPyConnection, *, site_id: str, metric_id: str,
                          catchment: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    if not isinstance(site_id, str) or not 1 <= len(site_id) <= 100 \
            or not isinstance(metric_id, str) or not 2 <= len(metric_id) <= 100 \
            or catchment not in CATCHMENT_SPECS or type(limit) is not int or not 1 <= limit <= 250 \
            or type(offset) is not int or not 0 <= offset <= 1_000_000:
        raise ValueError("Invalid bounded evidence query")
    metric = conn.execute(
        """SELECT m.evidence_record_ids_json, m.source_id, m.snapshot_id,
                  m.calculation_version_id, q.snapshot_set_json, m.source_year, m.unit, m.measure_type
           FROM site_metric m JOIN catchment c ON c.catchment_id=m.catchment_id
           JOIN site_query q ON q.site_id=m.site_id
           WHERE m.site_id=? AND c.catchment_type=? AND m.metric_id=? AND q.status='SUCCEEDED'
           ORDER BY m.created_at DESC LIMIT 1""", [site_id, catchment, metric_id],
    ).fetchone()
    if metric is None:
        raise KeyError("Site metric not found")
    snapshot_set = json.loads(metric[4]) if isinstance(metric[4], str) else metric[4] or {}
    bounds = [{'source_id': source, 'snapshot_id': snapshot}
              for source, snapshots in snapshot_set.items() for snapshot in snapshots]
    scope = 'ANALYSIS_SNAPSHOT_SET'
    if metric[1] and metric[2]:
        # A direct metric must not acquire older or newer versions of the same
        # entity merely because ingestion retained their historical lineage.
        bounds = [row for row in bounds if row == {'source_id': metric[1], 'snapshot_id': metric[2]}]
        scope = 'METRIC_SNAPSHOT'
    total = conn.execute("SELECT count(DISTINCT json_extract_string(value, '$')) FROM json_each(?::JSON)",
                         [metric[0]]).fetchone()[0]
    cursor = conn.execute(
        """WITH evidence_ids AS (
               SELECT DISTINCT json_extract_string(value, '$') AS entity_id FROM json_each(?::JSON)
           ), bounds AS (
               SELECT json_extract_string(value, '$.source_id') AS source_id,
                      json_extract_string(value, '$.snapshot_id') AS snapshot_id FROM json_each(?::JSON)
           ), bound_lineage AS (
               SELECT s.* FROM standardized_record s
               JOIN bounds b ON b.source_id=s.source_id AND b.snapshot_id=s.snapshot_id
               JOIN evidence_ids e ON e.entity_id=s.entity_id
           ), resolved AS (
               SELECT e.entity_id, s.entity_type, s.source_record_id, s.source_id, s.snapshot_id,
                      s.method_version, d.vintage, d.retrieved_at, d.source_url, d.license,
                      d.status AS snapshot_status_at_lookup,
                      r.original_filename, r.sha256 AS raw_sha256,
                      row_number() OVER (PARTITION BY e.entity_id, s.source_id, s.snapshot_id
                                         ORDER BY r.original_filename NULLS LAST) AS raw_rank
               FROM evidence_ids e LEFT JOIN bound_lineage s ON s.entity_id=e.entity_id
               LEFT JOIN dataset_snapshot d ON d.snapshot_id=s.snapshot_id
               LEFT JOIN raw_file r ON r.snapshot_id=d.snapshot_id
           ) SELECT entity_id, entity_type, source_record_id, source_id, snapshot_id, method_version,
                    vintage, retrieved_at, source_url, license, snapshot_status_at_lookup, original_filename, raw_sha256
             FROM resolved WHERE raw_rank=1 OR raw_rank IS NULL
             ORDER BY entity_id, source_id, snapshot_id LIMIT ? OFFSET ?""",
        [metric[0], json_dumps(bounds), limit, offset],
    )
    names = [column[0] for column in cursor.description]
    rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    for row in rows:
        row['lineage_status'] = ('UNRESOLVED_IN_ANALYSIS_SNAPSHOT' if not row['snapshot_id']
                                 else 'SNAPSHOT_METADATA_UNAVAILABLE' if not row['snapshot_status_at_lookup'] else 'RESOLVED')
        row['raw_file_binding'] = 'SNAPSHOT_LEVEL_ONLY' if row['raw_sha256'] else 'UNAVAILABLE'
    return {'site_id': site_id, 'catchment': catchment, 'metric_id': metric_id,
            'total_evidence_ids': total, 'offset': offset, 'limit': limit, 'evidence': rows,
            'binding_scope': scope, 'calculation_version_id': metric[3],
            'metric_basis': dict(zip(['source_id', 'snapshot_id', 'source_year', 'unit', 'measure_type'],
                                     [metric[1], metric[2], metric[5], metric[6], metric[7]])),
            'unresolved_in_page': sum(row['lineage_status'] != 'RESOLVED' for row in rows),
            'raw_file_note': 'Raw file references are snapshot-level candidates, not verified entity-to-file bindings.'}
