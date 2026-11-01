"""A derived receipt for a prepared export, separate from its upstream inputs."""
from pathlib import Path
from typing import Any

from ..provenance import Snapshot, save_bytes_snapshot
from ..util import json_dumps, sha256_file


def prepare_publication_manifest(
    conn,
    *,
    source_id: str,
    run_id: str,
    vintage: str,
    inputs: list[tuple[Snapshot, str]],
    export: Path,
    row_count: int,
    details: dict[str, Any],
) -> Snapshot:
    """Record immutable prepared files; the ingestion run decides publication.

    This receipt is locally derived, never a downloaded source or a replacement
    for each fact's original snapshot. Failed attempts may retain prepared files
    and this receipt, but must not advance active facts or source freshness.
    """
    if not inputs or len({snapshot.snapshot_id for snapshot, _ in inputs}) != len(inputs):
        raise ValueError("Publication requires distinct input snapshots")
    if any(snapshot.source_id != source_id or role not in {"geometry", "count", "metadata", "membership"}
           for snapshot, role in inputs):
        raise ValueError("Publication input source or role mismatch")
    if type(row_count) is not int or row_count < 1:
        raise ValueError("Publication requires a positive standardized row count")
    payload = {
        "schema_version": 1,
        "kind": "derived-multisnapshot-publication",
        "source_id": source_id,
        "run_id": run_id,
        "vintage": vintage,
        "publication_state_authority": "ingestion_run.status for run_id; file existence is not publication",
        "row_count": row_count,
        "inputs": [{"snapshot_id": snapshot.snapshot_id, "role": role,
                    "sha256": snapshot.sha256, "byte_size": snapshot.byte_size,
                    "filename": snapshot.path.name, "vintage": snapshot.vintage}
                   for snapshot, role in inputs],
        "export": {"path": str(export), "sha256": sha256_file(export),
                   "byte_size": export.stat().st_size},
        "details": details,
    }
    return save_bytes_snapshot(
        conn, source_id=source_id, vintage=f"{vintage}-derived-publication",
        filename="publication.json", content=(json_dumps(payload) + "\n").encode("utf-8"),
        source_url="local-derived://multisnapshot-publication", run_id=run_id,
        content_type="application/json", query={"kind": payload["kind"], "run_id": run_id},
        warnings=["Locally derived manifest; not an upstream HTTP response or independent evidence."],
    )
