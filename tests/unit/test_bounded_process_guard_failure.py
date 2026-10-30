import os
from pathlib import Path
import subprocess
import sys
import time

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


@pytest.mark.skipif(os.name != "nt", reason="Native Windows Job Object kill-on-close")
def test_parent_exit_signals_the_actual_owned_worker_process(tmp_path):
    import ctypes
    from ctypes import wintypes
    native = ctypes.WinDLL("kernel32", use_last_error=True)
    native.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    native.OpenProcess.restype = wintypes.HANDLE
    native.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    native.WaitForSingleObject.restype = wintypes.DWORD
    native.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    native.TerminateProcess.restype = wintypes.BOOL
    native.CloseHandle.argtypes = [wintypes.HANDLE]
    native.CloseHandle.restype = wintypes.BOOL
    parent = subprocess.Popen([sys.executable, str(Path(__file__).parents[1] / "fixtures/parent_exit_supervisor.py"), str(tmp_path)],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    handle = None
    try:
        pid_file = tmp_path / "owned-worker-pid"
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pid_file.exists()
        handle = native.OpenProcess(0x100000 | 0x1, False, int(pid_file.read_text()))
        assert handle
        assert native.WaitForSingleObject(handle, 0) == 258
        parent.kill()
        parent.wait(timeout=5)
        assert native.WaitForSingleObject(handle, 5000) == 0
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if handle:
            if native.WaitForSingleObject(handle, 0) != 0:
                native.TerminateProcess(handle, 1)
                native.WaitForSingleObject(handle, 5000)
            native.CloseHandle(handle)
