"""Refuse analysis when a v2 external generation has unfinished source metadata."""
import hashlib
import json

from .storage import _catalog_file


MAX_MANIFEST_BYTES = 16 * 1024 * 1024


def require_ready_external_publications(conn) -> None:
    for table, root, digest, version in conn.execute(
        "SELECT table_name, storage_root, manifest_sha256, layout_version FROM external_table_catalog"
    ).fetchall():
        if version != "2.0.0":
            # Legacy manifests did not capture row-level source snapshot sets.
            continue
        path = _catalog_file(str(root) + "/_manifest.json")
        try:
            with path.open("rb") as stream:
                raw = stream.read(MAX_MANIFEST_BYTES + 1)
            if len(raw) > MAX_MANIFEST_BYTES or hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("EXTERNAL_PUBLICATION_MANIFEST_INVALID")
            manifest = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("EXTERNAL_PUBLICATION_MANIFEST_UNAVAILABLE") from error
        sources = manifest.get("source_snapshots") if isinstance(manifest, dict) else None
        if not isinstance(sources, dict) or manifest.get("table_name") != table:
            raise ValueError("EXTERNAL_PUBLICATION_MANIFEST_INVALID")
        for source, snapshots in sources.items():
            if not isinstance(source, str) or not isinstance(snapshots, list) or not snapshots:
                raise ValueError("EXTERNAL_PUBLICATION_SNAPSHOT_INVALID")
            for snapshot in snapshots:
                if not isinstance(snapshot, str) or not snapshot:
                    raise ValueError("EXTERNAL_PUBLICATION_SNAPSHOT_INVALID")
                complete = conn.execute(
                    """SELECT 1 FROM dataset_snapshot d JOIN ingestion_run r ON r.run_id=d.run_id
                       WHERE d.source_id=? AND d.snapshot_id=? AND d.status='SUCCEEDED'
                         AND r.source_id=d.source_id AND r.status IN ('SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS')
                         AND r.completed_at >= d.retrieved_at""", [source, snapshot]
                ).fetchone()
                if not complete:
                    raise ValueError("EXTERNAL_PUBLICATION_NOT_READY: source metadata must complete before analysis")
