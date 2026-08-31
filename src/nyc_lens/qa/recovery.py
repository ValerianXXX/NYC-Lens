from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import duckdb

from ..paths import project_home
from ..util import new_id, utc_now


def verify_clean_init() -> dict[str, Any]:
    """Initialize a brand-new isolated project home without touching the live database."""
    probe_id = new_id("clean_init")
    live_home = project_home()
    target = live_home / "outputs" / probe_id
    if target.exists():
        raise FileExistsError(f"Clean-init target already exists: {target}")
    target.mkdir(parents=True)
    shutil.copytree(live_home / "config", target / "config")
    shutil.copytree(live_home / "ui", target / "ui")
    environment = os.environ.copy()
    environment["NYC_LENS_HOME"] = str(target)
    environment.pop("NYC_LENS_DATABASE", None)
    completed = subprocess.run(
        [sys.executable, "-m", "nyc_lens.cli", "init"],
        cwd=live_home,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    probe_database = target / "data" / "nyc_lens.duckdb"
    table_count = 0
    source_count = 0
    if completed.returncode == 0 and probe_database.is_file():
        conn = duckdb.connect(str(probe_database), read_only=True)
        try:
            table_count = conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='main'"
            ).fetchone()[0]
            source_count = conn.execute("SELECT count(*) FROM source_registry").fetchone()[0]
        finally:
            conn.close()
    passed = (
        completed.returncode == 0
        and probe_database.is_file()
        and table_count >= 30
        and source_count >= 40
        and (target / "config" / "sources.yml").is_file()
        and (target / "ui" / "index.html").is_file()
    )
    report = {
        "probe_id": probe_id,
        "status": "PASS" if passed else "FAIL",
        "created_at": utc_now().isoformat(),
        "isolated_home": str(target),
        "database": str(probe_database),
        "database_bytes": probe_database.stat().st_size if probe_database.is_file() else 0,
        "table_count": table_count,
        "source_registry_count": source_count,
        "command_return_code": completed.returncode,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
        "live_database_touched": False,
        "probe_retained_for_inspection": True,
        "scope": "clean schema/config/UI initialization; public-data refresh is separately covered by ingestion runs and Raw hash QA",
    }
    report_path = live_home / "reports" / "qa" / f"{probe_id}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(report_path)
    if not passed:
        raise RuntimeError(json.dumps(report, ensure_ascii=False))
    return report
