"""CLI must distinguish a failed command from an unconfirmed publication outcome."""
from contextlib import contextmanager
import json

import pytest
from typer.testing import CliRunner

from nyc_lens import cli
from test_multisnapshot_publication import SOURCES, ProbeConnection, database, refresh, seed


@pytest.mark.parametrize("kind", SOURCES)
def test_refresh_cli_requires_inspection_after_lost_commit_ack(database, monkeypatch, kind):
    conn, _ = database
    seed(database, monkeypatch, kind)
    def lose_ack(statement):
        if statement == "COMMIT":
            raise OSError("synthetic lost COMMIT acknowledgement")
    @contextmanager
    def session():
        yield conn
    monkeypatch.setattr(cli, "initialize_database", lambda: None)
    monkeypatch.setattr(cli, "db_session", session)
    monkeypatch.setattr(cli, "record_geoservice_status", lambda _conn: {"test": "synthetic; not queried"})
    monkeypatch.setattr(cli, "REFRESHERS", {SOURCES[kind]: lambda _conn: refresh(ProbeConnection(conn, after=lose_ack), kind)})
    result = CliRunner().invoke(cli.app, ["refresh", "--source", SOURCES[kind]])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    outcome = payload[SOURCES[kind]]
    assert outcome["status"] == "INDETERMINATE"
    assert outcome["requires_catalog_inspection"] is True
    run_id, status = conn.execute("SELECT run_id,status FROM ingestion_run ORDER BY started_at DESC LIMIT 1").fetchone()
    assert outcome["recorded_run_id"] == run_id
    assert "before retrying" in outcome["message"]
    assert status == ("SUCCEEDED" if kind == "tiger" else "SUCCEEDED_WITH_WARNINGS")
