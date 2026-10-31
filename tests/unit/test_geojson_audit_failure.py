"""Combined upstream and audit-storage failure retains accurate attempt counts."""
from pathlib import Path

import pytest

from test_boundary_publication import active_state, database, refresh, seed
from test_geojson_completeness import install_source


def test_rejected_source_and_unwritable_audit_keep_failed_request_accounting(database, monkeypatch):
    conn, _ = database
    _, baseline, _ = seed(database, monkeypatch, "hazard")
    install_source(monkeypatch, "hazard", "count_error", [])
    original = Path.open
    def fail(path, mode="r", *args, **kwargs):
        if path.name == "completeness-check.json" and mode == "x":
            raise OSError("synthetic audit write failure after invalid count")
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", fail)
    with pytest.raises(OSError, match="synthetic audit write failure"):
        refresh(conn, "hazard")
    assert active_state(conn) == baseline
    run_id, requests, size = conn.execute("SELECT run_id,request_count,downloaded_bytes FROM ingestion_run ORDER BY started_at DESC LIMIT 1").fetchone()
    assert requests == 2
    assert size == conn.execute("SELECT sum(byte_size) FROM dataset_snapshot WHERE run_id=?", [run_id]).fetchone()[0]
