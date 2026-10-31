from __future__ import annotations

import html as html_lib
import io
import re
from datetime import date
from typing import Any

import duckdb
import geopandas as gpd
import httpx
import pandas as pd
from shapely import from_wkt

from ..config import geographies_config, get_source
from ..db import begin_run, finish_run
from ..provenance import download_snapshot, save_bytes_snapshot
from ..publication import publication_transaction
from .common import register_standardized_records, stable_id, standardized_path, update_snapshot_details
from .geographies import _prepare_geometry, _replace_boundaries


TRIP_LINK_PATTERN = re.compile(
    r"https://[^\"'<>\s]+/(?P<fleet>yellow|green)_tripdata_(?P<period>\d{4}-\d{2})\.parquet",
    re.IGNORECASE,
)
MAX_SINGLE_REMOTE_OBJECT_BYTES = 5_000_000_000
MAX_REMOTE_PHASE_BYTES = 20_000_000_000
MINIMUM_PUBLISHED_CELL_COUNT = 5


def _trip_links(page_html: str) -> dict[tuple[str, str], str]:
    links: dict[tuple[str, str], str] = {}
    for match in TRIP_LINK_PATTERN.finditer(html_lib.unescape(page_html)):
        fleet = match.group("fleet").lower()
        period = match.group("period")
        links[(fleet, period)] = match.group(0)
    return links


def _latest_common_period(links: dict[tuple[str, str], str]) -> str:
    yellow = {period for fleet, period in links if fleet == "yellow"}
    green = {period for fleet, period in links if fleet == "green"}
    common = sorted(yellow & green)
    if not common:
        raise ValueError("TLC page has no common yellow/green monthly Parquet period")
    return common[-1]


def _taxi_zone_boundary_rows(
    frame: gpd.GeoDataFrame,
    *,
    snapshot_id: str,
    retrieved_at: Any,
) -> pd.DataFrame:
    metric = frame.to_crs(geographies_config()["analysis_crs"])
    rows: list[dict[str, Any]] = []
    for position, (_, row) in enumerate(frame.iterrows()):
        location_id = str(int(float(row["LocationID"])))
        west, south, east, north = row.geometry.bounds
        rows.append(
            {
                "boundary_id": stable_id("boundary", "tlc_taxi_zone", location_id),
                "geography_type": "tlc_taxi_zone",
                "geography_id": location_id,
                "name": str(row.get("zone") or f"Taxi Zone {location_id}"),
                "parent_id": str(row.get("borough") or ""),
                "geom_wkt": row.geometry.wkt,
                "area_m2": float(metric.geometry.iloc[position].area),
                "source_id": "tlc_taxi_zones",
                "snapshot_id": snapshot_id,
                "retrieved_at": retrieved_at,
                "valid_from": None,
                "valid_to": None,
                "measure_type": "administrative_transport_zone",
                "confidence": 0.98,
                "method_version": "0.1.0",
                "min_longitude": float(west),
                "min_latitude": float(south),
                "max_longitude": float(east),
                "max_latitude": float(north),
            }
        )
    result = pd.DataFrame(rows)
    if result["geography_id"].duplicated().any():
        raise ValueError("TLC taxi-zone package contains duplicate LocationID values")
    return result


