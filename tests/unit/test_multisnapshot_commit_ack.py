"""A lost COMMIT acknowledgement cannot truthfully be recorded as a rollback."""
from pathlib import Path

import pytest

from test_multisnapshot_publication import (
    SOURCES, ProbeConnection, active_state, database, refresh, seed,
)
from nyc_lens.util import sha256_file


@pytest.mark.parametrize("kind", SOURCES)
def test_lost_commit_acknowledgement_preserves_committed_outcome(database, monkeypatch, kind):
    conn, _ = database
    _, baseline, exports = seed(database, monkeypatch, kind)
    def lose_ack(statement):
        if statement == "COMMIT":
            raise OSError("synthetic connection lost after actual COMMIT")
    with pytest.raises(OSError, match="after actual COMMIT") as raised:
        refresh(ProbeConnection(conn, after=lose_ack), kind)
    assert any("rollback could not be confirmed" in note for note in raised.value.__notes__)
    # The driver error is propagated. The stored outcome is not rewritten merely
    # because the caller failed to receive the successful commit acknowledgement.
    run_id, status, completed = conn.execute(
        "SELECT run_id,status,completed_at FROM ingestion_run ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    assert status == ("SUCCEEDED" if kind == "tiger" else "SUCCEEDED_WITH_WARNINGS")
    assert conn.execute("SELECT last_successful_update FROM source_registry WHERE source_id=?", [SOURCES[kind]]).fetchone() == (completed,)
    assert active_state(conn) != baseline
    assert all(sha256_file(path) == digest for path, digest in exports.items())
    receipt = conn.execute("SELECT raw_path FROM dataset_snapshot WHERE run_id=? AND source_url='local-derived://multisnapshot-publication'", [run_id]).fetchone()
    assert receipt and Path(receipt[0]).is_file()
