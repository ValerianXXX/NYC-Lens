"""Invalid process/environment handoffs cannot enter even a synthetic payload."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from nyc_lens.bounded_process import _worker_interpreter


@pytest.mark.parametrize('fault,expected', [('pid', 72), ('legacy_gate', 72), ('prefix', 73)])
def test_wrong_start_identity_is_rejected_before_payload(tmp_path, fault, expected):
    if os.name != 'nt' and sys.platform != 'linux':
        pytest.skip('No supported bounded-worker platform')
    environment = dict(os.environ)
    command = [_worker_interpreter(environment), '-m', 'nyc_lens.worker_bootstrap',
        '--memory-mb', '128', '--parent-pid', str(os.getpid()),
        '--python-prefix', str(tmp_path / 'wrong-environment') if fault == 'prefix' else sys.prefix,
        '--script', str(Path(__file__).parents[1] / 'fixtures/bounded_worker_fixture.py'), 'success', str(tmp_path)]
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=environment, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        gate = b'RUN\n' if fault == 'legacy_gate' else f'RUN {child.pid + (1 if fault == "pid" else 0)}\n'.encode('ascii')
        child.communicate(gate, timeout=5)
        assert child.returncode == expected
        assert not (tmp_path / 'started').exists() and not (tmp_path / 'result').exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(5)
