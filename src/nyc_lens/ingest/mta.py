from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pandas as pd

from ..config import get_source
from ..db import begin_run, finish_run
from ..provenance import Snapshot, download_snapshot, save_bytes_snapshot
from ..util import json_dumps
from .common import as_number, register_standardized_records, register_table_lineage, stable_id, standardized_path, update_snapshot_details
from .socrata import download_socrata_csv, fetch_metadata_snapshot


MTA_RIDERSHIP_YEAR = 2025
MTA_PARTITION_DAYS = 7
MTA_PARTITION_LIMIT = 250_000
MTA_PARTITION_VERSION = "v2"
NYC_BOUNDS = (-74.25909, -73.700272, 40.477399, 40.917577)
MTA_REALTIME_FEEDS = (
    {
        "feed_id": "nyct_subway_1234567s",
        "mode": "subway",
        "name": "Subway 1/2/3/4/5/6/7/S",
        "url": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs",
        "credentials_required": False,
    },
    {
        "feed_id": "nyct_subway_ace",
        "mode": "subway",
        "name": "Subway A/C/E",
        "url": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-ace",
        "credentials_required": False,
    },
    {
        "feed_id": "nyct_subway_bdfm",
        "mode": "subway",
        "name": "Subway B/D/F/M",
        "url": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-bdfm",
        "credentials_required": False,
    },
    {
        "feed_id": "nyct_subway_g",
        "mode": "subway",
        "name": "Subway G",
        "url": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-g",
        "credentials_required": False,
    },
    {
        "feed_id": "nyct_subway_jz",
        "mode": "subway",
        "name": "Subway J/Z",
        "url": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-jz",
        "credentials_required": False,
    },
    {
        "feed_id": "nyct_subway_l",
        "mode": "subway",
        "name": "Subway L",
        "url": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-l",
        "credentials_required": False,
    },
    {
        "feed_id": "nyct_subway_nqrw",
        "mode": "subway",
        "name": "Subway N/Q/R/W",
        "url": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-nqrw",
        "credentials_required": False,
    },
    {
        "feed_id": "nyct_subway_si",
        "mode": "subway",
        "name": "Staten Island Railway",
        "url": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-si",
        "credentials_required": False,
    },
    {
        "feed_id": "mta_all_alerts",
        "mode": "all",
        "name": "All service alerts",
        "url": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fall-alerts",
        "credentials_required": False,
    },
    {
        "feed_id": "mta_bus_time",
        "mode": "bus",
        "name": "Bus Time APIs",
        "url": "https://bustime.mta.info/wiki/Developers/Index",
        "credentials_required": True,
    },
)


def _bounded_date_windows(year: int, days: int = MTA_PARTITION_DAYS) -> list[tuple[date, date]]:
    """Return contiguous [start, end) windows covering exactly one calendar year."""
    cursor = date(year, 1, 1)
    year_end = date(year + 1, 1, 1)
    windows: list[tuple[date, date]] = []
    while cursor < year_end:
        end_exclusive = min(cursor + timedelta(days=days), year_end)
        windows.append((cursor, end_exclusive))
        cursor = end_exclusive
    return windows


def _partition_vintage(year: int, start: date) -> str:
    return f"{year}-week-{MTA_PARTITION_VERSION}-{start.isoformat()}"


def _reusable_partition_snapshot(
    conn: duckdb.DuckDBPyConnection,
    source_id: str,
    year: int,
    start: date,
    end_exclusive: date,
) -> Snapshot | None:
    """Reuse a completed bounded partition, including one left by an interrupted run."""
    rows = conn.execute(
        """
        SELECT snapshot_id, source_id, raw_path, metadata_path, sha256,
               byte_size, retrieved_at, vintage
        FROM dataset_snapshot
        WHERE source_id=? AND vintage=? AND valid_from=? AND valid_to=?
          AND status='SUCCEEDED'
        ORDER BY retrieved_at DESC
        """,
        [source_id, _partition_vintage(year, start), start, end_exclusive - timedelta(days=1)],
    ).fetchall()
    for row in rows:
        path = Path(row[2])
        metadata_path = Path(row[3])
        if path.is_file() and metadata_path.is_file() and path.stat().st_size == row[5]:
            return Snapshot(
                snapshot_id=row[0],
                source_id=row[1],
                path=path,
                metadata_path=metadata_path,
                sha256=row[4],
                byte_size=row[5],
                retrieved_at=row[6],
                vintage=row[7],
            )
    return None


