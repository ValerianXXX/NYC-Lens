from __future__ import annotations

import json
from contextlib import contextmanager
import re
import sys
from pathlib import Path
from typing import Annotated, Optional
from uuid import uuid4

import typer

from .analysis_contracts import normalized_request
from .analysis_jobs import AnalysisFailedError, AnalysisJobs, JobUnavailableError, run_analysis_sync
from .database_admission import DatabaseBusyError
from .adapters.commercial_mobility import (
    inspect_commercial_mobility_adapters,
    read_and_validate_aggregate_file,
)
from .adapters.commercial_real_estate import (
    inspect_commercial_real_estate_adapters,
    read_and_validate_local_file as read_and_validate_real_estate_file,
)
from .config import test_sites_config
from .db import db_session, initialize_database
from .geocoding.service import record_geoservice_status
from .ingest.census import refresh_acs, refresh_tiger_boundaries
from .ingest.cases import refresh_public_transaction_cases
from .ingest.citibike import refresh_citibike_gbfs, refresh_citibike_monthly_report
from .ingest.acris import refresh_acris_transactions
from .ingest.civic import refresh_facilities, refresh_nysla_licenses, refresh_short_term_rentals
from .ingest.decennial import refresh_decennial_pl
from .ingest.environment import refresh_hazard_layer
from .ingest.fema import refresh_fema_sfha
from .ingest.geographies import refresh_h3_grid, refresh_nyc_geography, refresh_zcta
from .ingest.land import refresh_building_footprints, refresh_mappluto
from .ingest.lodes import refresh_lodes
from .ingest.mta import (
    refresh_bus_gtfs,
    refresh_bus_realtime_status,
    refresh_gtfs,
    refresh_hourly_ridership,
    refresh_realtime_feed_catalog,
    refresh_stations,
    refresh_subway_entrances,
)
from .ingest.mobility import refresh_current_traffic_speed, refresh_pedestrian_counts, refresh_traffic_counts
from .ingest.overture import refresh_overture_buildings, refresh_overture_places
from .ingest.places import refresh_cdc_places, refresh_cdc_places_2024_fallback
from .ingest.property import (
    refresh_active_dob_violations,
    refresh_active_ecb_violations,
    refresh_active_safety_violations,
    refresh_bis_permits,
    refresh_dob_now_certificates_of_occupancy,
    refresh_dob_now_permits,
    refresh_legacy_certificates_of_occupancy,
    refresh_rolling_sales,
)
from .ingest.regional import refresh_region_definition, refresh_regional_counties, refresh_regional_tracts
from .ingest.regional_transit import refresh_regional_mta_rail_gtfs
from .ingest.regulatory import refresh_311_aggregates
from .ingest.socrata import refresh_air_quality, refresh_dohmh, refresh_hotels
from .ingest.tlc import refresh_taxi_zones, refresh_tlc_monthly_aggregates
from .maintenance import repair_site_percentile_bounds
from .metrics.benchmarks import refresh_citywide_benchmarks
from .documentation import write_generated_docs
from .paths import project_home
from .qa.address_validation import validate_public_addresses
from .qa.checks import run_qa
from .qa.recovery import verify_clean_init
from .reporting.export import export_all
from .reporting.comparison_export import export_comparison_all
from .storage_migration import (
    compact_catalog_database,
    external_storage_status,
    migrate_to_external_storage,
)
from .scoring import scoring_status


app = typer.Typer(help="NYC Lens | Local trade-area and commercial intelligence", no_args_is_help=True)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _print(value) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


