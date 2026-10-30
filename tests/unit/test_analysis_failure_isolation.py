"""Freeze operation ownership before adding deadline or cancellation controls."""
import asyncio
from datetime import datetime, timezone
from importlib import import_module
import json

import duckdb
import pytest

analysis = import_module("nyc_lens.analyze")


@pytest.fixture
def connection():
    conn = duckdb.connect(":memory:")
    conn.execute("""CREATE TABLE site_query(
        site_id VARCHAR PRIMARY KEY, original_input VARCHAR, business_type VARCHAR,
        status VARCHAR, created_at TIMESTAMP, completed_at TIMESTAMP,
        warnings_json JSON, result_json JSON)""")
    try:
        yield conn
    finally:
        conn.close()


def insert(conn, site_id, status="RUNNING"):
    conn.execute("INSERT INTO site_query VALUES (?, 'Same synthetic address', 'full_service_restaurant', ?, ?, NULL, '[]', NULL)",
                 [site_id, status, datetime.now(timezone.utc)])


@pytest.mark.parametrize("exception,terminal", [(ValueError, "FAILED"), (RuntimeError, "FAILED"),
    (KeyboardInterrupt, "CANCELLED"), (asyncio.CancelledError, "CANCELLED")])
def test_only_owned_attempt_is_closed_when_same_address_is_running(connection, monkeypatch, exception, terminal):
    owned = []
    def fail(conn, **kwargs):
        identifier = kwargs.get("site_id", "site-owned-baseline")
        owned.append(identifier)
        insert(conn, identifier)
        insert(conn, "site-unrelated")
        raise exception("synthetic source failure")
    monkeypatch.setattr(analysis, "_analyze_site_impl", fail)
    with pytest.raises(exception):
        analysis.analyze_site(connection, address="Same synthetic address")
    rows = dict(connection.execute("SELECT site_id, status FROM site_query").fetchall())
    assert rows[owned[0]] == terminal
    assert rows["site-unrelated"] == "RUNNING"


def test_error_before_own_insert_cannot_close_another_attempt(connection, monkeypatch):
    def fail(conn, **kwargs):
        insert(conn, "site-unrelated")
        raise ValueError("failure before own row")
    monkeypatch.setattr(analysis, "_analyze_site_impl", fail)
    with pytest.raises(ValueError):
        analysis.analyze_site(connection, address="Same synthetic address")
    assert connection.execute("SELECT status FROM site_query").fetchone()[0] == "RUNNING"


def test_terminal_write_failure_does_not_mask_original_error(connection, monkeypatch):
    original = ValueError("original synthetic failure")
    def fail(conn, **kwargs):
        raise original
    class BrokenStatusConnection:
        def execute(self, *args, **kwargs):
            raise RuntimeError("status-write unavailable")
    monkeypatch.setattr(analysis, "_analyze_site_impl", fail)
    with pytest.raises(ValueError) as raised:
        analysis.analyze_site(BrokenStatusConnection(), address="Same synthetic address")
    assert raised.value is original
    assert any("terminal status" in note.lower() for note in original.__notes__)


@pytest.mark.parametrize("status", ["FAILED", "CANCELLED", "RUNNING"])
def test_unsuccessful_saved_result_is_not_exposed(connection, status):
    insert(connection, "site-previous", status)
    connection.execute("UPDATE site_query SET result_json=?::JSON", [json.dumps({"site_id": "site-previous", "partial": True})])
    assert analysis.get_site(connection, "site-previous") is None


def test_successful_result_remains_readable(connection):
    insert(connection, "site-previous", "SUCCEEDED")
    value = {"site_id": "site-previous", "synthetic": True}
    connection.execute("UPDATE site_query SET result_json=?::JSON", [json.dumps(value)])
    assert analysis.get_site(connection, "site-previous") == value
