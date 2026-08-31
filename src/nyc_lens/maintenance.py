from __future__ import annotations

import json
import math
from typing import Any

import duckdb

from .db import begin_run, finish_run
from .util import json_dumps


def _clamp_percentiles(value: Any) -> int:
    """Clamp nested JSON percentile fields in place and return the change count."""
    changed = 0
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                key == "percentile"
                and isinstance(item, (int, float))
                and not isinstance(item, bool)
                and math.isfinite(float(item))
                and (item < 0 or item > 100)
            ):
                value[key] = min(100.0, max(0.0, float(item)))
                changed += 1
            else:
                changed += _clamp_percentiles(item)
    elif isinstance(value, list):
        for item in value:
            changed += _clamp_percentiles(item)
    return changed


def repair_site_percentile_bounds(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Repair legacy site results created before percentile bounds were enforced."""
    source_id = "derived_citywide_benchmark"
    run_id = begin_run(conn, source_id, "repair legacy site percentile bounds")
    affected = conn.execute(
        """
        SELECT DISTINCT site_id
        FROM site_metric
        WHERE percentile IS NOT NULL AND (percentile < 0 OR percentile > 100)
        ORDER BY site_id
        """
    ).fetchall()
    site_ids = [row[0] for row in affected]
    metric_rows = conn.execute(
        """
        SELECT count(*) FROM site_metric
        WHERE percentile IS NOT NULL AND (percentile < 0 OR percentile > 100)
        """
    ).fetchone()[0]
    json_percentiles = 0
    updated_site_queries = 0
    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            UPDATE site_metric
            SET percentile=least(100.0, greatest(0.0, percentile))
            WHERE percentile IS NOT NULL AND (percentile < 0 OR percentile > 100)
            """
        )
        for site_id in site_ids:
            row = conn.execute(
                "SELECT result_json FROM site_query WHERE site_id=?", [site_id]
            ).fetchone()
            if not row or row[0] is None:
                continue
            payload = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            changed = _clamp_percentiles(payload)
            if changed:
                conn.execute(
                    "UPDATE site_query SET result_json=?::JSON WHERE site_id=?",
                    [json_dumps(payload), site_id],
                )
                json_percentiles += changed
                updated_site_queries += 1
        conn.execute("COMMIT")
        warning = (
            "Clamped only legacy percentile values outside the documented [0,100] range; "
            "no raw source or benchmark observation values were changed."
        )
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS" if metric_rows else "SUCCEEDED",
            updated_records=int(metric_rows) + json_percentiles,
            warnings=[warning] if metric_rows else [],
        )
        return {
            "run_id": run_id,
            "site_metric_rows_repaired": int(metric_rows),
            "result_json_percentiles_repaired": json_percentiles,
            "site_queries_repaired": updated_site_queries,
            "affected_site_ids": site_ids,
        }
    except Exception as exc:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        finish_run(
            conn,
            run_id,
            "FAILED",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        raise
