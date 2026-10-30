"""Private bounded worker protocol; no SQL, command, provider or path tool."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

MAX_REQUEST_BYTES = 262_144


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(2)
    source = Path(sys.argv[1]).resolve(strict=True)
    if source.name != "request.json":
        raise SystemExit(2)
    destination = source.with_name("response.json")
    with source.open("rb") as stream:
        raw = stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise SystemExit(2)
    envelope = json.loads(raw)
    required = {"schema", "operation_id", "operation", "request", "home", "database", "site_ids", "comparison_ids", "database_limits"}
    if not isinstance(envelope, dict) or set(envelope) != required or envelope["schema"] != 1:
        raise SystemExit(2)
    if not Path(envelope["home"]).is_absolute() or not Path(envelope["database"]).is_absolute():
        raise SystemExit(2)
    os.environ["NYC_LENS_HOME"] = envelope["home"]
    os.environ["NYC_LENS_DATABASE"] = envelope["database"]
    from nyc_lens.analysis_contracts import normalized_request
    from nyc_lens.analyze import analyze_site
    from nyc_lens.assistant import ConfirmedComparison, execute_comparison
    from nyc_lens.comparison import compare_sites
    from nyc_lens.db import db_session
    from nyc_lens.execution_context import DatabaseLimits, worker_context
    payload = {"schema": 1, "operation_id": envelope["operation_id"], "status": "FAILED"}
    exit_code = 1
    try:
        request, count = normalized_request(envelope["operation"], envelope["request"])
        if len(envelope["site_ids"]) != count or len(envelope["comparison_ids"]) != (0 if envelope["operation"] == "site" else 1):
            raise ValueError("Invalid reserved identity count")
        limits = DatabaseLimits(**envelope["database_limits"])
        with worker_context(envelope["site_ids"], envelope["comparison_ids"], limits, source.parent / "spill"):
            with db_session() as conn:
                payload["database_settings"] = {
                    name: conn.execute("SELECT current_setting(?)", [name]).fetchone()[0]
                    for name in ("memory_limit", "threads", "max_temp_directory_size")
                }
                if envelope["operation"] == "site":
                    result = analyze_site(conn, **request)
                    identifier = result["site_id"]
                elif envelope["operation"] == "comparison":
                    result = compare_sites(conn, **request)
                    identifier = result["comparison_id"]
                else:
                    result = execute_comparison(conn, ConfirmedComparison.model_validate(request))
                    identifier = result["comparison_id"]
                payload.update(status="SUCCEEDED", result_id=identifier)
                exit_code = 0
    except BaseException as error:
        payload.update(error_type=type(error).__name__[:100])
    temporary = destination.with_suffix(".tmp")
    with temporary.open("x", encoding="utf8") as stream:
        json.dump(payload, stream, ensure_ascii=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
