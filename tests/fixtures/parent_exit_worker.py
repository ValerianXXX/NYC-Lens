from pathlib import Path
import os
import sys
import time

directory = Path(sys.argv[1])
(directory / "owned-worker-pid").write_text(str(os.getpid()))
while True:
    (directory / "parent-exit-heartbeat").write_text(str(time.monotonic()))
    time.sleep(0.03)
