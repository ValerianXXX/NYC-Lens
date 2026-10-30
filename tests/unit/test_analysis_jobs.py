"""Actual HTTP jobs, crash recovery and MCP cancellation over synthetic data."""
import asyncio
from importlib import import_module
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from nyc_lens import analysis_execution
from nyc_lens.analysis_jobs import AnalysisJobs, TERMINAL, stop_active_jobs
from nyc_lens.db import db_session
from nyc_lens.demo import DEMO_ADDRESSES, seed_demo


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("NYC_LENS_HOME", str(tmp_path))
    monkeypatch.delenv("NYC_LENS_DATABASE", raising=False)
    monkeypatch.setenv("NYC_LENS_AI_PROVIDER", "disabled")
    seed_demo(tmp_path)
    yield tmp_path
    stop_active_jobs()


def payload():
    return {"operation": "assistant_comparison", "request_id": uuid4().hex,
        "request": {"confirmed": True, "intent": {"addresses": DEMO_ADDRESSES,
            "business_type": "full_service_restaurant", "catchment": "500m", "priorities": ["competition", "rent"]}}}


def until_ready(home):
    until = time.monotonic() + 15
    while not (home / "held-worker-ready.json").exists() and time.monotonic() < until:
        time.sleep(.02)
    assert (home / "held-worker-ready.json").exists()


def finished(jobs, identifier):
    until = time.monotonic() + 15
    while time.monotonic() < until:
        receipt = jobs.get(identifier)
        if receipt["status"] in TERMINAL:
            return receipt
        time.sleep(.02)
    pytest.fail("Job did not reach a terminal state")


def test_http_job_refresh_duplicate_and_report(home):
    client = TestClient(import_module("nyc_lens.api.app").app)
    request = payload()
    response = client.post("/api/analysis-jobs", json=request)
    assert response.status_code == 202, response.text
    identifier = response.json()["job_id"]
    duplicate = client.post("/api/analysis-jobs", json=request)
    assert duplicate.status_code == 202 and duplicate.json()["job_id"] == identifier
    changed = {**request, "request": {**request["request"], "original_request": "Different intent"}}
    assert client.post("/api/analysis-jobs", json=changed).status_code == 422
    receipt = finished(AnalysisJobs(), identifier)
    assert receipt["status"] == "SUCCEEDED" and receipt["process"]["process_stopped"]
    restored = TestClient(import_module("nyc_lens.api.app").app)
    assert restored.get(f"/api/analysis-jobs/{identifier}").json()["result_id"] == receipt["result_id"]
    result = restored.get(f"/api/analysis-jobs/{identifier}/result").json()
    assert [row["value"] for row in result["metric_comparisons"]["direct_competitor_count"]["values"]] == [1., 3.]
    report = restored.get(f"/api/comparison/{receipt['result_id']}/report?format=json")
    assert report.status_code == 200 and report.json()["assistant"] == result["assistant"]
    assert restored.post(f"/api/analysis-jobs/{identifier}/cancel").json()["status"] == "SUCCEEDED"
    with db_session() as conn:
        assert conn.execute("SELECT count(*) FROM site_comparison").fetchone()[0] == 1
    raw = AnalysisJobs()._path(identifier).read_text()
    assert DEMO_ADDRESSES[0] not in raw and "Different intent" not in raw


def test_http_actual_cancel_and_busy_are_distinct_from_success(home, monkeypatch):
    monkeypatch.setattr(analysis_execution, "WORKER_SCRIPT", Path(__file__).parents[1] / "fixtures/held_analysis_worker.py")
    client = TestClient(import_module("nyc_lens.api.app").app)
    response = client.post("/api/analysis-jobs", json=payload())
    assert response.status_code == 202, response.text
    identifier = response.json()["job_id"]
    until_ready(home)
    busy = client.post("/api/analysis-jobs", json=payload())
    assert busy.status_code == 503 and busy.json()["detail"]["started"] is False
    assert client.get("/api/scoring/status").status_code == 503
    cancellation = client.post(f"/api/analysis-jobs/{identifier}/cancel")
    assert cancellation.status_code == 200 and cancellation.json()["status"] in {"CANCELLING", "CANCELLED"}
    receipt = finished(AnalysisJobs(), identifier)
    assert receipt["status"] == "CANCELLED" and receipt["process"]["process_stopped"]
    assert list(receipt["saved_states"].values()) == ["SUCCEEDED", "CANCELLED", "CANCELLED"]
    assert client.get(f"/api/analysis-jobs/{identifier}/result").status_code == 503


def test_oversize_body_never_creates_job(home):
    client = TestClient(import_module("nyc_lens.api.app").app)
    response = client.post("/api/analysis-jobs", content=b" " * 262_145, headers={"Content-Type": "application/json"})
    assert response.status_code == 413 and response.json()["detail"]["started"] is False
    assert not list(AnalysisJobs().directory.glob("analysis_*.json"))


def test_actual_owner_crash_reconciles_own_ids_without_reexecution(home):
    request_id = uuid4().hex
    fixture = Path(__file__).parents[1] / "fixtures/analysis_job_owner.py"
    child = subprocess.Popen([sys.executable, str(fixture), request_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        until_ready(home)
        child.kill()
        child.wait(5)
        receipt = finished(AnalysisJobs(), "analysis_" + request_id)
        assert receipt["status"] == "INTERRUPTED" and receipt["process"] is None
        assert receipt["database_cleanup_confirmed"]
        assert list(receipt["saved_states"].values()) == ["SUCCEEDED", "INTERRUPTED", "INTERRUPTED"]
        with db_session() as conn:
            assert conn.execute("SELECT count(*) FROM site_query").fetchone()[0] == 2
            assert conn.execute("SELECT count(*) FROM site_comparison").fetchone()[0] == 1
        assert finished(AnalysisJobs(), receipt["job_id"]) == receipt
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(5)


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_actual_mcp_client_cancellation_stops_worker(home, monkeypatch, mode):
    mcp = pytest.importorskip("mcp")
    from nyc_lens.mcp_server import server
    monkeypatch.setattr(analysis_execution, "WORKER_SCRIPT", Path(__file__).parents[1] / "fixtures/held_analysis_worker.py")
    async def scenario():
        async with mcp.Client(server, mode=mode) as client:
            task = asyncio.create_task(client.call_tool("compare_addresses", {"addresses": DEMO_ADDRESSES, "confirmed": True}))
            try:
                until = time.monotonic() + 15
                while not (home / "held-worker-ready.json").exists() and time.monotonic() < until:
                    await asyncio.sleep(.02)
                assert (home / "held-worker-ready.json").exists()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                if not task.done():
                    task.cancel()
    asyncio.run(scenario())
    paths = list(AnalysisJobs().directory.glob("analysis_*.json"))
    assert len(paths) == 1
    receipt = finished(AnalysisJobs(), paths[0].stem)
    assert receipt["status"] == "CANCELLED" and receipt["process"]["process_stopped"]
    assert receipt["database_cleanup_confirmed"]
