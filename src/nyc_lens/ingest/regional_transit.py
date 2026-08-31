from __future__ import annotations

import json
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from shapely import from_wkt, union_all
from shapely.geometry import Point

from ..config import get_source
from ..db import begin_run, finish_run
from ..provenance import Snapshot, download_snapshot
from ..util import json_dumps
from .common import (
    as_number,
    register_standardized_records,
    stable_id,
    standardized_path,
    update_snapshot_details,
)


def _gtfs_date(value: Any) -> date | None:
    parsed = pd.to_datetime(value, format="%Y%m%d", errors="coerce")
    return parsed.date() if pd.notna(parsed) else None


def _csa_union(conn: duckdb.DuckDBPyConnection):
    rows = conn.execute(
        """
        SELECT geom_wkt
        FROM geo_boundary
        WHERE source_id='tiger_county_2024'
          AND geography_type='county_equivalent_2024'
          AND geography_id IN (
              SELECT geography_id FROM region_member WHERE region_code='CSA408'
          )
        """
    ).fetchall()
    if len(rows) != 30:
        raise ValueError(f"Expected 30 official CSA 408 county equivalents, found {len(rows)}")
    return union_all(from_wkt([row[0] for row in rows]))


def _route_associations(
    archive: zipfile.ZipFile,
    stops: pd.DataFrame,
) -> tuple[dict[str, set[str]], pd.DataFrame]:
    routes = pd.read_csv(archive.open("routes.txt"), dtype=str, low_memory=False)
    trips = pd.read_csv(
        archive.open("trips.txt"),
        dtype=str,
        usecols=lambda column: column in {"trip_id", "route_id"},
        low_memory=False,
    )
    short = routes.get("route_short_name", pd.Series(index=routes.index, dtype="string")).fillna("").str.strip()
    long = routes.get("route_long_name", pd.Series(index=routes.index, dtype="string")).fillna("").str.strip()
    labels = short.where(short.ne(""), long)
    route_names = dict(zip(routes["route_id"].astype(str), labels.astype(str)))
    trip_routes = {
        str(trip_id): route_names.get(str(route_id), str(route_id))
        for trip_id, route_id in zip(trips["trip_id"], trips["route_id"])
    }
    stop_to_parent = {
        str(stop_id): (
            str(parent).strip()
            if pd.notna(parent) and str(parent).strip()
            else str(stop_id)
        )
        for stop_id, parent in zip(
            stops["stop_id"],
            stops.get("parent_station", pd.Series(index=stops.index, dtype="string")),
        )
    }
    associations: dict[str, set[str]] = {}
    with archive.open("stop_times.txt") as handle:
        for chunk in pd.read_csv(
            handle,
            dtype=str,
            usecols=lambda column: column in {"stop_id", "trip_id"},
            chunksize=500_000,
            low_memory=False,
        ):
            chunk["parent_stop_id"] = chunk["stop_id"].astype(str).map(stop_to_parent)
            chunk["route_name"] = chunk["trip_id"].astype(str).map(trip_routes)
            for stop_id, names in chunk.dropna(subset=["parent_stop_id", "route_name"]).groupby("parent_stop_id")["route_name"]:
                associations.setdefault(str(stop_id), set()).update(
                    str(value) for value in names if str(value).strip()
                )
    return associations, routes