@contextmanager
def _analysis_errors(identifier: str | None = None):
    """Stable CLI errors without copying upstream exception text or user input."""
    try:
        yield
    except AnalysisFailedError as error:
        receipt = error.receipt
        _print({"status": "error", "error": {
            "code": receipt["error_code"], "status": receipt["status"],
            "job_id": receipt["job_id"], "database_cleanup_confirmed": receipt["database_cleanup_confirmed"],
            "message": "Analysis did not complete. Inspect the saved task before starting another request."}})
        raise typer.Exit(1) from None
    except (ValueError, KeyError, JobUnavailableError, DatabaseBusyError, KeyboardInterrupt) as error:
        exit_code = 1
        if isinstance(error, ValueError):
            code, exit_code = "INVALID_ANALYSIS_INPUT", 2
        elif isinstance(error, KeyError):
            code = "ANALYSIS_JOB_NOT_FOUND"
        elif isinstance(error, DatabaseBusyError):
            code = "DATABASE_BUSY"
        elif isinstance(error, KeyboardInterrupt):
            code, exit_code = "ANALYSIS_INTERRUPTED", 130
        else:
            allowed = {"RESOURCE_GUARD_UNAVAILABLE", "JOB_HISTORY_CAPACITY_REQUIRES_ARCHIVE",
                       "ANALYSIS_RESULT_NOT_READY", "JOB_RESULT_ID_MISMATCH", "SAVED_RESULT_UNAVAILABLE",
                       "JOB_RECEIPT_INVALID", "JOB_RECEIPT_UNAVAILABLE", "JOB_START_UNCONFIRMED", "JOB_START_FAILED"}
            code = str(error) if str(error) in allowed else "ANALYSIS_JOB_UNAVAILABLE"
        detail = {"code": code, "message": "Check the input and saved task status before retrying. No automatic retry was performed."}
        if identifier and re.fullmatch(r"analysis_[0-9a-f]{32}", identifier):
            detail["job_id"] = identifier
        _print({"status": "error", "error": detail})
        raise typer.Exit(exit_code) from None


def _run_cli_analysis(operation: str, request: dict, request_id: str | None = None) -> dict:
    key = request_id if request_id is not None else uuid4().hex
    identifier = "analysis_" + key
    with _analysis_errors(identifier):
        if not re.fullmatch(r"[0-9a-f]{32}", key):
            raise ValueError("Invalid request identity")
        normalized, _ = normalized_request(operation, request)
        # A new request retains source registration/initialization semantics.
        # Reattaching to a saved attempt must not reopen its active database or
        # apply newer source metadata to an already completed request.
        try:
            AnalysisJobs().get(identifier)
        except KeyError:
            initialize_database()
        typer.echo(f"Analysis request: {identifier}", err=True)
        return run_analysis_sync(operation, normalized, request_id=key)


@app.command("analysis-status")
def analysis_status_command(job_id: Annotated[str, typer.Option("--job-id")]) -> None:
    """Read a durable task receipt without rerunning its analysis."""
    with _analysis_errors(job_id):
        _print(AnalysisJobs().get(job_id))


@app.command("analysis-result")
def analysis_result_command(job_id: Annotated[str, typer.Option("--job-id")]) -> None:
    """Read a completed task result; incomplete attempts remain unavailable."""
    with _analysis_errors(job_id):
        _print(AnalysisJobs().result(job_id))


@app.command("analysis-cancel")
def analysis_cancel_command(job_id: Annotated[str, typer.Option("--job-id")]) -> None:
    """Request cancellation; inspect status to verify process/DB cleanup."""
    with _analysis_errors(job_id):
        _print(AnalysisJobs().cancel(job_id))


@app.command("init")
def init_command() -> None:
    """Initialize the local database and source registry."""
    _print(initialize_database())


def _heavy_refresh_progress(completed: int, total: int, label: str) -> None:
    if completed == 1 or completed % 10 == 0 or completed == total:
        typer.echo(f"[{completed}/{total}] {label}", err=True)


