"""Hold a real receipt read in a separate synthetic-only process."""
import json
import os
from pathlib import Path
import time

from nyc_lens.analysis_jobs import AnalysisJobs

home = Path(os.environ['NYC_LENS_HOME'])
assert (home/'receipt-fixture-marker').read_text() == 'SYNTHETIC_RECEIPT_IO'
jobs = AnalysisJobs()
identifier = 'analysis_' + 'a' * 32
target = jobs._path(identifier)
original = Path.open


class Held:
    def __init__(self, stream): self.stream = stream
    def __enter__(self): return self
    def read(self, limit):
        (home/'receipt-reader-ready').write_text('held')
        deadline = time.monotonic() + 4
        while not (home/'receipt-reader-release').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        return self.stream.read(limit)
    def __exit__(self, *args): self.stream.close()


def opened(self, mode='r', *args, **kwargs):
    stream = original(self, mode, *args, **kwargs)
    return Held(stream) if self == target and mode == 'rb' else stream


Path.open = opened
print(json.dumps(jobs.get(identifier)))
