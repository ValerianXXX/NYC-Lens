"""Real Windows sharing locks, real DuckDB state; worker outcome is a test double."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import duckdb
import pytest

from nyc_lens import analysis_execution as execution
from nyc_lens.bounded_process import ProcessOutcome


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Actual Windows native file-sharing contract')


@pytest.mark.parametrize('reason,transient', [('CANCELLED', True), ('TIMED_OUT', True), ('CANCELLED', False)])
def test_stopped_attempt_waits_for_transient_sharing_but_preserves_persistent_failure(tmp_path, monkeypatch, reason, transient):
    monkeypatch.setenv('NYC_LENS_HOME', str(tmp_path))
    monkeypatch.delenv('NYC_LENS_DATABASE', raising=False)
    request = {'addresses': ['1 Synthetic Street, New York, NY', '2 Synthetic Street, New York, NY'],
        'catchments': ['500m'], 'comparison_catchment': '500m'}
    attempt = execution.prepare_attempt('comparison', request)
    database = tmp_path / 'data/nyc_lens.duckdb'
    database.parent.mkdir()
    with duckdb.connect(str(database)) as conn:
        conn.execute('''CREATE TABLE site_query(site_id VARCHAR, status VARCHAR, completed_at TIMESTAMP,
            warnings_json JSON, result_json JSON)''')
        conn.execute('''CREATE TABLE site_comparison(comparison_id VARCHAR, status VARCHAR, completed_at TIMESTAMP,
            warnings_json JSON, result_json JSON, site_ids_json JSON)''')
        conn.execute('''INSERT INTO site_query VALUES (?, 'SUCCEEDED', now(), '[]', '{"retained":true}'),
            (?, 'RUNNING', NULL, '[]', NULL), ('unrelated', 'RUNNING', NULL, '[]', NULL)''', attempt.site_ids)
        conn.execute("INSERT INTO site_comparison VALUES (?, 'RUNNING', NULL, '[]', NULL, '[]')", attempt.comparison_ids)
    ready, release = tmp_path / 'handle-ready.json', tmp_path / 'release-handle'
    fixture = Path(__file__).parents[1] / 'fixtures/held_database_file.py'
    holders, timers, calls = [], [], []
    def stopped_worker(*args, **kwargs):
        calls.append(1)
        # The fixture is stdlib-only. Launch the actual interpreter so its
        # retained handle identifies the process which holds the database,
        # rather than Windows' additional virtual-environment redirector.
        child = subprocess.Popen([sys._base_executable, str(fixture), str(database), str(ready), str(release)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        holders.append(child)
        until = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < until:
            assert child.poll() is None, child.stderr.read().decode(errors='replace')
            time.sleep(.01)
        assert ready.exists(), 'Synthetic holder did not acquire the database'
        assert json.loads(ready.read_text(encoding='utf-8'))['pid'] == child.pid
        if transient:
            timer = threading.Timer(.30, release.touch)
            timers.append(timer)
            timer.start()
        return ProcessOutcome(reason, 1, None, True, 'SYNTHETIC_STOP_RECEIPT', 0)
    monkeypatch.setattr(execution, 'run_python_worker', stopped_worker)
    started = time.monotonic()
    try:
        receipt = execution.execute_bounded_analysis('comparison', request, attempt=attempt)
        elapsed = time.monotonic() - started
    finally:
        release.touch()
        for timer in timers:
            timer.cancel()
            timer.join(2)
        for child in holders:
            try:
                child.wait(5)
                assert child.returncode == 0, child.stderr.read().decode(errors='replace')
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(5)
                child.stderr.close()
    assert calls == [1], 'Recovery must not rerun analysis'
    assert receipt.status == reason and receipt.result is None
    assert receipt.process.process_stopped and elapsed < 4
    if transient:
        assert receipt.database_cleanup_confirmed, receipt
        assert list(receipt.saved_states.values()) == ['SUCCEEDED', reason, reason]
    else:
        assert not receipt.database_cleanup_confirmed
        assert receipt.error_code == 'DATABASE_RECONCILIATION_UNAVAILABLE'
    with duckdb.connect(str(database)) as conn:
        assert conn.execute('SELECT status,result_json FROM site_query WHERE site_id=?', attempt.site_ids[:1]).fetchone() == ('SUCCEEDED', '{"retained":true}')
        assert conn.execute("SELECT status FROM site_query WHERE site_id='unrelated'").fetchone() == ('RUNNING',)
        assert conn.execute('SELECT status FROM site_query WHERE site_id=?', attempt.site_ids[1:]).fetchone() == (reason if transient else 'RUNNING',)
        comparison = conn.execute('SELECT status,site_ids_json FROM site_comparison').fetchone()
        assert comparison[0] == (reason if transient else 'RUNNING')
        assert json.loads(comparison[1]) == (attempt.site_ids[:1] if transient else [])
