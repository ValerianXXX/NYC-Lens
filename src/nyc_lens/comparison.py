from __future__ import annotations

import json
import math
from numbers import Real
from typing import Any

import duckdb

from .analyze import analyze_site
from .config import configuration_snapshot, metrics_config, taxonomy_config
from .db import calculation_version
from .util import json_dumps, new_id, utc_now


DEFAULT_COMPARISON_CATCHMENTS = ["500m", "1km"]
MAX_COMPARISON_SITES = 20
COMPARISON_METHOD_VERSION = "comparable-basis-2026-10-29-v1"


def comparison_basis(values: list[dict[str, Any]]) -> dict[str, Any]:
    """A relative rank needs at least two finite values on one verified basis."""
    available = [row for row in values if isinstance(row.get("value"), Real)
                 and not isinstance(row["value"], bool) and math.isfinite(row["value"])]
    reasons = []
    if len(available) < 2:
        reasons.append("INSUFFICIENT_NUMERIC_DATA")
    for field in ("unit", "source_id", "snapshot_id", "source_year", "measure_type", "calculation_version_id"):
        observed = [row.get(field) for row in available]
        if any(value is None or not str(value).strip() or str(value).casefold() == "unknown" for value in observed):
            reasons.append(f"MISSING_{field.upper()}")
        elif len(set(observed)) > 1:
            reasons.append(f"MISMATCHED_{field.upper()}")
    return {"status": "INCOMPARABLE" if reasons else "COMPARABLE" if len(available) == len(values) else "PARTIAL_COMPARABLE",
            "reasons": reasons, "available_sites": len(available), "total_sites": len(values),
            "method_version": COMPARISON_METHOD_VERSION}


def _snapshot_union(results: list[dict[str, Any]]) -> dict[str, list[str]]:
    union: dict[str, set[str]] = {}
    for result in results:
        for source_id, snapshot_ids in result.get("snapshot_set", {}).items():
            union.setdefault(source_id, set()).update(str(value) for value in snapshot_ids)
    return {source_id: sorted(values) for source_id, values in sorted(union.items())}


def _metric_comparisons(
    results: list[dict[str, Any]],
    comparison_catchment: str,
) -> dict[str, Any]:
    definitions = metrics_config().get("metrics", {})
    metric_ids = sorted(
        {
            metric_id
            for result in results
            for metric_id in result["catchments"][comparison_catchment]["metrics"]
        }
    )
    comparisons: dict[str, Any] = {}
    for metric_id in metric_ids:
        values: list[dict[str, Any]] = []
        for result in results:
            metric = result["catchments"][comparison_catchment]["metrics"].get(metric_id, {})
            values.append(
                {
                    "site_id": result["site_id"],
                    "address": result["standardized_address"],
                    "value": metric.get("value"),
                    "text_value": metric.get("text_value"),
                    "unit": metric.get("unit"),
                    "source_id": metric.get("source_id"),
                    "snapshot_id": metric.get("snapshot_id"),
                    "source_year": metric.get("source_year"),
                    "measure_type": metric.get("measure_type"),
                    "confidence": metric.get("confidence"),
                    "margin_of_error": metric.get("margin_of_error"),
                    "calculation_version_id": metric.get("calculation_version_id") or result.get("calculation_version_id"),
                    "warnings": metric.get("warnings") or [],
                }
            )
        basis = comparison_basis(values)
        numeric_values = [float(item["value"]) for item in values
                          if isinstance(item["value"], Real) and not isinstance(item["value"], bool)
                          and math.isfinite(item["value"])]
        available = len(numeric_values)
        for item in values:
            valid = isinstance(item["value"], Real) and not isinstance(item["value"], bool) and math.isfinite(item["value"])
            if not valid and item["value"] is not None:
                item["value"] = None
                item["warnings"] = [*item["warnings"], "INVALID_NUMERIC_VALUE"]
            if not valid or basis["reasons"]:
                item["high_to_low_rank"] = None
                item["within_batch_high_value_percentile"] = None
                continue
            value = float(item["value"])
            # Competition rank preserves gaps after ties. Percentile compares
            # with other sites and gives half weight to equal-valued peers.
            item["high_to_low_rank"] = 1 + sum(other > value for other in numeric_values)
            item["within_batch_high_value_percentile"] = (
                100.0 * (sum(other < value for other in numeric_values)
                         + 0.5 * (sum(other == value for other in numeric_values) - 1)) / (available - 1)
                if available > 1
                else None
            )
        comparisons[metric_id] = {
            "label_zh": definitions.get(metric_id, {}).get("label_zh", metric_id),
            "unit": next((item["unit"] for item in values if item["unit"]), None),
            "available_sites": available,
            "rank_semantics": "raw_numeric_high_to_low_not_better_or_worse",
            "comparability": basis,
            "percentile_semantics": "other_comparable_sites_below_plus_half_equal_percent",
            "values": values,
        }
    return comparisons