def _group_ridership_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "station_complex_id", "station_complex", "borough", "latitude", "longitude",
        "day_of_week", "hour_of_day", "ridership", "transfers",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"MTA partition is missing columns: {', '.join(missing)}")
    normalized = frame.copy()
    normalized["station_complex_id"] = normalized["station_complex_id"].astype("string")
    normalized["day_of_week"] = pd.to_numeric(normalized["day_of_week"], errors="coerce").astype("Int64")
    normalized["hour_of_day"] = pd.to_numeric(normalized["hour_of_day"], errors="coerce").astype("Int64")
    for column in ("latitude", "longitude", "ridership", "transfers"):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized = normalized.dropna(subset=["station_complex_id", "day_of_week", "hour_of_day", "ridership"])
    return (
        normalized.groupby(["station_complex_id", "day_of_week", "hour_of_day"], as_index=False, dropna=False)
        .agg(
            station_complex=("station_complex", "last"),
            borough=("borough", "last"),
            latitude=("latitude", "last"),
            longitude=("longitude", "last"),
            ridership=("ridership", "sum"),
            transfers=("transfers", "sum"),
        )
    )


def _aggregate_ridership_partitions(paths: list[Path]) -> pd.DataFrame:
    """Aggregate bounded server results while keeping memory bounded near the final table size."""
    aggregate: pd.DataFrame | None = None
    for path in paths:
        partition = pd.read_csv(path, dtype=str, low_memory=False)
        if len(partition) >= MTA_PARTITION_LIMIT:
            raise ValueError(f"MTA partition may be truncated at the API limit: {path}")
        if partition.empty:
            raise ValueError(f"MTA partition is empty: {path}")
        grouped = _group_ridership_frame(partition)
        aggregate = grouped if aggregate is None else _group_ridership_frame(pd.concat([aggregate, grouped], ignore_index=True))
    if aggregate is None or aggregate.empty:
        raise ValueError("No MTA ridership partitions were available")
    aggregate = aggregate.sort_values(["station_complex_id", "day_of_week", "hour_of_day"]).reset_index(drop=True)
    station_count = aggregate["station_complex_id"].nunique()
    if station_count < 400:
        raise ValueError(f"MTA station-complex coverage is unexpectedly low: {station_count}")
    if not aggregate["day_of_week"].between(0, 6).all():
        raise ValueError("MTA day_of_week is outside 0-6")
    if not aggregate["hour_of_day"].between(0, 23).all():
        raise ValueError("MTA hour_of_day is outside 0-23")
    if (aggregate["ridership"] < 0).any() or aggregate["ridership"].sum() <= 0:
        raise ValueError("MTA ridership contains an invalid annual total")
    return aggregate


