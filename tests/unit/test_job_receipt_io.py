"""Receipt reads/publication must survive bounded file-sharing contention."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import pytest

from nyc_lens.analysis_jobs import AnalysisJobs, JobUnavailableError
from nyc_lens.paths import DEFAULT_HOME


@pytest.fixture
def stored(tmp_path, monkeypatch):
    monkeypatch.setenv('NYC_LENS_HOME', str(tmp_path))
    monkeypatch.delenv('NYC_LENS_DATABASE', raising=False)
    jobs = AnalysisJobs()
    receipt = {'schema':1,'job_id':'analysis_'+'a'*32,'operation':'site','status':'FAILED',
               'site_ids':['site_'+'b'*32],'comparison_ids':[], 'serial':0}
    jobs._write(receipt, create=True)
    return jobs, receipt, jobs._path(receipt['job_id'])


def test_transient_read_sharing_denial_returns_the_original_receipt(stored, monkeypatch):
    jobs, receipt, path = stored
    original, attempts = Path.open, []
    def temporarily_denied(self, mode='r', *args, **kwargs):
        if self == path and mode == 'rb':
            attempts.append(1)
            if len(attempts) == 1:
                raise PermissionError('synthetic private path must not escape')
        return original(self, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', temporarily_denied)
    assert jobs.get(receipt['job_id']) == receipt
    assert len(attempts) >= 2


def test_persistent_read_denial_stays_unavailable_and_does_not_relabel_state(stored, monkeypatch):
    jobs, receipt, path = stored
    original = Path.open
    before = path.read_bytes()
    def denied(self, mode='r', *args, **kwargs):
        if self == path and mode == 'rb':
            raise PermissionError('synthetic private path must not escape')
        return original(self, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', denied)
    started = time.monotonic()
    with pytest.raises(JobUnavailableError, match='^JOB_RECEIPT_UNAVAILABLE$'):
        jobs.get(receipt['job_id'])
    assert time.monotonic() - started < 3
    monkeypatch.setattr(Path, 'open', original)
    assert path.read_bytes() == before


def test_transient_replace_denial_publishes_once_without_changing_identity(stored, monkeypatch):
    jobs, receipt, path = stored
    original, attempts = os.replace, []
    def temporarily_denied(source, destination):
        if Path(destination) == path:
            attempts.append(1)
            if len(attempts) == 1:
                raise PermissionError('synthetic replacement sharing denial')
        return original(source, destination)
    monkeypatch.setattr(os, 'replace', temporarily_denied)
    updated = {**receipt, 'serial':1}
    jobs._write(updated)
    assert jobs.get(receipt['job_id']) == updated and len(attempts) >= 2


def test_persistent_replace_denial_preserves_prior_complete_receipt(stored, monkeypatch):
    jobs, receipt, path = stored
    before = path.read_bytes()
    def denied(*args):
        raise PermissionError('synthetic private replacement detail')
    monkeypatch.setattr(os, 'replace', denied)
    started = time.monotonic()
    with pytest.raises(JobUnavailableError, match='^JOB_RECEIPT_UNAVAILABLE$'):
        jobs._write({**receipt,'serial':1})
    assert time.monotonic() - started < 3 and path.read_bytes() == before
    assert jobs.get(receipt['job_id']) == receipt


def test_receipt_publication_waits_until_cooperating_reader_closes_handle(stored, monkeypatch):
    jobs, receipt, path = stored
    original = Path.open
    opened, release, writing = threading.Event(), threading.Event(), threading.Event()
    class HeldReader:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def read(self, size):
            opened.set()
            assert release.wait(2), 'Fixture reader was not released'
            return self.stream.read(size)
        def __exit__(self, *args): self.stream.close()
    def held(self, mode='r', *args, **kwargs):
        stream = original(self, mode, *args, **kwargs)
        return HeldReader(stream) if self == path and mode == 'rb' else stream
    monkeypatch.setattr(Path, 'open', held)
    updated = {**receipt,'serial':1}
    def publish():
        writing.set()
        jobs._write(updated)
    with ThreadPoolExecutor(max_workers=2) as executor:
        reader = executor.submit(jobs.get, receipt['job_id'])
        try:
            assert opened.wait(2)
            writer = executor.submit(publish)
            assert writing.wait(2)
            with pytest.raises(FutureTimeout):
                writer.result(timeout=.1)
        finally:
            release.set()
        assert reader.result(timeout=2) == receipt
        writer.result(timeout=2)
    monkeypatch.setattr(Path, 'open', original)
    assert jobs.get(receipt['job_id']) == updated


def test_separate_process_read_and_publication_share_the_same_lock(stored):
    jobs, receipt, path = stored
    home = jobs.home
    shutil.copytree(DEFAULT_HOME/'config', home/'config')
    (home/'receipt-fixture-marker').write_text('SYNTHETIC_RECEIPT_IO')
    child = subprocess.Popen([sys.executable,str(Path(__file__).parents[1]/'fixtures/held_receipt_reader.py')],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0)
    try:
        deadline = time.monotonic()+8
        while not (home/'receipt-reader-ready').exists() and child.poll() is None and time.monotonic()<deadline:
            time.sleep(.01)
        if not (home/'receipt-reader-ready').exists() and child.poll() is not None:
            _, details = child.communicate(timeout=2)
            pytest.fail('Synthetic reader failed before opening its receipt: ' + details)
        assert (home/'receipt-reader-ready').exists()
        updated = {**receipt,'serial':1}
        with ThreadPoolExecutor(max_workers=1) as executor:
            writer = executor.submit(jobs._write, updated)
            try:
                with pytest.raises(FutureTimeout):
                    writer.result(timeout=.1)
            finally:
                (home/'receipt-reader-release').write_text('release owned reader')
            writer.result(timeout=3)
        stdout, stderr = child.communicate(timeout=5)
        assert child.returncode == 0, stderr
        assert json.loads(stdout) == receipt
        assert jobs.get(receipt['job_id']) == updated
    finally:
        (home/'receipt-reader-release').write_text('cleanup')
        if child.poll() is None:
            try: child.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill(); child.communicate(timeout=3)