@configuration_snapshot()
def compare_sites(
    conn: duckdb.DuckDBPyConnection,
    *,
    addresses: list[str],
    business_type: str = "full_service_restaurant",
    catchments: list[str] | None = None,
    comparison_catchment: str = "500m",
) -> dict[str, Any]:
    cleaned = [str(value).strip() for value in addresses if str(value).strip()]
    deduplicated = list(dict.fromkeys(value.casefold() for value in cleaned))
    if len(cleaned) != len(deduplicated):
        raise ValueError("Comparison addresses must be unique")
    if not 2 <= len(cleaned) <= MAX_COMPARISON_SITES:
        raise ValueError(f"Comparison requires 2 to {MAX_COMPARISON_SITES} addresses")
    if business_type not in taxonomy_config().get("business_types", {}):
        raise ValueError(f"Unknown business type: {business_type}")
    requested = list(dict.fromkeys(catchments or DEFAULT_COMPARISON_CATCHMENTS))
    if comparison_catchment not in requested:
        raise ValueError("comparison_catchment must be included in catchments")

    comparison_id = new_id("comparison")
    calculation_id = calculation_version(conn)
    created_at = utc_now()
    conn.execute(
        """
        INSERT INTO site_comparison VALUES (
            ?, ?::JSON, ?, ?::JSON, ?, '[]'::JSON, '{}'::JSON, ?, NULL,
            '[]'::JSON, 'RUNNING', ?, NULL
        )
        """,
        [
            comparison_id,
            json_dumps(cleaned),
            business_type,
            json_dumps(requested),
            comparison_catchment,
            calculation_id,
            created_at,
        ],
    )
    results: list[dict[str, Any]] = []
    try:
        for address in cleaned:
            results.append(
                analyze_site(
                    conn,
                    address=address,
                    business_type=business_type,
                    catchments=requested,
                )
            )
        calculation_ids = {result["calculation_version_id"] for result in results}
        if len(calculation_ids) != 1:
            raise RuntimeError("Compared sites used different calculation versions")
        metric_comparisons = _metric_comparisons(results, comparison_catchment)
        warnings = [
            "Rows are raw same-metric comparisons. High-to-low rank does not mean better, recommended, causal, or suitable.",
            "No composite site score is produced because NYC Lens has no approved real-project calibration set.",
            "Mixed jurisdictions can have different source coverage; each value retains its source, year, confidence, missingness and warnings.",
            "Within-batch percentiles describe only this small submitted address set and are not citywide or regional benchmarks.",
            "Ranks and differences are withheld when source snapshots, units, years, measure types or calculation versions are missing or inconsistent.",
        ]
        snapshot_set = _snapshot_union(results)
        result = {
            "comparison_id": comparison_id,
            "data_mode": "SYNTHETIC_DEMO" if any(item.get("data_mode") == "SYNTHETIC_DEMO" for item in results) else "LOCAL_SOURCE_DATA",
            "business_type": business_type,
            "catchments": requested,
            "comparison_catchment": comparison_catchment,
            "calculation_version_id": calculation_id,
            "comparison_method_version": COMPARISON_METHOD_VERSION,
            "snapshot_set": snapshot_set,
            "sites": [
                {
                    "site_id": item["site_id"],
                    "input_address": item["input_address"],
                    "standardized_address": item["standardized_address"],
                    "coordinates": item["coordinates"],
                    "borough": item.get("borough"),
                    "state_abbr": item.get("state_abbr"),
                    "county_geoid": item.get("county_geoid"),
                    "region_code": item.get("region_code"),
                    "parcel_bbl": (item.get("parcel") or {}).get("bbl"),
                    "warnings": item.get("warnings") or [],
                    "missing_data": item.get("missing_data") or [],
                }
                for item in results
            ],
            "metric_comparisons": metric_comparisons,
            "warnings": warnings,
        }
        conn.execute(
            """
            UPDATE site_comparison
            SET site_ids_json=?::JSON, snapshot_set_json=?::JSON, result_json=?::JSON,
                warnings_json=?::JSON, status='SUCCEEDED', completed_at=?
            WHERE comparison_id=?
            """,
            [
                json_dumps([item["site_id"] for item in results]),
                json_dumps(snapshot_set),
                json_dumps(result),
                json_dumps(warnings),
                utc_now(),
                comparison_id,
            ],
        )
        return result
    except Exception as exc:
        conn.execute(
            """
            UPDATE site_comparison
            SET site_ids_json=?::JSON, warnings_json=?::JSON,
                status='FAILED', completed_at=?
            WHERE comparison_id=?
            """,
            [
                json_dumps([item["site_id"] for item in results]),
                json_dumps([f"comparison_error:{type(exc).__name__}:{exc}"]),
                utc_now(),
                comparison_id,
            ],
        )
        raise


def get_comparison(
    conn: duckdb.DuckDBPyConnection,
    comparison_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT result_json FROM site_comparison WHERE comparison_id=? AND status='SUCCEEDED'",
        [comparison_id],
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return json.loads(row[0]) if isinstance(row[0], str) else row[0]