REFRESHERS = {
    "omb_2023_cbsa_delineation": refresh_region_definition,
    "tiger_county_2024": refresh_regional_counties,
    "tiger_tract_2024": refresh_regional_tracts,
    "tiger_bg_2024": refresh_tiger_boundaries,
    "nyc_census_blocks_2020": lambda conn: refresh_nyc_geography(conn, "nyc_census_blocks_2020"),
    "nyc_census_tracts_2020": lambda conn: refresh_nyc_geography(conn, "nyc_census_tracts_2020"),
    "nyc_nta_2020": lambda conn: refresh_nyc_geography(conn, "nyc_nta_2020"),
    "nyc_cdta_2020": lambda conn: refresh_nyc_geography(conn, "nyc_cdta_2020"),
    "nyc_community_districts": lambda conn: refresh_nyc_geography(conn, "nyc_community_districts"),
    "nyc_borough_boundaries": lambda conn: refresh_nyc_geography(conn, "nyc_borough_boundaries"),
    "census_zcta_2020": refresh_zcta,
    "derived_h3_grid": refresh_h3_grid,
    "nysla_active_licenses": refresh_nysla_licenses,
    "nyc_str_registration": refresh_short_term_rentals,
    "nyc_facilities": refresh_facilities,
    "acs_2024_5yr": refresh_acs,
    "census_decennial_2020_pl": refresh_decennial_pl,
    "lodes_2023": refresh_lodes,
    "mappluto_26v2": lambda conn: refresh_mappluto(conn, progress=_heavy_refresh_progress),
    "nyc_building_footprints": lambda conn: refresh_building_footprints(conn, progress=_heavy_refresh_progress),
    "dohmh_restaurants": refresh_dohmh,
    "overture_places": refresh_overture_places,
    "overture_buildings": refresh_overture_buildings,
    "mta_gtfs_static": refresh_gtfs,
    "regional_mta_rail_gtfs_static": refresh_regional_mta_rail_gtfs,
    "mta_bus_gtfs_static": refresh_bus_gtfs,
    "mta_realtime_feed_catalog": refresh_realtime_feed_catalog,
    "mta_bus_realtime": refresh_bus_realtime_status,
    "citibike_gbfs_current": refresh_citibike_gbfs,
    "citibike_monthly_operating_report": refresh_citibike_monthly_report,
    "nyc_hurricane_evacuation_zones": lambda conn: refresh_hazard_layer(
        conn, "nyc_hurricane_evacuation_zones"
    ),
    "nyc_future_floodplain_2050s": lambda conn: refresh_hazard_layer(
        conn, "nyc_future_floodplain_2050s"
    ),
    "nyc_sea_level_rise_2100_floodplain": lambda conn: refresh_hazard_layer(
        conn, "nyc_sea_level_rise_2100_floodplain"
    ),
    "fema_nfhl_special_flood_hazard_areas": refresh_fema_sfha,
    "cdc_places_2025_tract": refresh_cdc_places,
    "cdc_places_2024_tract_fallback": refresh_cdc_places_2024_fallback,
    "tlc_taxi_zones": refresh_taxi_zones,
    "tlc_trip_record_monthly_aggregates": refresh_tlc_monthly_aggregates,
    "mta_subway_stations": refresh_stations,
    "mta_subway_entrances_2024": refresh_subway_entrances,
    "mta_hourly_2025": refresh_hourly_ridership,
    "nyc_dot_traffic_counts": refresh_traffic_counts,
    "nyc_dot_pedestrian_counts": refresh_pedestrian_counts,
    "nyc_dot_traffic_speed_current": refresh_current_traffic_speed,
    "nyc_rolling_sales": refresh_rolling_sales,
    "derived_public_transaction_cases": refresh_public_transaction_cases,
    "acris_real_property_master_recent": refresh_acris_transactions,
    "nyc_311_recent_aggregates": refresh_311_aggregates,
    "dob_permit_issuance_recent": refresh_bis_permits,
    "dob_now_approved_permits_recent": refresh_dob_now_permits,
    "dob_violations_active": refresh_active_dob_violations,
    "dob_safety_violations_active": refresh_active_safety_violations,
    "dob_ecb_violations_active": refresh_active_ecb_violations,
    "dob_certificate_occupancy_legacy": refresh_legacy_certificates_of_occupancy,
    "dob_now_certificate_occupancy": refresh_dob_now_certificates_of_occupancy,
    "air_quality_health": refresh_air_quality,
    "nyc_hotels": refresh_hotels,
    "derived_citywide_benchmark": lambda conn: refresh_citywide_benchmarks(
        conn, progress=_heavy_refresh_progress
    ),
}


