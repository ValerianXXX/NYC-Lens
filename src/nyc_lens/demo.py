"""Disposable synthetic data with the real DuckDB/spatial/comparison/report engine."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import tempfile

import duckdb

from .db import SCHEMA_PATH, initialize_database, db_session
from .paths import DEFAULT_HOME

DEMO_ADDRESSES = ["100 Demo Avenue, Newark, NJ", "200 Demo Avenue, Newark, NJ"]


def seed_demo(home: Path) -> None:
    """Only a new database in an empty dedicated home; never modifies an existing DB."""
    database = home / "data" / "nyc_lens.duckdb"
    if database.exists() or os.getenv("NYC_LENS_DATABASE"):
        raise ValueError("Synthetic demo requires a new isolated database")
    home.mkdir(parents=True, exist_ok=True)
    for directory in ("config", "ui"):
        shutil.copytree(DEFAULT_HOME / directory, home / directory)
    database.parent.mkdir(exist_ok=True)
    os.environ["NYC_LENS_HOME"] = str(home)
    with duckdb.connect(str(database)) as conn:
        # Tiny materialized tables implement the same schema as production Parquet views.
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("CREATE TABLE synthetic_demo_guard(label VARCHAR)")
        conn.execute("INSERT INTO synthetic_demo_guard VALUES ('SYNTHETIC_DEMO')")
    initialize_database()
    with db_session() as conn:
        conn.execute("""INSERT INTO ingestion_run(run_id, source_id, command, started_at, completed_at, status)
            VALUES ('synthetic-run', 'overture_places', 'synthetic fixture', now(), now(), 'SUCCEEDED')""")
        conn.execute("""INSERT INTO dataset_snapshot(snapshot_id, source_id, run_id, vintage, retrieved_at, source_url, license, status, warnings_json)
            VALUES ('synthetic-places-v1', 'overture_places', 'synthetic-run', 'SYNTHETIC 2026', now(),
            'synthetic://nyc-lens/demo-v1', 'Original synthetic fixture; no actual businesses', 'SUCCEEDED', '["SYNTHETIC_DEMO"]')""")
        conn.execute("UPDATE ingestion_run SET completed_at=now() WHERE run_id='synthetic-run'")
        for index, (address, longitude) in enumerate(zip(DEMO_ADDRESSES, [-74.170, -74.150])):
            conn.execute("""INSERT INTO address(address_id, original_input, standardized_address, state_fips, state_abbr,
                county_geoid, county_name, region_code, longitude, latitude, geocoder, match_status, confidence,
                source_id, snapshot_id, retrieved_at, method_version)
                VALUES (?, ?, ?, '34', 'NJ', '34013', 'Essex', 'CSA408', ?, 40.735, 'synthetic_demo', 'MATCH', NULL,
                'synthetic_demo', 'synthetic-places-v1', now(), 'synthetic-v1')""",
                [f"synthetic-address-{index}", address, address, longitude])
            for offset in range(index * 2 + 1):
                identifier = f"synthetic-poi-{index}-{offset}"
                conn.execute("""INSERT INTO poi(poi_id, source_poi_id, name, basic_category, longitude, latitude,
                    status, source_id, snapshot_id, retrieved_at, measure_type, method_version)
                    VALUES (?, ?, ?, 'restaurant', ?, ?, 'open', 'overture_places', 'synthetic-places-v1', now(), 'synthetic_record', 'synthetic-v1')""",
                    [identifier, identifier, f"Fictional restaurant {index}-{offset}", longitude + offset * 0.0001, 40.735])
                conn.execute("INSERT INTO standardized_record VALUES (?, 'poi', ?, 'overture_places', 'synthetic-places-v1', ?, 'synthetic-v1', now())",
                             [identifier, identifier, identifier])


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a disposable synthetic NYC Lens demo")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if os.getenv("NYC_LENS_HOME") or os.getenv("NYC_LENS_DATABASE"):
        raise SystemExit("Unset NYC_LENS_HOME and NYC_LENS_DATABASE before starting the isolated demo")
    with tempfile.TemporaryDirectory(prefix="nyc-lens-synthetic-") as directory:
        seed_demo(Path(directory))
        import uvicorn
        from .api.app import app
        print(f"SYNTHETIC DEMO ONLY: http://127.0.0.1:{args.port}", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