def refresh_taxi_zones(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "tlc_taxi_zones"
    source = get_source(source_id)
    run_id = begin_run(conn, source_id, "refresh taxi-zone polygons")
    downloaded_bytes = 0
    try:
        snapshot = download_snapshot(
            conn,
            source_id=source_id,
            vintage="current",
            url=source["api_url"],
            filename="taxi_zones.zip",
            run_id=run_id,
            timeout_seconds=300,
        )
        downloaded_bytes = snapshot.byte_size
        frame = gpd.read_file(
            f"zip://{snapshot.path.resolve().as_posix()}!taxi_zones/taxi_zones.shp"
        )
        frame, invalid_geometry_count, removed_geometry_count = _prepare_geometry(frame)
        if not 250 <= len(frame) <= 300:
            raise ValueError(f"TLC taxi-zone coverage unexpectedly has {len(frame)} features")
        boundaries = _taxi_zone_boundary_rows(
            frame,
            snapshot_id=snapshot.snapshot_id,
            retrieved_at=snapshot.retrieved_at,
        )
        warnings = [
            "Taxi zones are coarse operating geographies, not exact pickup or dropoff locations.",
            f"Invalid geometries repaired: {invalid_geometry_count}; empty or missing geometries removed: {removed_geometry_count}.",
        ]
        output = standardized_path(source_id, snapshot.snapshot_id, "taxi_zones.parquet")
        frame.to_parquet(output, index=False)
        with publication_transaction(conn):
            previous, inserted = _replace_boundaries(
                conn, source_id, boundaries, snapshot.snapshot_id
            )
            update_snapshot_details(conn, snapshot.snapshot_id, boundaries, warnings=warnings)
            finish_run(
                conn,
                run_id,
                "SUCCEEDED_WITH_WARNINGS",
                request_count=1,
                downloaded_bytes=downloaded_bytes,
                inserted_records=inserted,
                deleted_records=int(previous),
                warnings=warnings,
            )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "taxi_zones": inserted,
            "invalid_geometries_repaired": invalid_geometry_count,
            "removed_geometries": removed_geometry_count,
            "raw_bytes": downloaded_bytes,
            "standardized_parquet": str(output),
        }
    except Exception as exc:
        finish_run(
            conn,
            run_id,
            "FAILED",
            request_count=1 if downloaded_bytes else 0,
            downloaded_bytes=downloaded_bytes,
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        raise


def _remote_object_metadata(url: str) -> dict[str, Any]:
    response = httpx.head(
        url,
        follow_redirects=True,
        timeout=60,
        headers={"User-Agent": "NYC-Lens/0.1 local-research"},
    )
    response.raise_for_status()
    size = int(response.headers.get("content-length") or 0)
    return {
        "url": url,
        "content_length": size,
        "etag": response.headers.get("etag"),
        "last_modified": response.headers.get("last-modified"),
    }


def _aggregate_remote_month(
    remote_conn: duckdb.DuckDBPyConnection,
    *,
    fleet: str,
    url: str,
    period_start: date,
) -> tuple[pd.DataFrame, int]:
    columns = {
        "yellow": ("tpep_pickup_datetime", "tpep_dropoff_datetime"),
        "green": ("lpep_pickup_datetime", "lpep_dropoff_datetime"),
    }
    if fleet not in columns:
        raise ValueError(f"Unsupported TLC fleet: {fleet}")
    pickup_column, dropoff_column = columns[fleet]
    period_end = (pd.Timestamp(period_start) + pd.offsets.MonthBegin()).date()
    total_rows = int(
        remote_conn.execute("SELECT count(*) FROM read_parquet(?)", [url]).fetchone()[0]
    )
    frame = remote_conn.execute(
        f"""
        WITH trips AS MATERIALIZED (
            SELECT {pickup_column} AS pickup_at,
                   {dropoff_column} AS dropoff_at,
                   CAST(PULocationID AS INTEGER) AS pickup_zone_id,
                   CAST(DOLocationID AS INTEGER) AS dropoff_zone_id
            FROM read_parquet(?)
            WHERE {pickup_column} >= ? AND {pickup_column} < ?
        ),
        events AS (
            SELECT 'pickup' AS direction, pickup_zone_id AS zone_id,
                   dayofweek(pickup_at) AS day_of_week, hour(pickup_at) AS hour_of_day
            FROM trips
            UNION ALL
            SELECT 'dropoff' AS direction, dropoff_zone_id AS zone_id,
                   dayofweek(dropoff_at) AS day_of_week, hour(dropoff_at) AS hour_of_day
            FROM trips
        )
        SELECT '{fleet}' AS fleet, direction, zone_id, day_of_week, hour_of_day,
               count(*)::BIGINT AS trip_count
        FROM events
        WHERE zone_id IS NOT NULL AND zone_id > 0
          AND day_of_week BETWEEN 0 AND 6 AND hour_of_day BETWEEN 0 AND 23
        GROUP BY direction, zone_id, day_of_week, hour_of_day
        ORDER BY direction, zone_id, day_of_week, hour_of_day
        """,
        [url, period_start, period_end],
    ).fetchdf()
    return frame, total_rows


def _zone_points(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    zones = conn.execute(
        """
        SELECT geography_id, geom_wkt
        FROM geo_boundary
        WHERE source_id='tlc_taxi_zones' AND geography_type='tlc_taxi_zone'
        ORDER BY geography_id
        """
    ).fetchdf()
    if len(zones) < 250:
        raise ValueError("Load TLC taxi zones before refreshing monthly trip aggregates")
    geometries = list(from_wkt(zones["geom_wkt"].to_numpy()))
    points = [geometry.representative_point() for geometry in geometries]
    zones["longitude"] = [point.x for point in points]
    zones["latitude"] = [point.y for point in points]
    return zones[["geography_id", "longitude", "latitude"]]


def refresh_tlc_monthly_aggregates(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "tlc_trip_record_monthly_aggregates"
    source = get_source(source_id)
    run_id = begin_run(
        conn,
        source_id,
        "refresh latest common yellow/green taxi-zone by day-of-week and hour aggregates",
    )
    downloaded_bytes = 0
    request_count = 0
    try:
        zones = _zone_points(conn)
        page = download_snapshot(
            conn,
            source_id=source_id,
            vintage="current-index",
            url=source["api_url"],
            filename="tlc_trip_record_data.html",
            run_id=run_id,
            timeout_seconds=180,
        )
        request_count += 1
        downloaded_bytes += page.byte_size
        links = _trip_links(page.path.read_text(encoding="utf-8", errors="replace"))
        period = _latest_common_period(links)
        period_start = pd.Timestamp(f"{period}-01").date()
        period_end = (pd.Timestamp(period_start) + pd.offsets.MonthEnd()).date()
        remote_objects = [
            _remote_object_metadata(links[(fleet, period)]) for fleet in ("yellow", "green")
        ]
        request_count += len(remote_objects)
        remote_bytes = sum(int(item["content_length"]) for item in remote_objects)
        oversized = [
            item for item in remote_objects if int(item["content_length"]) > MAX_SINGLE_REMOTE_OBJECT_BYTES
        ]
        if oversized or remote_bytes > MAX_REMOTE_PHASE_BYTES:
            raise RuntimeError(
                "TLC remote objects exceed the project storage/download guardrail; user confirmation is required"
            )

        frames: list[pd.DataFrame] = []
        source_row_counts: dict[str, int] = {}
        remote_conn = duckdb.connect()
        try:
            for fleet in ("yellow", "green"):
                aggregate, total_rows = _aggregate_remote_month(
                    remote_conn,
                    fleet=fleet,
                    url=links[(fleet, period)],
                    period_start=period_start,
                )
                frames.append(aggregate)
                source_row_counts[fleet] = total_rows
        finally:
            remote_conn.close()
        aggregate_all = pd.concat(frames, ignore_index=True)
        aggregate_all["trip_count"] = pd.to_numeric(
            aggregate_all["trip_count"], errors="raise"
        ).astype("int64")
        valid_trip_rows = {
            fleet: int(
                aggregate_all.loc[
                    (aggregate_all["fleet"] == fleet)
                    & (aggregate_all["direction"] == "pickup"),
                    "trip_count",
                ].sum()
            )
            for fleet in ("yellow", "green")
        }
        suppressed_mask = aggregate_all["trip_count"] < MINIMUM_PUBLISHED_CELL_COUNT
        suppressed_cells = int(suppressed_mask.sum())
        suppressed_trips = int(aggregate_all.loc[suppressed_mask, "trip_count"].sum())
        safe_aggregate = aggregate_all.loc[~suppressed_mask].copy()
        known_zone_ids = set(zones["geography_id"].astype(str))
        safe_aggregate["zone_id"] = safe_aggregate["zone_id"].astype(int).astype(str)
        unknown_zone_mask = ~safe_aggregate["zone_id"].isin(known_zone_ids)
        unknown_zone_cells = int(unknown_zone_mask.sum())
        unknown_zone_trips = int(
            safe_aggregate.loc[unknown_zone_mask, "trip_count"].sum()
        )

        aggregate_buffer = io.BytesIO()
        safe_aggregate.to_parquet(aggregate_buffer, index=False)
        aggregate_snapshot = save_bytes_snapshot(
            conn,
            source_id=source_id,
            vintage=period,
            filename=f"tlc_zone_dow_hour_aggregate_{period}.parquet",
            content=aggregate_buffer.getvalue(),
            source_url=source["url"],
            run_id=run_id,
            content_type="application/vnd.apache.parquet",
            valid_from=period_start,
            valid_to=period_end,
            query={
                "remote_objects": remote_objects,
                "projection": [
                    "pickup_datetime",
                    "dropoff_datetime",
                    "PULocationID",
                    "DOLocationID",
                ],
                "aggregation": "fleet x direction x taxi_zone x day_of_week x hour_of_day",
                "origin_destination_linkage_retained": False,
                "individual_trip_rows_retained": False,
                "minimum_published_cell_count": MINIMUM_PUBLISHED_CELL_COUNT,
            },
            warnings=[
                "This Raw artifact is already aggregated and contains no individual trip rows or linked origin-destination records.",
                f"Cells below {MINIMUM_PUBLISHED_CELL_COUNT} trips were suppressed before persistence.",
            ],
        )
        request_count += 1
        downloaded_bytes += aggregate_snapshot.byte_size
        update_snapshot_details(
            conn,
            aggregate_snapshot.snapshot_id,
            safe_aggregate,
            warnings=[
                "No individual trip rows or linked origin-destination pairs were retained.",
                f"Suppressed cells: {suppressed_cells}; trips in suppressed cells: {suppressed_trips}.",
                f"Known-zone standardization excluded cells: {unknown_zone_cells}; trips: {unknown_zone_trips}.",
            ],
        )

        standardized = safe_aggregate.loc[~unknown_zone_mask].merge(
            zones,
            left_on="zone_id",
            right_on="geography_id",
            how="inner",
            validate="many_to_one",
        )
        mobility = pd.DataFrame(
            {
                "mobility_proxy_id": standardized.apply(
                    lambda row: stable_id(
                        "mobility",
                        source_id,
                        period,
                        row["fleet"],
                        row["direction"],
                        row["zone_id"],
                        int(row["day_of_week"]),
                        int(row["hour_of_day"]),
                    ),
                    axis=1,
                ),
                "proxy_type": standardized.apply(
                    lambda row: f"tlc_{row['fleet']}_{row['direction']}_trip_count",
                    axis=1,
                ),
                "value": standardized["trip_count"].astype(float),
                "unit": "trips",
                "period": standardized.apply(
                    lambda row: (
                        f"{period}|dow={int(row['day_of_week'])}|hour={int(row['hour_of_day']):02d}"
                    ),
                    axis=1,
                ),
                "longitude": standardized["longitude"],
                "latitude": standardized["latitude"],
                "source_id": source_id,
                "snapshot_id": aggregate_snapshot.snapshot_id,
                "retrieved_at": aggregate_snapshot.retrieved_at,
                "valid_from": period_start,
                "valid_to": period_end,
                "geography_type": "tlc_taxi_zone",
                "geography_id": standardized["zone_id"],
                "measure_type": "privacy_guarded_monthly_zone_hour_aggregate",
                "confidence": 0.85,
                "method_version": "0.1.0",
            }
        )
        if mobility["mobility_proxy_id"].duplicated().any():
            raise ValueError("TLC aggregation produced duplicate standardized identifiers")
        previous = int(
            conn.execute(
                "SELECT count(*) FROM mobility_proxy WHERE source_id=?", [source_id]
            ).fetchone()[0]
        )
        conn.execute("DELETE FROM mobility_proxy WHERE source_id=?", [source_id])
        conn.register("_tlc_mobility", mobility)
        conn.execute("INSERT INTO mobility_proxy BY NAME SELECT * FROM _tlc_mobility")
        conn.unregister("_tlc_mobility")
        register_standardized_records(
            conn,
            mobility,
            entity_type="mobility_proxy",
            entity_id_column="mobility_proxy_id",
            source_id=source_id,
            snapshot_id=aggregate_snapshot.snapshot_id,
        )
        mobility.to_parquet(
            standardized_path(source_id, period, "zone_dow_hour_mobility.parquet"),
            index=False,
        )
        warnings = [
            "No individual trip rows or origin-destination linkage were retained locally.",
            f"Cells below {MINIMUM_PUBLISHED_CELL_COUNT} trips were suppressed.",
            "Taxi-zone metrics are area-level mobility proxies, not exact pickup points, dropoff points, unique people, or site footfall.",
        ]
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            inserted_records=len(mobility),
            deleted_records=previous,
            warnings=warnings,
        )
        return {
            "run_id": run_id,
            "index_snapshot_id": page.snapshot_id,
            "aggregate_snapshot_id": aggregate_snapshot.snapshot_id,
            "period": period,
            "remote_source_objects": remote_objects,
            "remote_source_total_bytes": remote_bytes,
            "source_rows": source_row_counts,
            "valid_pickup_month_rows": valid_trip_rows,
            "aggregate_cells_before_suppression": len(aggregate_all),
            "suppressed_cells": suppressed_cells,
            "suppressed_trips": suppressed_trips,
            "unknown_zone_cells_excluded": unknown_zone_cells,
            "unknown_zone_trips_excluded": unknown_zone_trips,
            "standardized_cells": len(mobility),
            "individual_trip_rows_retained": False,
            "origin_destination_linkage_retained": False,
            "local_retained_bytes_this_run": downloaded_bytes,
        }
    except Exception as exc:
        try:
            conn.unregister("_tlc_mobility")
        except Exception:
            pass
        finish_run(
            conn,
            run_id,
            "FAILED",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            error={"type": type(exc).__name__, "message": str(exc)},
            warnings=["No individual TLC trip rows are intentionally persisted by this adapter."],
        )
        raise