@app.command("refresh")
def refresh_command(source: Annotated[Optional[str], typer.Option("--source", help="Refresh one source_id only")] = None) -> None:
    """Download public data, save raw snapshots, and rebuild standardized layers."""
    initialize_database()
    selected = [source] if source else list(REFRESHERS)
    unknown = [item for item in selected if item not in REFRESHERS]
    if unknown:
        raise typer.BadParameter(f"Unknown source: {unknown[0]}")
    results = {}
    failures: list[str] = []
    with db_session() as conn:
        results["nyc_geoservice"] = record_geoservice_status(conn)
        for source_id in selected:
            typer.echo(f"[{source_id}] refreshing...", err=True)
            try:
                results[source_id] = REFRESHERS[source_id](conn)
            except Exception as exc:
                failures.append(source_id)
                results[source_id] = {
                    "status": "FAILED",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
    _print(results)
    if failures:
        typer.echo(f"Failed sources: {', '.join(failures)}", err=True)
        raise typer.Exit(1)


@app.command("analyze")
def analyze_command(
    address: Annotated[str, typer.Option("--address", help="Address in NYC or Greater New York CSA 408")],
    business_type: Annotated[str, typer.Option("--business-type")] = "full_service_restaurant",
    catchments: Annotated[str, typer.Option("--catchments")] = "500m,1km,5min_walk,10min_walk,15min_walk",
    export: Annotated[bool, typer.Option("--export/--no-export")] = True,
    request_id: Annotated[Optional[str], typer.Option("--request-id", help="32 lowercase hex characters; reuse for the identical analysis only")] = None,
) -> None:
    """Analyze one address and optionally export JSON, CSV, XLSX, and HTML."""
    result = _run_cli_analysis("site", {
        "address": address, "business_type": business_type,
        "catchments": [item.strip() for item in catchments.split(",") if item.strip()],
    }, request_id)
    if export:
        with db_session() as conn:
            result["exports"] = export_all(conn, result["site_id"])
    _print(result)


@app.command("samples")
def samples_command(
    sample: Annotated[Optional[str], typer.Option("--sample", help="Run one configured sample ID only")] = None,
) -> None:
    """Run the Times Square, Flushing, and Downtown Brooklyn samples end to end."""
    outputs = []
    configured_sites = test_sites_config()["sites"]
    selected_sites = [site for site in configured_sites if sample is None or site["id"] == sample]
    if sample and not selected_sites:
        raise typer.BadParameter(f"Unknown sample id: {sample}")
    for site in selected_sites:
        typer.echo(f"[{site['id']}] analyzing...", err=True)
        result = _run_cli_analysis("site", {"address": site["address"]})
        with db_session() as conn:
            exports = export_all(conn, result["site_id"], include_xlsx=False)
        preferred = result["catchments"].get("10min_walk") or next(iter(result["catchments"].values()))
        metrics = preferred["metrics"]
        outputs.append(
            {
                "sample_id": site["id"],
                "site_id": result["site_id"],
                "address": result["standardized_address"],
                "bbl": (result.get("parcel") or {}).get("bbl"),
                "catchments": list(result["catchments"]),
                "resident_population_10min": metrics.get("resident_population", {}).get("value"),
                "daytime_jobs_10min": metrics.get("daytime_jobs", {}).get("value"),
                "competitors_10min": metrics.get("direct_competitor_count", {}).get("value"),
                "audience_type_10min": metrics.get("audience_type", {}).get("text_value"),
                "missing_data": result["missing_data"],
                "exports": exports,
            }
        )
    index_path = project_home() / "reports" / "samples" / "latest_samples.json"
    index_path.write_text(
        json.dumps(outputs, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    for output in outputs:
        output["sample_index"] = str(index_path)
    _print(outputs)


@app.command("regional-samples")
def regional_samples_command(
    sample: Annotated[
        Optional[str], typer.Option("--sample", help="Run one configured regional sample ID only")
    ] = None,
    catchments: Annotated[str, typer.Option("--catchments")] = (
        "250m,500m,1km,5min_walk,10min_walk,15min_walk"
    ),
) -> None:
    """Run one Greater New York sample per state and export complete reports."""
    preferred_ids = {
        "mineola_nassau",
        "newark_essex",
        "stamford_western_ct",
        "milford_pike",
    }
    configured_sites = test_sites_config().get("regional_sites", [])
    selected_sites = [
        site
        for site in configured_sites
        if (sample is not None and site["id"] == sample)
        or (sample is None and site["id"] in preferred_ids)
    ]
    if sample and not selected_sites:
        raise typer.BadParameter(f"Unknown regional sample id: {sample}")
    requested = [item.strip() for item in catchments.split(",") if item.strip()]
    outputs = []
    for site in selected_sites:
        typer.echo(f"[{site['id']}] analyzing...", err=True)
        result = _run_cli_analysis("site", {"address": site["address"], "catchments": requested})
        with db_session() as conn:
            exports = export_all(conn, result["site_id"])
        preferred = result["catchments"].get("10min_walk") or next(
            iter(result["catchments"].values())
        )
        metrics = preferred["metrics"]
        outputs.append(
            {
                "sample_id": site["id"],
                "site_id": result["site_id"],
                "address": result["standardized_address"],
                "state_abbr": result.get("state_abbr"),
                "county_geoid": result.get("county_geoid"),
                "catchments": list(result["catchments"]),
                "resident_population_10min": metrics.get("resident_population", {}).get("value"),
                "daytime_jobs_10min": metrics.get("daytime_jobs", {}).get("value"),
                "competitors_10min": metrics.get("direct_competitor_count", {}).get("value"),
                "fema_sfha_overlap_pct_10min": metrics.get(
                    "fema_special_flood_hazard_overlap_pct", {}
                ).get("value"),
                "cdc_asthma_pct_10min": metrics.get(
                    "cdc_places_current_asthma_pct", {}
                ).get("value"),
                "audience_type_10min": metrics.get("audience_type", {}).get("text_value"),
                "missing_data": result["missing_data"],
                "exports": exports,
            }
        )
    index_path = project_home() / "reports" / "samples" / "latest_regional_samples.json"
    index_path.write_text(
        json.dumps(outputs, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    for output in outputs:
        output["sample_index"] = str(index_path)
    _print(outputs)


@app.command("compare")
def compare_command(
    address: Annotated[list[str], typer.Option("--address", help="Repeat 2 to 20 times")] = [],
    business_type: Annotated[str, typer.Option("--business-type")] = "full_service_restaurant",
    catchments: Annotated[str, typer.Option("--catchments")] = "500m,1km",
    comparison_catchment: Annotated[str, typer.Option("--comparison-catchment")] = "500m",
    export: Annotated[bool, typer.Option("--export/--no-export")] = True,
    request_id: Annotated[Optional[str], typer.Option("--request-id", help="32 lowercase hex characters; reuse for the identical analysis only")] = None,
) -> None:
    """Compare public addresses on the same basis without an uncalibrated score."""
    result = _run_cli_analysis("comparison", {
        "addresses": address, "business_type": business_type,
        "catchments": [item.strip() for item in catchments.split(",") if item.strip()],
        "comparison_catchment": comparison_catchment,
    }, request_id)
    if export:
        with db_session() as conn:
            result["exports"] = export_comparison_all(conn, result["comparison_id"])
    _print(result)


@app.command("status")
def status_command() -> None:
    """Show retrieval, record, and query status."""
    initialize_database()
    with db_session(read_only=True) as conn:
        sources = conn.execute(
            """
            WITH snapshots AS (
                SELECT source_id, max(retrieved_at) AS latest_snapshot,
                       count(snapshot_id) AS snapshots,
                       sum(coalesce(byte_size,0)) AS raw_bytes,
                       arg_max(status, retrieved_at) AS latest_snapshot_status
                FROM dataset_snapshot GROUP BY source_id
            ), runs AS (
                SELECT source_id, arg_max(status, started_at) AS latest_run_status,
                       max(started_at) AS latest_run_started_at
                FROM ingestion_run WHERE source_id IS NOT NULL GROUP BY source_id
            )
            SELECT s.source_id, p.latest_snapshot, coalesce(p.snapshots, 0) AS snapshots,
                   coalesce(p.raw_bytes, 0) AS raw_bytes, p.latest_snapshot_status,
                   r.latest_run_status, r.latest_run_started_at
            FROM source_registry s
            LEFT JOIN snapshots p USING(source_id)
            LEFT JOIN runs r USING(source_id)
            ORDER BY s.source_id
            """
        ).fetchdf().to_dict("records")
        table_names = [
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY table_name"
            ).fetchall()
        ]
        tables = [
            {"table_name": table_name, "rows": conn.execute(f'SELECT count(*) FROM "{table_name}"').fetchone()[0]}
            for table_name in table_names
        ]
        runs = conn.execute(
            "SELECT status, count(*) AS count FROM ingestion_run GROUP BY status ORDER BY status"
        ).fetchdf().to_dict("records")
    _print({"sources": sources, "tables": tables, "runs": runs})


@app.command("migrate-storage-a")
def migrate_storage_a_command() -> None:
    """Migrate high-volume facts to partitioned Parquet and compact the catalog."""
    _print(migrate_to_external_storage())


@app.command("storage-status")
def storage_status_command() -> None:
    """Validate external facts, partition directories, and catalog size."""
    _print(external_storage_status())


@app.command("compact-catalog")
def compact_catalog_command() -> None:
    """Atomically rebuild the catalog after externalizing high-volume facts."""
    _print(compact_catalog_database())


@app.command("adapters-status")
def adapters_status_command() -> None:
    """Show commercial-adapter license and privacy gates without contacting vendors."""
    _print(
        {
            "commercial_mobility": inspect_commercial_mobility_adapters(),
            "commercial_real_estate": inspect_commercial_real_estate_adapters(),
        }
    )


@app.command("validate-mobile-aggregate")
def validate_mobile_aggregate_command(
    input_file: Annotated[Path, typer.Option("--input", exists=True, dir_okay=False)],
    license_gate: Annotated[Path, typer.Option("--license-gate", exists=True, dir_okay=False)],
) -> None:
    """Validate an operator-supplied aggregate and license declaration offline."""
    _print(read_and_validate_aggregate_file(input_file, license_gate))


@app.command("validate-real-estate-file")
def validate_real_estate_file_command(
    input_file: Annotated[Path, typer.Option("--input", exists=True, dir_okay=False)],
    license_gate: Annotated[Path, typer.Option("--license-gate", exists=True, dir_okay=False)],
) -> None:
    """Validate an operator-supplied real-estate file and license gate offline."""
    _print(read_and_validate_real_estate_file(input_file, license_gate))


@app.command("scoring-status")
def scoring_status_command() -> None:
    """Show the real-project calibration gate for automated scoring."""
    initialize_database()
    with db_session(read_only=True) as conn:
        _print(scoring_status(conn))


@app.command("qa")
def qa_command(verify_hashes: Annotated[bool, typer.Option("--verify-hashes/--skip-hashes")] = True) -> None:
    """Run source, hash, geometry, coordinate, MOE, and lineage checks."""
    initialize_database()
    with db_session() as conn:
        report = run_qa(conn, verify_hashes=verify_hashes)
    _print(report)
    if report["summary"]["failed"]:
        raise typer.Exit(1)


@app.command("docs")
def docs_command() -> None:
    """Regenerate metric, data, and licensing documentation."""
    initialize_database()
    with db_session(read_only=True) as conn:
        result = write_generated_docs(conn)
    _print(result)


@app.command("repair-percentiles")
def repair_percentiles_command() -> None:
    """Repair legacy analysis percentiles outside the 0–100 range."""
    initialize_database()
    with db_session() as conn:
        result = repair_site_percentile_bounds(conn)
    _print(result)


@app.command("validate-addresses")
def validate_addresses_command(
    sample_size: Annotated[
        int,
        typer.Option("--sample-size", min=5, help="Stratified public-address sample size across five boroughs"),
    ] = 100,
    maximum_coordinate_error_m: Annotated[
        float,
        typer.Option("--maximum-coordinate-error-m", min=1, help="Allowed coordinate error in meters"),
    ] = 250.0,
) -> None:
    """Validate standardization, borough assignment, and coordinates with public addresses."""
    initialize_database()

    def progress(completed: int, total: int, address: str) -> None:
        if completed == 1 or completed % 10 == 0 or completed == total:
            typer.echo(f"[{completed}/{total}] {address}", err=True)

    with db_session() as conn:
        result = validate_public_addresses(
            conn,
            sample_size=sample_size,
            maximum_coordinate_error_m=maximum_coordinate_error_m,
            progress=progress,
        )
    _print(result)


@app.command("verify-clean-init")
def verify_clean_init_command() -> None:
    """Verify clean initialization in isolation without touching the working database."""
    _print(verify_clean_init())


@app.command("serve")
def serve_command(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Start the local-only bilingual web interface and API."""
    import uvicorn

    uvicorn.run("nyc_lens.api.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
