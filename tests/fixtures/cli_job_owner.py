"""Test-only CLI process over an already isolated synthetic home."""
from pathlib import Path
import os
import sys

import duckdb

home = Path(os.environ['NYC_LENS_HOME']).resolve(strict=True)
with duckdb.connect(str(home / 'data/nyc_lens.duckdb'), read_only=True) as conn:
    assert conn.execute('SELECT label FROM synthetic_demo_guard').fetchone() == ('SYNTHETIC_DEMO',)

from nyc_lens import analysis_execution
from nyc_lens.cli import app
from nyc_lens.demo import DEMO_ADDRESSES

analysis_execution.WORKER_SCRIPT = Path(__file__).with_name('held_analysis_worker.py')
app(args=['compare', '--address', DEMO_ADDRESSES[0], '--address', DEMO_ADDRESSES[1],
    '--catchments', '500m', '--no-export', '--request-id', sys.argv[1]])
