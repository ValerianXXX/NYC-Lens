"""Behavioral contracts for stopping actual owned processes, not a fake timer."""
from importlib import import_module
from pathlib import Path
import threading
import time

import pytest

FIXTURE = Path(__file__).parents[1] / "fixtures/bounded_worker_fixture.py"


def api():
    return import_module("nyc_lens.bounded_process")


def run(directory, mode, *, wall=5, memory=128, cancel=None):
    module = api()
    return module.run_python_worker(FIXTURE, [mode, str(directory)],
        limits=module.ProcessLimits(wall_seconds=wall, memory_mb=memory), cancel=cancel)


def test_success_has_real_exit_and_resource_boundary(tmp_path):
    result = run(tmp_path, "success")
    assert result.reason == "EXITED" and result.returncode == 0
    assert result.process_stopped is True
    assert result.memory_boundary in {"WINDOWS_JOB_COMMITTED_MEMORY", "POSIX_ADDRESS_SPACE"}
    assert (tmp_path / "result").read_text() == "complete"


def test_deadline_stops_actual_work_before_returning(tmp_path):
    result = run(tmp_path, "wait", wall=0.7)
    assert result.reason == "TIMED_OUT" and result.process_stopped
    assert (tmp_path / "started").exists()
    before = (tmp_path / "heartbeat").read_bytes()
    time.sleep(0.15)
    assert (tmp_path / "heartbeat").read_bytes() == before
    assert result.elapsed_seconds < 5


def test_precancelled_work_never_starts(tmp_path):
    cancel = threading.Event()
    cancel.set()
    result = run(tmp_path, "success", cancel=cancel)
    assert result.reason == "CANCELLED" and result.process_stopped
    assert result.pid is None
    assert not (tmp_path / "started").exists()


def test_cancellation_waits_for_real_exit(tmp_path):
    cancel = threading.Event()
    results = []
    thread = threading.Thread(target=lambda: results.append(run(tmp_path, "wait", cancel=cancel)))
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "heartbeat").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (tmp_path / "heartbeat").exists()
        cancel.set()
        thread.join(5)
        assert not thread.is_alive()
        assert results[0].reason == "CANCELLED" and results[0].process_stopped
        before = (tmp_path / "heartbeat").read_bytes()
        time.sleep(0.15)
        assert (tmp_path / "heartbeat").read_bytes() == before
    finally:
        cancel.set()
        thread.join(10)


def test_deadline_stops_descendant_work_too(tmp_path):
    result = run(tmp_path, "descendant", wall=1.2)
    assert result.reason == "TIMED_OUT" and result.process_stopped
    assert (tmp_path / "descendant-heartbeat").exists()
    before = (tmp_path / "descendant-heartbeat").read_bytes()
    time.sleep(0.15)
    assert (tmp_path / "descendant-heartbeat").read_bytes() == before


def test_actual_memory_allocation_is_refused_without_host_pressure(tmp_path):
    result = run(tmp_path, "memory", memory=96)
    assert result.reason == "EXITED" and result.returncode == 0
    assert (tmp_path / "memory-limited").exists()
    assert not (tmp_path / "unbounded").exists()


def test_output_flood_cannot_fill_parent_pipes_or_receipt(tmp_path):
    result = run(tmp_path, "flood")
    assert result.returncode == 0 and (tmp_path / "result").exists()
    assert len(str(result)) < 1000


def test_failure_preserves_exit_code_without_capturing_private_output(tmp_path):
    result = run(tmp_path, "failure")
    assert result.reason == "EXITED" and result.returncode == 7 and result.process_stopped
    assert "synthetic-private-output" not in str(result)


@pytest.mark.parametrize("kwargs", [{"wall_seconds": 0}, {"wall_seconds": float("inf")},
    {"wall_seconds": True}, {"memory_mb": 32}, {"memory_mb": True}, {"memory_mb": 128.5}])
def test_invalid_limits_are_refused_before_launch(kwargs):
    with pytest.raises(ValueError):
        api().ProcessLimits(**kwargs)
