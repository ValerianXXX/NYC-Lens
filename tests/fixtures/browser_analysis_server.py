"""Explicit isolated browser-test server, optionally holding a real SQL query."""
import argparse
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--home", type=Path, required=True)
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--held", action="store_true")
parser.add_argument("--split-source", action="store_true", help="Synthetic multi-snapshot provenance exercise")
parser.add_argument("--census-inputs", action="store_true", help="Synthetic complete/incomplete Census inputs")
args = parser.parse_args()
if args.home.exists() or os.getenv("NYC_LENS_HOME") or os.getenv("NYC_LENS_DATABASE"):
    raise SystemExit("Only a new isolated synthetic home is permitted")
from nyc_lens.demo import seed_demo
seed_demo(args.home.resolve())
os.environ["NYC_LENS_AI_PROVIDER"] = "disabled"
if args.census_inputs:
    from census_scenario import seed_census_scenario
    from nyc_lens.db import db_session
    with db_session() as conn:
        seed_census_scenario(conn)
if args.split_source:
    from nyc_lens.db import db_session
    with db_session() as conn:
        conn.execute("""INSERT INTO dataset_snapshot(snapshot_id,source_id,run_id,vintage,retrieved_at,source_url,license,status)
            VALUES ('synthetic-part-2','overture_places','synthetic-run','SYNTHETIC part 2',now(),
                    'synthetic://lineage/part-2','Original synthetic fixture','SUCCEEDED')""")
        conn.execute("UPDATE poi SET snapshot_id='synthetic-part-2' WHERE poi_id='synthetic-poi-1-0'")
        conn.execute("UPDATE standardized_record SET snapshot_id='synthetic-part-2' WHERE entity_id='synthetic-poi-1-0'")
        conn.execute("""INSERT INTO standardized_record VALUES ('synthetic-retained-old','poi',
            'synthetic-poi-1-0','overture_places','synthetic-places-v1','old-copy','synthetic-v0',now())""")
        conn.execute("UPDATE ingestion_run SET completed_at=now() WHERE run_id='synthetic-run'")
if args.held:
    from nyc_lens import analysis_execution
    analysis_execution.WORKER_SCRIPT = Path(__file__).with_name("held_analysis_worker.py")
import uvicorn
uvicorn.run("nyc_lens.api.app:app", host="127.0.0.1", port=args.port)