def _feed_frame(
    snapshot: Snapshot,
    *,
    feed_key: str,
    csa_union,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    with zipfile.ZipFile(snapshot.path) as archive:
        required = {"stops.txt", "routes.txt", "trips.txt", "stop_times.txt"}
        missing = sorted(required - set(archive.namelist()))
        if missing:
            raise ValueError(f"MTA railroad feed {feed_key} is missing: {', '.join(missing)}")
        stops = pd.read_csv(archive.open("stops.txt"), dtype=str, low_memory=False)
        feed_info = (
            pd.read_csv(archive.open("feed_info.txt"), dtype=str, low_memory=False)
            if "feed_info.txt" in archive.namelist()
            else pd.DataFrame()
        )
        associations, routes = _route_associations(archive, stops)

    location_type = stops.get("location_type", pd.Series("0", index=stops.index)).fillna("0")
    parent = stops.loc[location_type.eq("1")].copy()
    if parent.empty:
        parent_values = stops.get("parent_station", pd.Series(index=stops.index, dtype="string"))
        parent = stops.loc[parent_values.isna() | parent_values.astype(str).str.strip().eq("")].copy()
    parent["longitude"] = as_number(parent.get("stop_lon", pd.Series(index=parent.index, dtype="string")))
    parent["latitude"] = as_number(parent.get("stop_lat", pd.Series(index=parent.index, dtype="string")))
    parent = parent.dropna(subset=["stop_id", "stop_name", "longitude", "latitude"]).copy()
    parent = parent.loc[
        [
            csa_union.covers(Point(float(lon), float(lat)))
            for lon, lat in zip(parent["longitude"], parent["latitude"])
        ]
    ].copy()
    if parent.empty:
        raise ValueError(f"MTA railroad feed {feed_key} has no parent stations inside CSA 408")

    valid_from = None
    valid_to = None
    if not feed_info.empty:
        start_values = feed_info.get("feed_start_date", pd.Series(dtype="string")).map(_gtfs_date).dropna()
        end_values = feed_info.get("feed_end_date", pd.Series(dtype="string")).map(_gtfs_date).dropna()
        valid_from = min(start_values) if not start_values.empty else None
        valid_to = max(end_values) if not end_values.empty else None

    source_id = "regional_mta_rail_gtfs_static"
    source_stop_ids = parent["stop_id"].astype(str)
    composite_ids = source_stop_ids.map(lambda value: f"{feed_key}:{value}")
    transit = pd.DataFrame(
        {
            "transit_stop_id": composite_ids.map(lambda value: stable_id("stop", source_id, value)),
            "source_stop_id": composite_ids,
            "complex_id": composite_ids,
            "name": parent["stop_name"].astype(str),
            "borough": None,
            "routes_json": source_stop_ids.map(
                lambda value: json_dumps(sorted(associations.get(value, set())))
            ),
            "ada_status": parent.get("wheelchair_boarding"),
            "stop_kind": "commuter_rail_station",
            "entrance_type": None,
            "entry_allowed": None,
            "exit_allowed": None,
            "longitude": parent["longitude"],
            "latitude": parent["latitude"],
            "source_id": source_id,
            "snapshot_id": snapshot.snapshot_id,
            "retrieved_at": snapshot.retrieved_at,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "geography_type": "point",
            "geography_id": composite_ids,
            "measure_type": "scheduled_transit_record",
            "confidence": 0.95,
            "method_version": "0.2.0",
        }
    ).drop_duplicates("transit_stop_id", keep="last")
    stats = {
        "feed_key": feed_key,
        "raw_stops": len(stops),
        "raw_routes": len(routes),
        "csa_parent_stations": len(transit),
        "valid_from": valid_from,
        "valid_to": valid_to,
    }
    return transit, stats


def refresh_regional_mta_rail_gtfs(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "regional_mta_rail_gtfs_static"
    source = get_source(source_id)
    run_id = begin_run(conn, source_id, "refresh current LIRR and Metro-North GTFS parent stations inside CSA 408")
    request_count = 0
    downloaded_bytes = 0
    snapshots: list[Snapshot] = []
    frames: list[pd.DataFrame] = []
    feed_stats: list[dict[str, Any]] = []
    try:
        csa_union = _csa_union(conn)
        for feed in source["feeds"]:
            snapshot = download_snapshot(
                conn,
                source_id=source_id,
                vintage=f"current-{feed['key']}",
                url=feed["url"],
                filename=f"gtfs_{feed['key']}.zip",
                run_id=run_id,
                timeout_seconds=300,
            )
            request_count += 1
            downloaded_bytes += snapshot.byte_size
            snapshots.append(snapshot)
            frame, stats = _feed_frame(snapshot, feed_key=feed["key"], csa_union=csa_union)
            frames.append(frame)
            feed_stats.append(stats)
            update_snapshot_details(
                conn,
                snapshot.snapshot_id,
                frame,
                warnings=[
                    "Static GTFS represents scheduled service, not observed arrivals, reliability, ridership, or unique passengers.",
                    "Only parent station points within the official 2023 CSA 408 county-equivalent union are standardized.",
                    "MTA logos, maps, symbols, and other brand assets are not retained.",
                ],
            )
            frame.to_parquet(
                standardized_path(source_id, f"current-{feed['key']}", "parent_stations.parquet"),
                index=False,
            )

        combined = pd.concat(frames, ignore_index=True).drop_duplicates("transit_stop_id", keep="last")
        conn.execute("DELETE FROM transit_stop WHERE source_id=?", [source_id])
        conn.register("_regional_rail_stops", combined)
        conn.execute("INSERT INTO transit_stop BY NAME SELECT * FROM _regional_rail_stops")
        conn.unregister("_regional_rail_stops")
        for snapshot, frame in zip(snapshots, frames):
            register_standardized_records(
                conn,
                frame,
                entity_type="transit_stop",
                entity_id_column="transit_stop_id",
                source_id=source_id,
                snapshot_id=snapshot.snapshot_id,
                source_record_id_column="source_stop_id",
            )
        status = "SUCCEEDED_WITH_WARNINGS"
        warnings = [
            "NJT Port Jervis/Pascack Valley, PATH, and CTtransit feeds are not silently substituted; their credential or license gates are recorded separately.",
            "Coverage outside MTA railroad territory is supplemented only by a disclosed Overture transit-place proxy.",
        ]
        finish_run(
            conn,
            run_id,
            status,
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            inserted_records=len(combined),
            warnings=warnings,
        )
        return {
            "run_id": run_id,
            "status": status,
            "snapshot_ids": [snapshot.snapshot_id for snapshot in snapshots],
            "raw_bytes": downloaded_bytes,
            "parent_stations": len(combined),
            "feed_stats": feed_stats,
            "warnings": warnings,
        }
    except Exception as exc:
        try:
            conn.unregister("_regional_rail_stops")
        except Exception:
            pass
        finish_run(
            conn,
            run_id,
            "FAILED",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        raise