def refresh_gtfs(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "mta_gtfs_static"
    run_id = begin_run(conn, source_id, "refresh")
    try:
        source = get_source(source_id)
        snapshot = download_snapshot(
            conn,
            source_id=source_id,
            vintage="current",
            url=source["api_url"],
            filename="gtfs_subway.zip",
            run_id=run_id,
        )
        with zipfile.ZipFile(snapshot.path) as archive:
            stops = pd.read_csv(archive.open("stops.txt"), dtype=str)
            feed_info = pd.read_csv(archive.open("feed_info.txt"), dtype=str) if "feed_info.txt" in archive.namelist() else pd.DataFrame()
        update_snapshot_details(
            conn,
            snapshot.snapshot_id,
            stops,
            warnings=["Static GTFS is scheduled service, not observed service."],
        )
        # Parent stations are the stable station-complex level used by the UI.
        parent = stops.loc[stops.get("location_type", "0").fillna("0").eq("1")].copy()
        if parent.empty:
            parent = stops.loc[stops.get("parent_station").isna()].copy()
        transit = pd.DataFrame(
            {
                "transit_stop_id": parent["stop_id"].map(lambda value: stable_id("stop", source_id, value)),
                "source_stop_id": parent["stop_id"],
                "complex_id": parent["stop_id"],
                "name": parent["stop_name"],
                "borough": None,
                "routes_json": "[]",
                "ada_status": parent.get("wheelchair_boarding"),
                "longitude": as_number(parent["stop_lon"]),
                "latitude": as_number(parent["stop_lat"]),
                "source_id": source_id,
                "snapshot_id": snapshot.snapshot_id,
                "retrieved_at": snapshot.retrieved_at,
                "valid_from": None,
                "valid_to": None,
                "geography_type": "point",
                "geography_id": parent["stop_id"],
                "measure_type": "scheduled_transit_record",
                "confidence": 0.95,
                "method_version": "0.1.0",
            }
        )
        conn.execute("DELETE FROM transit_stop WHERE source_id=?", [source_id])
        conn.register("_gtfs_stops", transit)
        conn.execute("INSERT INTO transit_stop BY NAME SELECT * FROM _gtfs_stops")
        conn.unregister("_gtfs_stops")
        register_standardized_records(
            conn,
            transit,
            entity_type="transit_stop",
            entity_id_column="transit_stop_id",
            source_id=source_id,
            snapshot_id=snapshot.snapshot_id,
            source_record_id_column="source_stop_id",
        )
        stops.to_parquet(standardized_path(source_id, "current", "stops.parquet"), index=False)
        if not feed_info.empty:
            feed_info.to_parquet(standardized_path(source_id, "current", "feed_info.parquet"), index=False)
        finish_run(conn, run_id, "SUCCEEDED", request_count=1, downloaded_bytes=snapshot.byte_size, inserted_records=len(transit))
        return {"run_id": run_id, "snapshot_id": snapshot.snapshot_id, "stops": len(stops), "parent_stations": len(transit), "raw_bytes": snapshot.byte_size}
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise


def _gtfs_date(value: Any) -> date | None:
    parsed = pd.to_datetime(value, format="%Y%m%d", errors="coerce")
    return parsed.date() if pd.notna(parsed) else None


def _bus_feed_frame(
    snapshot: Snapshot,
    *,
    feed_key: str,
    borough: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    with zipfile.ZipFile(snapshot.path) as archive:
        required = {"stops.txt", "routes.txt", "trips.txt", "stop_times.txt"}
        missing = sorted(required - set(archive.namelist()))
        if missing:
            raise ValueError(f"MTA bus feed {feed_key} is missing: {', '.join(missing)}")
        stops = pd.read_csv(archive.open("stops.txt"), dtype=str, low_memory=False)
        routes = pd.read_csv(archive.open("routes.txt"), dtype=str, low_memory=False)
        trips = pd.read_csv(
            archive.open("trips.txt"),
            dtype=str,
            usecols=lambda column: column in {"trip_id", "route_id"},
            low_memory=False,
        )
        feed_info = (
            pd.read_csv(archive.open("feed_info.txt"), dtype=str, low_memory=False)
            if "feed_info.txt" in archive.namelist()
            else pd.DataFrame()
        )
        route_label = routes.get("route_short_name", pd.Series(index=routes.index, dtype=str)).fillna("").str.strip()
        route_long = routes.get("route_long_name", pd.Series(index=routes.index, dtype=str)).fillna("").str.strip()
        route_label = route_label.where(route_label.ne(""), route_long)
        route_names = dict(zip(routes["route_id"].astype(str), route_label.astype(str)))
        trip_routes = {
            str(trip_id): route_names.get(str(route_id), str(route_id))
            for trip_id, route_id in zip(trips["trip_id"], trips["route_id"])
        }
        stop_routes: dict[str, set[str]] = {}
        with archive.open("stop_times.txt") as stop_times_handle:
            for chunk in pd.read_csv(
                stop_times_handle,
                dtype=str,
                usecols=lambda column: column in {"stop_id", "trip_id"},
                chunksize=500_000,
                low_memory=False,
            ):
                chunk["route_name"] = chunk["trip_id"].map(trip_routes)
                for stop_id, names in chunk.dropna(subset=["stop_id", "route_name"]).groupby("stop_id")["route_name"]:
                    stop_routes.setdefault(str(stop_id), set()).update(
                        str(value) for value in names if str(value).strip()
                    )

    location_type = stops.get("location_type", pd.Series("0", index=stops.index)).fillna("0")
    longitude = as_number(stops.get("stop_lon", pd.Series(index=stops.index, dtype=str)))
    latitude = as_number(stops.get("stop_lat", pd.Series(index=stops.index, dtype=str)))
    west, east, south, north = NYC_BOUNDS
    valid = (
        location_type.eq("0")
        & longitude.between(west, east)
        & latitude.between(south, north)
        & stops.get("stop_id", pd.Series(index=stops.index, dtype=str)).notna()
    )
    selected = stops.loc[valid].copy()
    selected_longitude = longitude.loc[valid]
    selected_latitude = latitude.loc[valid]
    source_stop_ids = selected["stop_id"].astype(str)
    valid_from = None
    valid_to = None
    if not feed_info.empty:
        start_values = feed_info.get("feed_start_date", pd.Series(dtype=str)).map(_gtfs_date).dropna()
        end_values = feed_info.get("feed_end_date", pd.Series(dtype=str)).map(_gtfs_date).dropna()
        valid_from = min(start_values) if not start_values.empty else None
        valid_to = max(end_values) if not end_values.empty else None
    transit = pd.DataFrame(
        {
            "transit_stop_id": source_stop_ids.map(
                lambda value: stable_id("stop", "mta_bus_gtfs_static", feed_key, value)
            ),
            "source_stop_id": source_stop_ids.map(lambda value: f"{feed_key}:{value}"),
            "complex_id": source_stop_ids.map(lambda value: f"{feed_key}:{value}"),
            "name": selected.get("stop_name", source_stop_ids),
            "borough": borough,
            "routes_json": source_stop_ids.map(
                lambda value: json_dumps(sorted(stop_routes.get(value, set())))
            ),
            "ada_status": selected.get("wheelchair_boarding"),
            "longitude": selected_longitude,
            "latitude": selected_latitude,
            "source_id": "mta_bus_gtfs_static",
            "snapshot_id": snapshot.snapshot_id,
            "retrieved_at": snapshot.retrieved_at,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "geography_type": "point",
            "geography_id": source_stop_ids.map(lambda value: f"{feed_key}:{value}"),
            "measure_type": "scheduled_transit_record",
            "confidence": 0.95,
            "method_version": "0.1.0",
        }
    ).drop_duplicates("transit_stop_id", keep="last")
    stats = {
        "feed_key": feed_key,
        "raw_stops": len(stops),
        "nyc_bus_stops": len(transit),
        "routes": len(routes),
        "trips": len(trips),
        "valid_from": valid_from,
        "valid_to": valid_to,
    }
    return transit, feed_info.assign(feed_key=feed_key), stats


def refresh_bus_gtfs(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "mta_bus_gtfs_static"
    source = get_source(source_id)
    run_id = begin_run(conn, source_id, "refresh six current MTA bus GTFS schedule feeds")
    request_count = 0
    downloaded_bytes = 0
    snapshots: list[Snapshot] = []
    frames: list[pd.DataFrame] = []
    feed_info_frames: list[pd.DataFrame] = []
    feed_stats: list[dict[str, Any]] = []
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=300,
            limits=httpx.Limits(max_keepalive_connections=2, max_connections=4),
        ) as client:
            for feed in source["feeds"]:
                snapshot = download_snapshot(
                    conn,
                    source_id=source_id,
                    vintage=f"current-{feed['key']}",
                    url=feed["url"],
                    filename=f"gtfs_bus_{feed['key']}.zip",
                    run_id=run_id,
                    timeout_seconds=300,
                    client=client,
                )
                request_count += 1
                downloaded_bytes += snapshot.byte_size
                snapshots.append(snapshot)
                transit, feed_info, stats = _bus_feed_frame(
                    snapshot,
                    feed_key=feed["key"],
                    borough=feed["borough"],
                )
                if len(transit) < 100:
                    raise ValueError(f"MTA bus feed {feed['key']} has unexpectedly low NYC stop coverage")
                frames.append(transit)
                feed_info_frames.append(feed_info)
                feed_stats.append(stats)
                update_snapshot_details(
                    conn,
                    snapshot.snapshot_id,
                    transit,
                    warnings=[
                        "Static schedule feed; temporary detours and observed service are not represented.",
                        f"NYC bounding-box filter retained {len(transit)} scheduled stop rows.",
                    ],
                )
        combined = pd.concat(frames, ignore_index=True)
        if combined["transit_stop_id"].duplicated().any():
            raise ValueError("MTA bus feed combination produced duplicate deterministic stop ids")
        previous = int(
            conn.execute("SELECT count(*) FROM transit_stop WHERE source_id=?", [source_id]).fetchone()[0]
        )
        conn.execute("DELETE FROM transit_stop WHERE source_id=?", [source_id])
        conn.register("_bus_stops", combined)
        conn.execute("INSERT INTO transit_stop BY NAME SELECT * FROM _bus_stops")
        conn.unregister("_bus_stops")
        for snapshot in snapshots:
            subset = combined.loc[combined["snapshot_id"] == snapshot.snapshot_id]
            register_standardized_records(
                conn,
                subset,
                entity_type="transit_stop",
                entity_id_column="transit_stop_id",
                source_id=source_id,
                snapshot_id=snapshot.snapshot_id,
                source_record_id_column="source_stop_id",
            )
        combined.to_parquet(
            standardized_path(source_id, "current", "bus_stops.parquet"), index=False
        )
        nonempty_feed_info = [frame for frame in feed_info_frames if not frame.empty]
        if nonempty_feed_info:
            pd.concat(nonempty_feed_info, ignore_index=True).to_parquet(
                standardized_path(source_id, "current", "feed_info.parquet"), index=False
            )
        route_names = set()
        for value in combined["routes_json"]:
            route_names.update(json.loads(value))
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            inserted_records=len(combined),
            deleted_records=previous,
            warnings=[
                "Six current quarterly schedule feeds were loaded; temporary changes and real-time performance are excluded.",
                "The MTA Bus Company feed spans multiple boroughs; its feed label is not treated as a precise borough assignment.",
            ],
        )
        return {
            "run_id": run_id,
            "snapshot_ids": [snapshot.snapshot_id for snapshot in snapshots],
            "feeds": feed_stats,
            "scheduled_stop_rows": len(combined),
            "route_names": len(route_names),
            "downloaded_bytes_this_run": downloaded_bytes,
        }
    except Exception as exc:
        finish_run(
            conn,
            run_id,
            "FAILED",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            error={"type": type(exc).__name__, "message": str(exc)},
            warnings=[f"Completed Raw feed snapshots remain reusable: {len(snapshots)}"],
        )
        raise


def refresh_realtime_feed_catalog(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "mta_realtime_feed_catalog"
    source = get_source(source_id)
    run_id = begin_run(conn, source_id, "refresh official realtime-feed documentation metadata")
    try:
        page = download_snapshot(
            conn,
            source_id=source_id,
            vintage="current-documentation",
            url=source["api_url"],
            filename="mta_gtfs_documentation_readme.md",
            run_id=run_id,
        )
        payload = {
            "source_page_snapshot_id": page.snapshot_id,
            "catalog_version": "2026-08-29",
            "feeds": list(MTA_REALTIME_FEEDS),
            "notes": [
                "Public subway and service-alert entries are endpoint metadata, not archived live observations.",
                "MTA Bus Time is separately credentials-gated and no account is created by NYC Lens.",
            ],
        }
        catalog = save_bytes_snapshot(
            conn,
            source_id=source_id,
            vintage="current-catalog",
            filename="realtime_feed_catalog.json",
            content=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            source_url=source["url"],
            run_id=run_id,
            content_type="application/json",
            query={"method": "normalized endpoint catalog linked to official developer documentation"},
            warnings=["This snapshot is metadata only and contains no vehicle or passenger observations."],
        )
        update_snapshot_details(conn, catalog.snapshot_id, pd.DataFrame(MTA_REALTIME_FEEDS))
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=1,
            downloaded_bytes=page.byte_size,
            inserted_records=len(MTA_REALTIME_FEEDS),
            warnings=["Catalog metadata loaded; no live vehicle positions were retained."],
        )
        return {
            "run_id": run_id,
            "documentation_snapshot_id": page.snapshot_id,
            "catalog_snapshot_id": catalog.snapshot_id,
            "feeds": len(MTA_REALTIME_FEEDS),
            "live_observations_downloaded": False,
        }
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise


def refresh_bus_realtime_status(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "mta_bus_realtime"
    run_id = begin_run(conn, source_id, "check operator-provided Bus Time credential")
    credential_present = bool(os.getenv("MTA_BUS_TIME_API_KEY", "").strip())
    if not credential_present:
        warning = (
            "MTA documents that Bus Time APIs require an account and API key; none was supplied. "
            "NYC Lens did not create an account, submit a form, or download live bus data."
        )
        finish_run(conn, run_id, "BLOCKED_CREDENTIALS", warnings=[warning])
        return {"run_id": run_id, "status": "BLOCKED_CREDENTIALS", "warning": warning}
    warning = (
        "An operator-provided Bus Time credential is present, but this metadata-only phase does not retain "
        "live vehicle observations or expose the credential."
    )
    finish_run(conn, run_id, "SUCCEEDED_WITH_WARNINGS", warnings=[warning])
    return {
        "run_id": run_id,
        "status": "SUCCEEDED_WITH_WARNINGS",
        "credential_present": True,
        "live_observations_downloaded": False,
        "warning": warning,
    }


def refresh_stations(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "mta_subway_stations"
    run_id = begin_run(conn, source_id, "refresh")
    try:
        fetch_metadata_snapshot(conn, source_id, "current", run_id)
        snapshot = download_socrata_csv(conn, source_id, "current", run_id, limit=10_000)
        frame = pd.read_csv(snapshot.path, dtype=str)
        update_snapshot_details(conn, snapshot.snapshot_id, frame)
        transit = pd.DataFrame(
            {
                "transit_stop_id": frame.apply(lambda row: stable_id("stop", source_id, row.get("station_id"), row.get("gtfs_stop_id")), axis=1),
                "source_stop_id": frame.get("gtfs_stop_id"),
                "complex_id": frame.get("complex_id"),
                "name": frame.get("stop_name"),
                "borough": frame.get("borough"),
                "routes_json": frame.get("daytime_routes", "").fillna("").map(lambda value: json_dumps(value.split() if value else [])),
                "ada_status": frame.get("ada"),
                "longitude": as_number(frame.get("gtfs_longitude")),
                "latitude": as_number(frame.get("gtfs_latitude")),
                "source_id": source_id,
                "snapshot_id": snapshot.snapshot_id,
                "retrieved_at": snapshot.retrieved_at,
                "valid_from": None,
                "valid_to": None,
                "geography_type": "point",
                "geography_id": frame.get("complex_id"),
                "measure_type": "administrative_record",
                "confidence": 0.98,
                "method_version": "0.1.0",
            }
        )
        conn.execute("DELETE FROM transit_stop WHERE source_id=?", [source_id])
        conn.register("_stations", transit)
        conn.execute("INSERT INTO transit_stop BY NAME SELECT * FROM _stations")
        conn.unregister("_stations")
        register_standardized_records(
            conn,
            transit,
            entity_type="transit_stop",
            entity_id_column="transit_stop_id",
            source_id=source_id,
            snapshot_id=snapshot.snapshot_id,
            source_record_id_column="source_stop_id",
        )
        transit.to_parquet(standardized_path(source_id, "current", "stations.parquet"), index=False)
        finish_run(conn, run_id, "SUCCEEDED", request_count=2, downloaded_bytes=snapshot.byte_size, inserted_records=len(transit))
        return {"run_id": run_id, "snapshot_id": snapshot.snapshot_id, "stations": len(transit), "raw_bytes": snapshot.byte_size}
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise


def refresh_subway_entrances(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "mta_subway_entrances_2024"
    run_id = begin_run(conn, source_id, "refresh 2024 entrance and exit inventory")
    try:
        metadata = fetch_metadata_snapshot(conn, source_id, "2024", run_id)
        snapshot = download_socrata_csv(conn, source_id, "2024", run_id, limit=10_000)
        frame = pd.read_csv(snapshot.path, dtype=str)
        required = {
            "complex_id",
            "station_id",
            "gtfs_stop_id",
            "stop_name",
            "borough",
            "daytime_routes",
            "entrance_type",
            "entry_allowed",
            "exit_allowed",
            "entrance_latitude",
            "entrance_longitude",
        }
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError(f"MTA entrance inventory is missing columns: {', '.join(missing)}")
        exact_duplicate_rows = int(frame.duplicated().sum())
        frame = frame.drop_duplicates().reset_index(drop=True)
        longitude = as_number(frame["entrance_longitude"])
        latitude = as_number(frame["entrance_latitude"])
        west, east, south, north = NYC_BOUNDS
        invalid_coordinates = longitude.isna() | latitude.isna() | ~longitude.between(west, east) | ~latitude.between(south, north)
        if invalid_coordinates.any():
            raise ValueError(f"MTA entrance inventory has {int(invalid_coordinates.sum())} invalid coordinates")
        entry_allowed = frame["entry_allowed"].fillna("").str.upper().eq("YES")
        exit_allowed = frame["exit_allowed"].fillna("").str.upper().eq("YES")
        record_keys = frame.apply(
            lambda row: stable_id(
                "entrance",
                source_id,
                row.get("complex_id"),
                row.get("station_id"),
                row.get("gtfs_stop_id"),
                row.get("entrance_type"),
                row.get("entry_allowed"),
                row.get("exit_allowed"),
                row.get("entrance_latitude"),
                row.get("entrance_longitude"),
            ),
            axis=1,
        )
        if record_keys.duplicated().any():
            raise ValueError("MTA entrance identity is not unique after exact-row deduplication")
        transit = pd.DataFrame(
            {
                "transit_stop_id": record_keys,
                "source_stop_id": frame["gtfs_stop_id"],
                "complex_id": frame["complex_id"],
                "name": frame["stop_name"],
                "borough": frame["borough"],
                "routes_json": frame["daytime_routes"].fillna("").map(
                    lambda value: json_dumps(value.split() if value else [])
                ),
                "ada_status": None,
                "stop_kind": "subway_entrance_exit",
                "entrance_type": frame["entrance_type"],
                "entry_allowed": entry_allowed,
                "exit_allowed": exit_allowed,
                "longitude": longitude,
                "latitude": latitude,
                "source_id": source_id,
                "snapshot_id": snapshot.snapshot_id,
                "retrieved_at": snapshot.retrieved_at,
                "valid_from": date(2024, 1, 1),
                "valid_to": date(2024, 12, 31),
                "geography_type": "point",
                "geography_id": frame["complex_id"],
                "measure_type": "administrative_entrance_exit_record",
                "confidence": 0.96,
                "method_version": "0.1.0",
            }
        )
        warnings = [
            "A physical entrance can appear once per served station complex; source association rows are retained and catchment metrics deduplicate physical point/type/permission keys.",
            f"Removed {exact_duplicate_rows} fully exact duplicate source rows before standardization.",
        ]
        update_snapshot_details(conn, snapshot.snapshot_id, transit, warnings=warnings)
        conn.execute("DELETE FROM transit_stop WHERE source_id=?", [source_id])
        conn.register("_subway_entrances", transit)
        conn.execute("INSERT INTO transit_stop BY NAME SELECT * FROM _subway_entrances")
        conn.unregister("_subway_entrances")
        register_standardized_records(
            conn,
            transit,
            entity_type="transit_stop",
            entity_id_column="transit_stop_id",
            source_id=source_id,
            snapshot_id=snapshot.snapshot_id,
            source_record_id_column="transit_stop_id",
        )
        transit.to_parquet(
            standardized_path(source_id, "2024", "subway_entrances_exits.parquet"),
            index=False,
        )
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=2,
            downloaded_bytes=metadata.byte_size + snapshot.byte_size,
            inserted_records=len(transit),
            deleted_records=exact_duplicate_rows,
            warnings=warnings,
        )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "entrance_association_rows": len(transit),
            "exact_duplicates_removed": exact_duplicate_rows,
            "raw_bytes": metadata.byte_size + snapshot.byte_size,
        }
    except Exception as exc:
        try:
            conn.unregister("_subway_entrances")
        except Exception:
            pass
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise


def refresh_hourly_ridership(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "mta_hourly_2025"
    run_id = begin_run(conn, source_id, "refresh")
    request_count = 0
    downloaded_bytes = 0
    partition_snapshots: list[Snapshot] = []
    reused_partitions = 0
    try:
        metadata_snapshot = fetch_metadata_snapshot(conn, source_id, str(MTA_RIDERSHIP_YEAR), run_id)
        request_count += 1
        downloaded_bytes += metadata_snapshot.byte_size
        select = (
            "station_complex_id,station_complex,borough,latitude,longitude,"
            "date_extract_dow(transit_timestamp) as day_of_week,"
            "date_extract_hh(transit_timestamp) as hour_of_day,"
            "sum(ridership) as ridership,sum(transfers) as transfers"
        )
        group = (
            "station_complex_id,station_complex,borough,latitude,longitude,"
            "date_extract_dow(transit_timestamp),date_extract_hh(transit_timestamp)"
        )
        for start, end_exclusive in _bounded_date_windows(MTA_RIDERSHIP_YEAR):
            partition = _reusable_partition_snapshot(
                conn, source_id, MTA_RIDERSHIP_YEAR, start, end_exclusive
            )
            if partition is None:
                partition = download_socrata_csv(
                    conn,
                    source_id,
                    _partition_vintage(MTA_RIDERSHIP_YEAR, start),
                    run_id,
                    select=select,
                    where=(
                        f"transit_timestamp >= '{start.isoformat()}T00:00:00.000' "
                        f"and transit_timestamp < '{end_exclusive.isoformat()}T00:00:00.000'"
                    ),
                    group=group,
                    limit=MTA_PARTITION_LIMIT,
                    valid_from=start,
                    valid_to=end_exclusive - timedelta(days=1),
                )
                request_count += 1
                downloaded_bytes += partition.byte_size
                partition_frame = pd.read_csv(partition.path, dtype=str, low_memory=False)
                if len(partition_frame) >= MTA_PARTITION_LIMIT:
                    raise ValueError(f"MTA partition reached the API limit for {start}")
                update_snapshot_details(conn, partition.snapshot_id, partition_frame)
            else:
                reused_partitions += 1
            partition_snapshots.append(partition)

        frame = _aggregate_ridership_partitions([item.path for item in partition_snapshots])
        published_complexes = conn.execute(
            """
            SELECT count(DISTINCT complex_id)
            FROM transit_stop
            WHERE source_id='mta_subway_stations' AND complex_id IS NOT NULL
            """
        ).fetchone()[0]
        ridership_complexes = int(frame["station_complex_id"].nunique())
        station_coverage = ridership_complexes / published_complexes if published_complexes else None
        if station_coverage is not None and station_coverage < 0.95:
            raise ValueError(
                f"MTA ridership complex coverage is below 95%: {ridership_complexes}/{published_complexes}"
            )
        combined_bytes = frame.to_csv(index=False).encode("utf-8")
        snapshot = save_bytes_snapshot(
            conn,
            source_id=source_id,
            vintage=str(MTA_RIDERSHIP_YEAR),
            filename="station_hour_of_week_2025.csv",
            content=combined_bytes,
            source_url="https://data.ny.gov/resource/5wq4-mkjj.csv",
            run_id=run_id,
            content_type="text/csv",
            query={
                "method": "local sum of bounded seven-day server-side aggregates",
                "partition_version": MTA_PARTITION_VERSION,
                "partition_snapshot_ids": [item.snapshot_id for item in partition_snapshots],
                "group": group,
                "select": select,
            },
            valid_from=date(MTA_RIDERSHIP_YEAR, 1, 1),
            valid_to=date(MTA_RIDERSHIP_YEAR, 12, 31),
            warnings=[
                "MTA publishes ridership estimates and can revise historical records.",
                "The local annual table is the sum of contiguous seven-day source aggregates.",
            ],
        )
        update_snapshot_details(conn, snapshot.snapshot_id, frame)
        day_num = pd.to_numeric(frame.get("day_of_week"), errors="coerce")
        day_type = day_num.map(lambda value: "weekend" if value in {0, 6, 7} else "weekday")
        transit = pd.DataFrame(
            {
                "transit_ridership_id": frame.apply(
                    lambda row: stable_id("ride", source_id, row.get("station_complex_id"), row.get("day_of_week"), row.get("hour_of_day")), axis=1
                ),
                "complex_id": frame.get("station_complex_id"),
                "period_start": pd.Timestamp(f"{MTA_RIDERSHIP_YEAR}-01-01"),
                "period_end": pd.Timestamp(f"{MTA_RIDERSHIP_YEAR}-12-31 23:59:59"),
                "hour_of_day": pd.to_numeric(frame.get("hour_of_day"), errors="coerce").astype("Int64"),
                "day_type": day_type,
                "ridership": as_number(frame.get("ridership")),
                "transfers": as_number(frame.get("transfers")),
                "source_id": source_id,
                "snapshot_id": snapshot.snapshot_id,
                "retrieved_at": snapshot.retrieved_at,
                "valid_from": date(MTA_RIDERSHIP_YEAR, 1, 1),
                "valid_to": date(MTA_RIDERSHIP_YEAR, 12, 31),
                "geography_type": "station_complex",
                "geography_id": frame.get("station_complex_id"),
                "measure_type": "administrative_estimate",
                "confidence": 0.9,
                "method_version": "0.2.0",
            }
        )
        previous_records = conn.execute(
            "SELECT count(*) FROM transit_ridership WHERE source_id=?", [source_id]
        ).fetchone()[0]
        conn.execute("DELETE FROM transit_ridership WHERE source_id=?", [source_id])
        conn.register("_ridership", transit)
        conn.execute("INSERT INTO transit_ridership BY NAME SELECT * FROM _ridership")
        conn.unregister("_ridership")
        register_table_lineage(
            conn,
            table_name="transit_ridership",
            entity_type="transit_ridership",
            id_column="transit_ridership_id",
            source_id=source_id,
            snapshot_id=snapshot.snapshot_id,
        )
        transit.to_parquet(
            standardized_path(source_id, str(MTA_RIDERSHIP_YEAR), "station_hour_of_week.parquet"),
            index=False,
        )
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            inserted_records=len(transit),
            deleted_records=previous_records,
            warnings=[
                f"Used {len(partition_snapshots)} bounded partitions; reused {reused_partitions} completed partitions.",
                "MTA describes ridership as estimates and may revise history.",
            ],
        )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "partition_snapshot_ids": [item.snapshot_id for item in partition_snapshots],
            "partitions": len(partition_snapshots),
            "reused_partitions": reused_partitions,
            "station_complexes": ridership_complexes,
            "published_station_complexes": published_complexes,
            "station_coverage": station_coverage,
            "station_hour_rows": len(transit),
            "annual_ridership": float(frame["ridership"].sum()),
            "downloaded_bytes_this_run": downloaded_bytes,
            "raw_partition_bytes": sum(item.byte_size for item in partition_snapshots),
        }
    except Exception as exc:
        finish_run(
            conn,
            run_id,
            "FAILED",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            error={"type": type(exc).__name__, "message": str(exc)},
            warnings=[f"Completed partitions remain reusable: {len(partition_snapshots)}"],
        )
        raise
