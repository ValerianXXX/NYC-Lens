"""Opt-in two-request public API check in a new isolated home; no fact import."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import duckdb

from nyc_lens.config import get_source
from nyc_lens.db import SCHEMA_PATH
from nyc_lens.ingest.socrata import download_socrata_csv
from nyc_lens.util import sha256_file, utc_iso


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--online", action="store_true", help="Allow two public NYC Open Data calls")
    parser.add_argument("--output", type=Path, required=True, help="New isolated output directory")
    args = parser.parse_args()
    if not args.online:
        parser.error("--online is required; this probe is never run by the test suite")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(ROOT / "config", output / "config")
    os.environ["NYC_LENS_HOME"] = str(output)
    os.environ["NYC_LENS_DATABASE"] = str(output / "probe.duckdb")
    source_id = "dohmh_restaurants"
    source_url = get_source(source_id)["api_url"]
    if source_url != "https://data.cityofnewyork.us/resource/43nn-pn8j.csv":
        raise ValueError("PROBE_ENDPOINT_CHANGED: review the configured public endpoint")
    receipt = {
        "observed_at": utc_iso(), "source_url": source_url, "cases": [],
        "requested_application_calls": 2, "model_calls": 0, "facts_imported": 0,
        "full_upstream_snapshot_verified": False,
        "business_conclusions": False,
        "scope": "Live bounded-response contract only; current public data is not synthetic",
    }
    with duckdb.connect(str(output / "probe.duckdb")) as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("""INSERT INTO source_registry
            (source_id,name,steward,source_url,config_json,registered_at)
            VALUES (?, 'Isolated API probe', 'NYC Open Data', ?, '{}', now())""",
            [source_id, source_url])
        for name, select, expected in (
            ("aggregate", "count(*) as record_count", "WITHIN_REQUESTED_LIMIT"),
            ("sentinel", "camis", "ROW_LIMIT_EXCEEDED"),
        ):
            case = {"name": name, "query": {"$select": select, "$limit": 2},
                    "row_limit": 1, "expected_status": expected}
            start = time.perf_counter()
            error_code = None
            try:
                download_socrata_csv(conn, source_id, name, None, select=select, limit=1)
            except Exception as error:
                error_code = ("SOCRATA_ROW_LIMIT_EXCEEDED" if isinstance(error, ValueError)
                              and str(error).startswith("SOCRATA_ROW_LIMIT_EXCEEDED:")
                              else type(error).__name__)
            case.update(seconds=round(time.perf_counter() - start, 4), error_code=error_code)
            snapshots = conn.execute("SELECT raw_path,metadata_path FROM dataset_snapshot WHERE vintage=?",
                                     [name]).fetchall()
            if len(snapshots) == 1:
                raw, manifest = map(Path, snapshots[0])
                check = raw.with_name("row-limit-check.json")
                metadata = json.loads(manifest.read_text(encoding="utf-8"))
                case.update(raw_sha256=sha256_file(raw), raw_bytes=raw.stat().st_size,
                            manifest_sha256=sha256_file(manifest),
                            http_status=metadata.get("http_status"), transport=metadata.get("transport"),
                            raw_relative_path=raw.relative_to(output).as_posix())
                if check.is_file():
                    case["check"] = json.loads(check.read_text(encoding="utf-8"))
                    case["check_sha256"] = sha256_file(check)
            wanted_error = "SOCRATA_ROW_LIMIT_EXCEEDED" if name == "sentinel" else None
            case["passed"] = (case.get("http_status") == 200 and error_code == wanted_error
                              and case.get("check", {}).get("status") == expected
                              and case["check"]["rows_read"] == (2 if name == "sentinel" else 1))
            receipt["cases"].append(case)
    receipt["status"] = "PASS" if all(case["passed"] for case in receipt["cases"]) else "FAILED"
    with (output / "receipt.json").open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"status": receipt["status"], "receipt": str(output / "receipt.json"),
                      "cases": [{key: case[key] for key in ("name", "passed", "error_code", "seconds")}
                                for case in receipt["cases"]]}))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
