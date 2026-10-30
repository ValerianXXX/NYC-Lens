"""Actual subprocess + synthetic DuckDB/geometry, not a mock process receipt."""
import json
from pathlib import Path
import threading
import time

import pytest

from nyc_lens import analysis_execution as execution
from nyc_lens.analysis_contracts import normalized_request
from nyc_lens.bounded_process import ProcessLimits
from nyc_lens.database_admission import DatabaseBusyError, database_admission
from nyc_lens.db import db_session
from nyc_lens.demo import DEMO_ADDRESSES, seed_demo
from nyc_lens.evidence import query_metric_evidence
from nyc_lens.execution_context import DatabaseLimits, allocate_identity, database_options, result_json, worker_context
from nyc_lens.reporting.comparison_export import export_comparison_json


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("NYC_LENS_HOME", str(tmp_path))
    monkeypatch.delenv("NYC_LENS_DATABASE", raising=False)
    monkeypatch.setenv("NYC_LENS_AI_PROVIDER", "disabled")
    seed_demo(tmp_path)
    return tmp_path


def request():
    return {"confirmed": True, "language": "en", "original_request": "Synthetic comparison with competition and missing rent",
            "intent": {"addresses": DEMO_ADDRESSES, "business_type": "full_service_restaurant",
                       "catchment": "500m", "priorities": ["competition", "rent"]}}


def test_real_worker_result_evidence_report_and_effective_budgets(home):
    receipt = execution.execute_bounded_analysis("assistant_comparison", request())
    assert receipt.status == "SUCCEEDED", receipt
    assert receipt.process.process_stopped and receipt.process.returncode == 0
    assert receipt.database_cleanup_confirmed
    result = receipt.result
    assert result["comparison_id"] == receipt.comparison_ids[0]
    assert [site["site_id"] for site in result["sites"]] == receipt.site_ids
    assert set(receipt.saved_states.values()) == {"SUCCEEDED"}
    assert [row["value"] for row in result["metric_comparisons"]["direct_competitor_count"]["values"]] == [1.0, 3.0]
    assert result["data_mode"] == "SYNTHETIC_DEMO"
    assert result["assistant"]["brief"]["composite_score"] is None
    assert result["assistant"]["brief"]["missing"]
    assert receipt.database_settings == {"memory_limit": "512.0 MiB", "threads": 2, "max_temp_directory_size": "1.0 GiB"}
    with db_session() as conn:
        evidence = query_metric_evidence(conn, site_id=receipt.site_ids[0], catchment="500m", metric_id="direct_competitor_count")
        assert evidence["evidence"][0]["source_url"].startswith("synthetic:")
        report = json.loads(export_comparison_json(conn, receipt.comparison_ids[0]).read_text(encoding="utf8"))
        assert report["assistant"] == result["assistant"]
        assert report["calculation_version_id"] == result["calculation_version_id"]
        assert report["snapshot_set"] == result["snapshot_set"]
    assert not list((home / "data/cache/analysis-workers").glob("owned-*"))


@pytest.mark.parametrize("operation,payload", [
    ("sql", {"sql": "SELECT 1"}),
    ("site", {"address": "Synthetic address", "script": "evil.py"}),
    ("site", {"address": "Synthetic address", "catchments": ["DROP TABLE address"]}),
    ("site", {"address": "x" * 201}),
    ("comparison", {"addresses": [DEMO_ADDRESSES[0]] * 2}),
    ("comparison", {"addresses": DEMO_ADDRESSES, "catchments": ["500m"], "comparison_catchment": "1km"}),
    ("assistant_comparison", {**request(), "confirmed": False}),
])
def test_invalid_requests_never_enter_process_boundary(monkeypatch, operation, payload):
    monkeypatch.setattr(execution, "run_python_worker", lambda *a, **k: pytest.fail("Invalid request launched a process"))
    with pytest.raises(ValueError):
        execution.execute_bounded_analysis(operation, payload)


