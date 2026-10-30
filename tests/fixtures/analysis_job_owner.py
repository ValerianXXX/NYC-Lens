"""Owned crash fixture; never run against a real data home."""
import json
from pathlib import Path
import sys
import time
from nyc_lens import analysis_execution
from nyc_lens.analysis_jobs import AnalysisJobs
from nyc_lens.demo import DEMO_ADDRESSES

analysis_execution.WORKER_SCRIPT = Path(__file__).with_name("held_analysis_worker.py")
jobs = AnalysisJobs()
receipt = jobs.start("assistant_comparison", {"confirmed": True, "intent": {
    "addresses": DEMO_ADDRESSES, "business_type": "full_service_restaurant", "catchment": "500m"}}, request_id=sys.argv[1])
(jobs.home / "owner-ready.json").write_text(json.dumps(receipt), encoding="utf8")
while True:
    time.sleep(.1)
