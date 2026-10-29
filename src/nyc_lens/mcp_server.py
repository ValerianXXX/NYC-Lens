"""Local stdio tools for Hermes and compatible MCP hosts. No free-form SQL tool."""
from __future__ import annotations

from typing import Annotated, Literal, Any
from pydantic import Field
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .analyze import analyze_site
from .assistant import METRICS, ComparisonIntent, ConfirmedComparison, PlanningRequest, Priority, execute_comparison, interpret
from .db import db_session, initialize_database
from .reporting.comparison_export import export_comparison_csv, export_comparison_json, export_comparison_xlsx
from .util import sha256_file

server = MCPServer("NYC Lens", version="0.2.0",
    instructions="Confirm the two addresses, full-service restaurant business type and radial scope with the user before compare_addresses. Preserve missing sources, years and warnings. Never infer a composite score. Synthetic demo data is not real trade-area evidence.")

RequirementText = Annotated[str, Field(min_length=1, max_length=500)]


def _comparison_response(result: dict[str, Any], view: str) -> dict[str, Any]:
    """Keep the full stored analysis; focus the default tool response on requested metrics."""
    if view == "full":
        return result
    intent = result["assistant"]["confirmed_intent"]
    requested = dict.fromkeys(METRICS[priority] for priority in intent["priorities"])
    metrics = {key: result["metric_comparisons"][key] for key in requested if key in result["metric_comparisons"]}
    return {
        "comparison_id": result["comparison_id"],
        "data_mode": result["data_mode"],
        "metric_comparisons": metrics,
        "evidence_queries": [
            {"site_id": row["site_id"], "metric_id": metric_id, "catchment": intent["catchment"]}
            for metric_id, metric in metrics.items() for row in metric["values"]
            if row.get("snapshot_id") and (row.get("value") is not None or row.get("text_value") is not None)
        ],
        "report_arguments": {"comparison_id": result["comparison_id"], "format": "json"},
        "assistant": result["assistant"],
        "sites": [{key: value for key, value in site.items() if key != "missing_data"} for site in result["sites"]],
        "business_type": result["business_type"],
        "comparison_catchment": result["comparison_catchment"],
        "calculation_version_id": result["calculation_version_id"],
        "snapshot_set": result["snapshot_set"],
        "warnings": result["warnings"],
        "response_view": "summary",
        "response_scope": {
            "requested_metrics": list(requested),
            "unavailable_metric_ids": [key for key in requested if key not in metrics],
            "omitted_metric_count": len(result["metric_comparisons"]) - len(metrics),
            "full_result": "generate_report retains every metric, source, warning and missing-data item; response_view=full returns the complete analysis inline.",
        },
    }


@server.tool()
def interpret_requirements(text: Annotated[str, Field(min_length=5, max_length=4000)], language: Literal["en", "zh"] = "en") -> dict[str, Any]:
    """Prepare an editable comparison intent. Does not run analysis or grant confirmation."""
    return interpret(PlanningRequest(text=text, language=language))


@server.tool()
def analyze_address(address: Annotated[str, Field(min_length=5, max_length=200)],
                    catchment: Literal["250m", "500m", "1km"] = "500m") -> dict[str, Any]:
    """Analyze a full-service restaurant using the existing local engine; may query public geocoding/parcel services."""
    with db_session() as conn:
        return analyze_site(conn, address=address, business_type="full_service_restaurant", catchments=[catchment])


@server.tool()
def compare_addresses(addresses: Annotated[list[str], Field(min_length=2, max_length=2)],
                      confirmed: bool = False, catchment: Literal["250m", "500m", "1km"] = "500m",
                      language: Literal["en", "zh"] = "en",
                      original_request: Annotated[str, Field(max_length=4000)] = "",
                      priorities: Annotated[list[Priority] | None, Field(max_length=6)] = None,
                      unmodeled_requirements: Annotated[list[RequirementText] | None, Field(max_length=12)] = None,
                      response_view: Literal["summary", "full"] = "summary") -> dict[str, Any]:
    """Compare two confirmed restaurant addresses using spatial queries. Carry forward the original request,
    priorities and unsupported requirements from interpretation. Summary returns the requested metrics,
    missingness and exact evidence_queries/report_arguments for the next tools. Full results are always
    saved for report export; response_view=full includes all metrics inline. No extra data access is granted."""
    if confirmed is not True:
        raise ToolError("Confirm the addresses, restaurant type and scope with the user first")
    intent = ComparisonIntent(addresses=addresses, business_type="full_service_restaurant", catchment=catchment,
                              unmodeled_requirements=unmodeled_requirements or [])
    if priorities is not None:
        intent = ComparisonIntent(**{**intent.model_dump(), "priorities": priorities})
    with db_session() as conn:
        result = execute_comparison(conn, ConfirmedComparison(intent=intent, confirmed=confirmed,
            language=language, original_request=original_request))
    return _comparison_response(result, response_view)


@server.tool()
def query_evidence(site_id: Annotated[str, Field(min_length=1, max_length=100)],
                   metric_id: Annotated[str, Field(min_length=2, max_length=100)],
                   catchment: Literal["250m", "500m", "1km"] = "500m",
                   limit: Annotated[int, Field(ge=1, le=100)] = 25) -> dict[str, Any]:
    """Read source/snapshot lineage using an exact entry from compare_addresses.evidence_queries.
    Metric IDs are canonical identifiers, not labels or guessed names. No SQL input."""
    from .api.app import evidence
    return evidence(site_id=site_id, metric_id=metric_id, catchment=catchment, limit=limit, offset=0)


@server.tool()
def generate_report(comparison_id: Annotated[str, Field(min_length=1, max_length=100)],
                    format: Literal["json", "csv", "xlsx"] = "json") -> dict[str, Any]:
    """Save a report for an existing comparison in the configured local report directory. Does not upload it."""
    with db_session() as conn:
        path = {"json": export_comparison_json, "csv": export_comparison_csv, "xlsx": export_comparison_xlsx}[format](conn, comparison_id)
    return {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size,
            "web_url": f"/api/comparison/{comparison_id}/report?format={format}", "uploaded": False}


def main() -> None:
    initialize_database()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
