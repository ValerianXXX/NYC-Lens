"""Capture declared metric evidence against active inputs, never archive history."""
from __future__ import annotations

from typing import Any

import duckdb

from .record_identity import LINEAGE_TABLE_IDS
from .source_observation import SourceObservationCapture
from .util import json_dumps


class MetricLineageCapture:
    """One bounded identity lookup per distinct input ID within an analysis.

    The engine still declares which IDs contributed. This captures their active
    typed-record versions, not all implicit geometry/weight/benchmark inputs.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self.conn = conn
        self.cache: dict[str, list[dict[str, str]]] = {}
        self.generation_cache: dict[tuple[str, str], str | None] = {}
        self.available = {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()}

    def _capture(self, identifiers: list[str]) -> None:
        for start in range(0, len(identifiers), 10_000):
            batch = identifiers[start:start + 10_000]
            for identifier in batch:
                self.cache[identifier] = []
            encoded = json_dumps(batch)
            for table, column in LINEAGE_TABLE_IDS.items():
                if table not in self.available:
                    continue
                rows = self.conn.execute(
                    f"""SELECT DISTINCT CAST(t.{column} AS VARCHAR), t.source_id, t.snapshot_id
                        FROM {table} t JOIN json_each(?::JSON) requested
                          ON CAST(t.{column} AS VARCHAR)=json_extract_string(requested.value, '$')
                        WHERE t.source_id IS NOT NULL AND t.snapshot_id IS NOT NULL""", [encoded]).fetchall()
                for entity_id, source_id, snapshot_id in rows:
                    self.cache[entity_id].append({'entity_id': entity_id, 'entity_type': table,
                        'source_id': str(source_id), 'snapshot_id': str(snapshot_id)})

    def _generation(self, source_id: str, snapshot_id: str) -> str | None:
        key = (source_id, snapshot_id)
        if key not in self.generation_cache:
            row = self.conn.execute("""SELECT d.run_id FROM dataset_snapshot d
                JOIN ingestion_run r ON r.run_id=d.run_id AND r.source_id=d.source_id
                WHERE d.snapshot_id=? AND d.source_id=? AND d.status='SUCCEEDED'
                  AND r.status IN ('SUCCEEDED','SUCCEEDED_WITH_WARNINGS')
                  AND r.completed_at>=d.retrieved_at""", [snapshot_id, source_id]).fetchone()
            self.generation_cache[key] = str(row[0]) if row and row[0] else None
        return self.generation_cache[key]

    def capture(self, metric: Any) -> dict[str, Any]:
        identifiers = sorted({str(value) for value in metric.evidence_ids or []})
        missing = [identifier for identifier in identifiers if identifier not in self.cache]
        self._capture(missing)
        records = sorted((record.copy() for identifier in identifiers for record in self.cache[identifier]),
            key=lambda r: (r['entity_id'], r['entity_type'], r['source_id'], r['snapshot_id']))
        refs = sorted({(record['source_id'], record['snapshot_id']) for record in records})
        generation_pairs = [(source, self._generation(source, snapshot)) for source, snapshot in refs]
        generations = sorted(set(generation_pairs)) if generation_pairs and all(run for _, run in generation_pairs) else []
        return {'schema': 'METRIC_INPUT_BINDINGS_V1', 'scope': 'DECLARED_EVIDENCE_ACTIVE_TYPED_INPUTS',
            'records': records, 'unresolved_ids': [identifier for identifier in identifiers if not self.cache[identifier]],
            'source_snapshots': [{'source_id': source, 'snapshot_id': snapshot} for source, snapshot in refs],
            'unverified_source_snapshots': [{'source_id': source, 'snapshot_id': snapshot}
                for source, snapshot in refs if self._generation(source, snapshot) is None],
            'generation_ids': [{'source_id': source, 'run_id': run} for source, run in generations],
            'limitation': 'Bindings cover the evidence IDs declared by the calculation; they do not establish that every implicit weighting, geometry or benchmark input was declared.'}


def align_metric_snapshot(metric: Any, binding: dict[str, Any], observations: SourceObservationCapture) -> None:
    """Rebind source snapshot/vintage labels without replacing computed periods."""
    if not metric.snapshot_id or not binding['records'] or metric.source_id in (None, 'derived'):
        return
    snapshots = {r['snapshot_id'] for r in binding['records'] if r['source_id'] == metric.source_id}
    if snapshots:
        anchor_vintage = observations.capture(metric.source_id, metric.snapshot_id).get('vintage')
        # Some measures declare a calculation period (for example a rolling
        # transaction range). Only replace a label inherited from the catalogue.
        if metric.source_year and metric.source_year == anchor_vintage:
            vintages = [observations.capture(metric.source_id, snapshot).get('vintage') for snapshot in snapshots]
            metric.source_year = ' / '.join(sorted(set(vintages))) if all(vintages) else None
        metric.snapshot_id = next(iter(snapshots)) if len(snapshots) == 1 else None
