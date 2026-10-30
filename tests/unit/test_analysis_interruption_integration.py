"""Actual DuckDB connections and synthetic spatial/HTTP recovery."""
import asyncio
from importlib import import_module
import json
import threading

import duckdb
from fastapi.testclient import TestClient
import pytest

from nyc_lens import db
from nyc_lens.analysis_state import record_comparison_failure, record_site_failure
from nyc_lens.demo import DEMO_ADDRESSES, seed_demo


def test_two_connections_cannot_fail_each_others_same_address_attempt(tmp_path, monkeypatch):
    analysis = import_module("nyc_lens.analyze")
    path = str(tmp_path / "synthetic.duckdb")
    ready, release = threading.Event(), threading.Event()
    outcome = []
    conn = duckdb.connect(path)
    conn.execute("""CREATE TABLE site_query(site_id VARCHAR PRIMARY KEY, original_input VARCHAR,
        business_type VARCHAR, status VARCHAR, created_at TIMESTAMP, completed_at TIMESTAMP,
        warnings_json JSON, result_json JSON)""")
    def failing_operation(connection, **kwargs):
        connection.execute("INSERT INTO site_query VALUES (?, 'Same synthetic address', 'full_service_restaurant', 'RUNNING', now(), NULL, '[]', NULL)", [kwargs["site_id"]])
        ready.set()
        assert release.wait(10)
        raise ValueError("synthetic first request failed")
    def worker():
        connection = duckdb.connect(path)
        try:
            analysis.analyze_site(connection, address="Same synthetic address")
        except ValueError:
            outcome.append("EXPECTED_FAILURE")
        finally:
            connection.close()
    monkeypatch.setattr(analysis, "_analyze_site_impl", failing_operation)
    thread = threading.Thread(target=worker)
    try:
        thread.start()
        assert ready.wait(10)
        conn.execute("INSERT INTO site_query VALUES ('unrelated', 'Same synthetic address', 'full_service_restaurant', 'RUNNING', now(), NULL, '[]', NULL)")
        release.set()
        thread.join(10)
        assert not thread.is_alive()
        assert outcome == ["EXPECTED_FAILURE"]
        rows = dict(conn.execute("SELECT site_id, status FROM site_query").fetchall())
        assert rows.pop("unrelated") == "RUNNING"
        assert list(rows.values()) == ["FAILED"]
    finally:
        release.set()
        thread.join(10)
        conn.close()


@pytest.mark.parametrize("record,table,key", [(record_site_failure, "site_query", "site_id"),
    (record_comparison_failure, "site_comparison", "comparison_id")])
def test_terminal_records_are_not_overwritten_and_raw_errors_are_not_stored(record, table, key):
    with duckdb.connect(":memory:") as conn:
        conn.execute(f"CREATE TABLE {table}({key} VARCHAR, status VARCHAR, completed_at TIMESTAMP, warnings_json JSON, result_json JSON, site_ids_json JSON)")
        conn.execute(f"INSERT INTO {table} VALUES ('completed', 'SUCCEEDED', now(), '[]', '{{}}', '[]'), ('owned', 'RUNNING', NULL, '[]', '{{}}', '[]')")
        failure = ValueError("synthetic-private-detail-must-not-be-persisted")
        for identifier in ("completed", "owned"):
            args = (conn, identifier, [], failure) if table == "site_comparison" else (conn, identifier, failure)
            record(*args)
        rows = {row[0]: row[1:] for row in conn.execute(f"SELECT {key},status,warnings_json,result_json FROM {table}").fetchall()}
        assert rows["completed"] == ("SUCCEEDED", "[]", "{}")
        assert rows["owned"][0] == "FAILED" and rows["owned"][2] is None
        assert "synthetic-private-detail" not in rows["owned"][1]
        assert "ValueError" in rows["owned"][1]


def test_actual_interrupted_comparison_hides_partial_results_and_manual_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("NYC_LENS_HOME", str(tmp_path))
    monkeypatch.delenv("NYC_LENS_DATABASE", raising=False)
    monkeypatch.setenv("NYC_LENS_AI_PROVIDER", "disabled")
    seed_demo(tmp_path)
    analysis = import_module("nyc_lens.analyze")
    comparisons = import_module("nyc_lens.comparison")
    original = analysis.calculate_metrics
    calls = []
    def interrupt_second(conn, *args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise asyncio.CancelledError("synthetic interruption after second catchment was saved")
        return original(conn, *args, **kwargs)
    monkeypatch.setattr(analysis, "calculate_metrics", interrupt_second)
    with db.db_session() as conn:
        with pytest.raises(asyncio.CancelledError):
            comparisons.compare_sites(conn, addresses=DEMO_ADDRESSES, catchments=["500m"])
        comparison_id, status, saved_ids, result = conn.execute("SELECT comparison_id,status,site_ids_json,result_json FROM site_comparison").fetchone()
        assert status == "CANCELLED" and result is None
        rows = conn.execute("SELECT site_id,status FROM site_query ORDER BY created_at").fetchall()
        successful = next(identifier for identifier, status in rows if status == "SUCCEEDED")
        cancelled = next(identifier for identifier, status in rows if status == "CANCELLED")
        assert json.loads(saved_ids) == [successful]
        assert comparisons.get_comparison(conn, comparison_id) is None
        assert analysis.get_site(conn, cancelled) is None
        assert analysis.get_site(conn, successful) is not None
        assert conn.execute("SELECT count(*) FROM catchment WHERE site_id=?", [cancelled]).fetchone()[0] == 1
    client = TestClient(import_module("nyc_lens.api.app").app)
    for suffix in ("", "/report?format=json", "/transactions?catchment=500m",
                   "/evidence?catchment=500m&metric_id=direct_competitor_count"):
        response = client.get(f"/api/site/{cancelled}{suffix}")
        assert response.status_code == 404, (suffix, response.text)
    monkeypatch.setattr(analysis, "calculate_metrics", original)
    response = client.post("/api/assistant/compare", json={"confirmed": True, "language": "en",
        "original_request": "Synthetic manual retry after interruption",
        "intent": {"addresses": DEMO_ADDRESSES, "business_type": "full_service_restaurant", "catchment": "500m", "priorities": ["competition"]}})
    assert response.status_code == 200, response.text
    restored = response.json()
    assert [row["value"] for row in restored["metric_comparisons"]["direct_competitor_count"]["values"]] == [1.0, 3.0]
    assert restored["comparison_id"] != comparison_id
    assert not {site["site_id"] for site in restored["sites"]} & {successful, cancelled}
    assert client.get(f"/api/comparison/{restored['comparison_id']}/report?format=json").status_code == 200
    with db.db_session() as conn:
        assert conn.execute("SELECT status FROM site_comparison WHERE comparison_id=?", [comparison_id]).fetchone()[0] == "CANCELLED"
        assert conn.execute("SELECT count(*) FROM site_query WHERE status='RUNNING'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM site_comparison WHERE status='RUNNING'").fetchone()[0] == 0
