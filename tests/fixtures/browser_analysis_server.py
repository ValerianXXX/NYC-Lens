"""Explicit isolated browser-test server, optionally holding a real SQL query."""
import argparse
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--home", type=Path, required=True)
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--held", action="store_true")
args = parser.parse_args()
if args.home.exists() or os.getenv("NYC_LENS_HOME") or os.getenv("NYC_LENS_DATABASE"):
    raise SystemExit("Only a new isolated synthetic home is permitted")
from nyc_lens.demo import seed_demo
seed_demo(args.home.resolve())
os.environ["NYC_LENS_AI_PROVIDER"] = "disabled"
if args.held:
    from nyc_lens import analysis_execution
    analysis_execution.WORKER_SCRIPT = Path(__file__).with_name("held_analysis_worker.py")
import uvicorn
uvicorn.run("nyc_lens.api.app:app", host="127.0.0.1", port=args.port)
