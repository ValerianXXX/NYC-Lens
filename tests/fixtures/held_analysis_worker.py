"""Test-only real engine: hold the second site's actual query until stopped."""
import importlib
import json
from pathlib import Path
import sys

request = json.loads(Path(sys.argv[1]).read_text(encoding="utf8"))
analysis = importlib.import_module("nyc_lens.analyze")
original = analysis.calculate_metrics
calls = 0


def held_query(conn, *args, **kwargs):
    global calls
    calls += 1
    if calls == 2:
        (Path(request["home"]) / "held-worker-ready.json").write_text(json.dumps(request), encoding="utf8")
        conn.execute("SELECT sum(sqrt(i)) FROM range(1000000000000) t(i)").fetchone()
    return original(conn, *args, **kwargs)


analysis.calculate_metrics = held_query
from nyc_lens.analysis_worker import main
main()
