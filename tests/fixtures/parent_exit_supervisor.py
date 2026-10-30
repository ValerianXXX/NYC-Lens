from pathlib import Path
import sys
from nyc_lens.bounded_process import ProcessLimits, run_python_worker

run_python_worker(Path(__file__).with_name("parent_exit_worker.py"), [sys.argv[1]],
                  limits=ProcessLimits(wall_seconds=30, memory_mb=128))
