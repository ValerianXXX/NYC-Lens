"""Synthetic process fixture. Executed only under an installed resource bound."""
from pathlib import Path
import subprocess
import sys
import time

mode, directory = sys.argv[1], Path(sys.argv[2])
directory.mkdir(exist_ok=True)
(directory / "started").write_text("synthetic worker entered")
if mode == "success":
    (directory / "result").write_text("complete")
elif mode == "failure":
    print("synthetic-private-output", file=sys.stderr)
    raise SystemExit(7)
elif mode == "memory":
    try:
        value = bytearray(512 * 1024 * 1024)
        (directory / "unbounded").write_text(str(len(value)))
    except MemoryError:
        (directory / "memory-limited").write_text("allocation refused")
elif mode == "flood":
    for _ in range(200):
        print("x" * 8192)
        print("y" * 8192, file=sys.stderr)
    (directory / "result").write_text("complete")
elif mode in ("wait", "descendant"):
    if mode == "descendant":
        subprocess.Popen([sys.executable, "-c",
            "from pathlib import Path; import sys,time; p=Path(sys.argv[1]); "
            "exec('while True:\\n p.write_text(str(time.monotonic()))\\n time.sleep(0.03)')",
            str(directory / "descendant-heartbeat")])
    while True:
        (directory / "heartbeat").write_text(str(time.monotonic()))
        time.sleep(0.03)
else:
    raise SystemExit(8)
