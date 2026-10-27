"""Local stdio tools for Hermes and compatible MCP hosts. No free-form SQL tool."""
from __future__ import annotations

from typing import Annotated, Literal, Any
from pydantic import Field
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .analyze import analyze_site
from .assistant import ComparisonIntent, ConfirmedComparison, PlanningRequest, execute_comparison, interpret
from .db import db_session, initialize_database
from .reporting.comparison_export import export_comparison_csv, export_comparison_json, export_comparison_xlsx
from .util import sha256_file

server = MCPServer("NYC Lens", version="0.2.0",
    instructions="Confirm the two addresses, full-service restaurant business type and radial scope with the user before compare_addresses. Preserve missing sources, years and warnings. Never infer a composite score. Synthetic demo data is not real trade-area evidence.")


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
                      language: Literal["en", "zh"] = "en") -> dict[str, Any]:
    """Compare exactly two user-confirmed restaurant addresses. Values come from existing spatial queries."""
    if confirmed is not True:
        raise ToolError("Confirm the addresses, restaurant type and scope with the user first")
    with db_session() as conn:
        return execute_comparison(conn, ConfirmedComparison(intent=ComparisonIntent(addresses=addresses,
            business_type="full_service_restaurant", catchment=catchment), confirmed=confirmed, language=language))


@server.tool()
def query_evidence(site_id: Annotated[str, Field(min_length=1, max_length=100)],
                   metric_id: Annotated[str, Field(min_length=2, max_length=100)],
                   catchment: Literal["250m", "500m", "1km"] = "500m",
                   limit: Annotated[int, Field(ge=1, le=100)] = 25) -> dict[str, Any]:
    """Read the same source/snapshot lineage shown by the Web evidence panel. No SQL input."""
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