def test_precancelled_request_creates_no_analysis(home):
    cancel = threading.Event()
    cancel.set()
    result = execution.execute_bounded_analysis("assistant_comparison", request(), cancel=cancel)
    assert result.status == "CANCELLED" and result.process is None
    assert result.database_cleanup_confirmed
    with db_session() as conn:
        assert conn.execute("SELECT count(*) FROM site_query").fetchone()[0] == 0


@pytest.mark.parametrize("reason", ["CANCELLED", "TIMED_OUT"])
def test_actual_query_stop_preserves_completed_child_and_manual_retry(home, monkeypatch, reason):
    monkeypatch.setattr(execution, "WORKER_SCRIPT", Path(__file__).parents[1] / "fixtures/held_analysis_worker.py")
    cancel, outcome, failures = threading.Event(), [], []
    # A deliberately unrelated prior attempt must never be reconciled by address.
    with db_session() as conn:
        conn.execute("INSERT INTO site_query(site_id,original_input,business_type,status,created_at) VALUES ('unrelated', ?, 'full_service_restaurant','RUNNING',now())", [DEMO_ADDRESSES[1]])
    def run():
        try:
            outcome.append(execution.execute_bounded_analysis("assistant_comparison", request(), cancel=cancel,
                process_limits=ProcessLimits(wall_seconds=12)))
        except BaseException as error:
            failures.append(error)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        until = time.monotonic() + 10
        while not (home / "held-worker-ready.json").exists() and time.monotonic() < until and thread.is_alive():
            time.sleep(.02)
        assert (home / "held-worker-ready.json").exists(), failures or outcome
        with pytest.raises(DatabaseBusyError):
            with db_session():
                pytest.fail("A second operation entered while the worker owned the database")
        if reason == "CANCELLED":
            cancel.set()
        thread.join(20)
        assert not thread.is_alive() and not failures
        receipt = outcome[0]
        assert receipt.status == reason and receipt.result is None
        assert receipt.process.process_stopped and receipt.database_cleanup_confirmed
        assert list(receipt.saved_states.values()) == ["SUCCEEDED", reason, reason]
        with db_session() as conn:
            assert conn.execute("SELECT status FROM site_query WHERE site_id='unrelated'").fetchone()[0] == "RUNNING"
            assert json.loads(conn.execute("SELECT site_ids_json FROM site_comparison WHERE comparison_id=?", receipt.comparison_ids).fetchone()[0]) == receipt.site_ids[:1]
        monkeypatch.setattr(execution, "WORKER_SCRIPT", Path(execution.__file__).with_name("analysis_worker.py"))
        retried = execution.execute_bounded_analysis("assistant_comparison", request())
        assert retried.status == "SUCCEEDED"
        assert not set(retried.site_ids) & set(receipt.site_ids)
        assert [row["value"] for row in retried.result["metric_comparisons"]["direct_competitor_count"]["values"]] == [1., 3.]
    finally:
        cancel.set()
        thread.join(20)


def test_private_identity_pool_limits_and_result_size(tmp_path):
    identifier = "site_" + "a" * 32
    with worker_context([identifier], [], DatabaseLimits(memory_mb=128, threads=1, spill_mb=0), tmp_path):
        assert allocate_identity("site") == identifier
        with pytest.raises(RuntimeError, match="IDENTITIES_EXHAUSTED"):
            allocate_identity("site")
        assert database_options()["max_temp_directory_size"] == "0MiB"
        with pytest.raises(ValueError, match="RESULT_SIZE_LIMIT"):
            result_json({"large": "x" * (8 * 1024 * 1024)})
    assert database_options() == {}


def test_lock_released_after_exception_and_nested_owner_allowed(tmp_path):
    path = tmp_path / "admission.duckdb"
    with pytest.raises(ValueError):
        with database_admission(path):
            with database_admission(path):
                raise ValueError("synthetic")
    with database_admission(path):
        pass
