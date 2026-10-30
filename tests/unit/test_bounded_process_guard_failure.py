import os
from pathlib import Path
import subprocess

import pytest

from nyc_lens import bounded_process


@pytest.mark.skipif(os.name != "nt", reason="Native Windows Job Object assignment")
def test_failed_native_assignment_stops_the_unassigned_process_before_task_entry(tmp_path, monkeypatch):
    original_popen = subprocess.Popen
    processes = []
    failure = OSError("synthetic guard assignment failure")
    def capture(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        processes.append(child)
        return child
    def fail_assignment(self, pid):
        raise failure
    monkeypatch.setattr(bounded_process.subprocess, "Popen", capture)
    monkeypatch.setattr(bounded_process.WindowsJob, "assign", fail_assignment)
    try:
        with pytest.raises(OSError) as raised:
            bounded_process.run_python_worker(Path(__file__).parents[1] / "fixtures/bounded_worker_fixture.py",
                ["success", str(tmp_path)], limits=bounded_process.ProcessLimits(wall_seconds=5, memory_mb=128))
        assert raised.value is failure
        assert len(processes) == 1 and processes[0].poll() is not None
        assert not (tmp_path / "started").exists()
    finally:
        # A failing baseline may leave only this known fixture waiting at its
        # start gate. Never scan or terminate any other process.
        for child in processes:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
