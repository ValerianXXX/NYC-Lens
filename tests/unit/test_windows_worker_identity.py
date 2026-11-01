"""The actual venv interpreter must be owned even if job assignment is delayed."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from nyc_lens import bounded_process


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows interpreter/Job Object identity')
def test_actual_interpreter_is_owned_before_payload_even_with_delayed_assignment(tmp_path, monkeypatch):
    native = ctypes.WinDLL('kernel32', use_last_error=True)
    native.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    native.OpenProcess.restype = wintypes.HANDLE
    native.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    native.IsProcessInJob.restype = wintypes.BOOL
    native.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    native.WaitForSingleObject.restype = wintypes.DWORD
    native.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    native.TerminateProcess.restype = wintypes.BOOL
    native.CloseHandle.argtypes = [wintypes.HANDLE]
    native.CloseHandle.restype = wintypes.BOOL
    assigned, outcomes, errors = [], [], []
    original = bounded_process.WindowsJob.assign
    def delayed(self, pid):
        # Allow the venv redirector time to create its real interpreter before
        # assignment. The actual payload must remain behind its start gate.
        time.sleep(.30)
        original(self, pid)
        assigned.append((self.handle, pid))
    monkeypatch.setattr(bounded_process.WindowsJob, 'assign', delayed)
    cancel = threading.Event()
    def run():
        try:
            outcomes.append(bounded_process.run_python_worker(Path(__file__).parents[1] / 'fixtures/worker_identity.py',
                [str(tmp_path)], limits=bounded_process.ProcessLimits(wall_seconds=8, memory_mb=128), cancel=cancel))
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=run)
    handle = None
    thread.start()
    try:
        ready = tmp_path / 'engine-ready.json'
        until = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < until and thread.is_alive():
            time.sleep(.01)
        assert ready.exists(), errors
        value = json.loads(ready.read_text(encoding='utf-8'))
        assert len(assigned) == 1
        handle = native.OpenProcess(0x1000 | 0x100000 | 0x1, False, value['pid'])
        assert handle
        member = wintypes.BOOL()
        assert native.IsProcessInJob(handle, assigned[0][0], ctypes.byref(member))
        observation = {'spawnedPid': assigned[0][1], 'actualInterpreter': value, 'inOwnedJob': bool(member.value)}
        (tmp_path / 'worker-ownership-observation.json').write_text(json.dumps(observation), encoding='utf-8')
        assert Path(value['prefix']) == Path(sys.prefix)
        assert Path(value['base_prefix']) == Path(sys.base_prefix)
        assert value['pid'] == assigned[0][1] and member.value, observation
        cancel.set()
        thread.join(5)
        assert not thread.is_alive() and not errors
        assert outcomes[0].process_stopped and outcomes[0].pid == value['pid']
        assert native.WaitForSingleObject(handle, 0) == 0
    finally:
        cancel.set()
        thread.join(10)
        if handle:
            # Only the exact process from this fixture's retained handle.
            try:
                if native.WaitForSingleObject(handle, 0) != 0:
                    # The launcher may already be terminating its interpreter.
                    # Observe actual exit even if that racing terminate fails.
                    native.TerminateProcess(handle, 1)
                    assert native.WaitForSingleObject(handle, 5000) == 0
            finally:
                native.CloseHandle(handle)
