from __future__ import annotations

import re
from datetime import date
from typing import Any

import duckdb
import pandas as pd
from pyproj import Transformer
from shapely import from_wkt

from ..db import begin_run, finish_run
from ..util import utc_now
from .common import (
    as_number,
    register_standardized_records,
    stable_id,
    standardized_path,
    update_snapshot_details,
)
from .socrata import download_socrata_csv, fetch_metadata_snapshot


STATE_PLANE_TO_WGS84 = Transformer.from_crs("EPSG:2263", "EPSG:4326", always_xy=True)
PED_MONTHS = {"may": 5, "june": 6, "sept": 9, "oct": 10}


def _state_plane_points(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    points = series.fillna("").map(
        lambda value: from_wkt(value) if isinstance(value, str) and value.upper().startswith("POINT") else None
    )
    coordinates = points.map(
        lambda point: STATE_PLANE_TO_WGS84.transform(point.x, point.y)
        if point is not None and not point.is_empty
        else (None, None)
    )
    longitude = coordinates.map(lambda value: value[0])
    latitude = coordinates.map(lambda value: value[1])
    valid = longitude.between(-74.25909, -73.700272) & latitude.between(40.477399, 40.917577)
    return longitude.where(valid), latitude.where(valid)


def _latest_pedestrian_fields(columns: list[str]) -> tuple[int, int, dict[str, str]]:
    candidates: list[tuple[int, int, str, str]] = []
    pattern = re.compile(r"^(may|june|sept|oct)_?(\d{2})_(am|md|pm|p_m)$", re.IGNORECASE)
    for column in columns:
        match = pattern.match(column)
        if not match:
            continue
        month_name, year_text, period = match.groups()
        candidates.append((2000 + int(year_text), PED_MONTHS[month_name.lower()], period.lower().replace("p_m", "pm"), column))
    if not candidates:
        raise ValueError("No recognizable seasonal pedestrian count fields")
    latest_year, latest_month = max((year, month) for year, month, _, _ in candidates)
    fields = {
        period: column
        for year, month, period, column in candidates
        if year == latest_year and month == latest_month
    }
    if set(fields) != {"am", "md", "pm"}:
        raise ValueError(f"Latest pedestrian period is incomplete: {latest_year}-{latest_month:02d} {fields}")
    return latest_year, latest_month, fields


def _wgs84_point_coordinates(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    points = series.fillna("").map(
        lambda value: from_wkt(value) if isinstance(value, str) and value.upper().startswith("POINT") else None
    )
    longitude = points.map(lambda point: point.x if point is not None and not point.is_empty else None)
    latitude = points.map(lambda point: point.y if point is not None and not point.is_empty else None)
    valid = longitude.between(-74.25909, -73.700272) & latitude.between(40.477399, 40.917577)
    return longitude.where(valid), latitude.where(valid)


def _link_centroid(value: Any) -> tuple[float | None, float | None]:
    coordinates: list[tuple[float, float]] = []
    for token in str(value or "").split():
        try:
            latitude_text, longitude_text = token.split(",", maxsplit=1)
            coordinates.append((float(longitude_text), float(latitude_text)))
        except (TypeError, ValueError):
            continue
    if not coordinates:
        return None, None
    longitude = sum(item[0] for item in coordinates) / len(coordinates)
    latitude = sum(item[1] for item in coordinates) / len(coordinates)
    if not (-74.25909 <= longitude <= -73.700272 and 40.477399 <= latitude <= 40.917577):
        return None, None
    return longitude, latitude


def _replace_traffic_observations(
    conn: duckdb.DuckDBPyConnection,
    source_id: str,
    records: pd.DataFrame,
    snapshot_id: str,
) -> None:
    conn.execute("DELETE FROM traffic_observation WHERE source_id=?", [source_id])
    conn.register("_traffic_observations", records)
    conn.execute("INSERT INTO traffic_observation BY NAME SELECT * FROM _traffic_observations")
    conn.unregister("_traffic_observations")
    register_standardized_records(
        conn,
        records,
        entity_type="traffic_observation",
        entity_id_column="traffic_observation_id",
        source_id=source_id,
        snapshot_id=snapshot_id,
    )


def refresh_traffic_counts(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "nyc_dot_traffic_counts"
    run_id = begin_run(conn, source_id, "refresh 2025 onward volume counts")
    try:
        metadata = fetch_metadata_snapshot(conn, source_id, "2025-present", run_id)
        select = "requestid,boro,yr,m,d,hh,mm,vol,segmentid,wktgeom,street,fromst,tost,direction"
        snapshot = download_socrata_csv(
            conn,
            source_id,
            "2025-present",
            run_id,
            select=select,
            where="yr >= 2025",
            order="yr,m,d,hh,mm,requestid,segmentid",
            limit=2_000_000,
        )
        frame = pd.read_csv(snapshot.path, dtype=str, keep_default_na=True, low_memory=False)
        update_snapshot_details(conn, snapshot.snapshot_id, frame)
        longitude, latitude = _state_plane_points(frame.get("wktgeom", pd.Series(index=frame.index, dtype=str)))
        observed_at = pd.to_datetime(
            {
                "year": as_number(frame["yr"]),
                "month": as_number(frame["m"]),
                "day": as_number(frame["d"]),
                "hour": as_number(frame["hh"]),
                "minute": as_number(frame["mm"]),
            },
            errors="coerce",
        )
        values = as_number(frame["vol"])
        records = pd.DataFrame(
            {
                "traffic_observation_id": [
                    stable_id("traffic", source_id, request, segment, timestamp, direction)
                    for request, segment, timestamp, direction in zip(
                        frame["requestid"], frame["segmentid"], observed_at, frame["direction"]
                    )
                ],
                "observation_type": "vehicle_volume_15min",
                "observed_at": observed_at,
                "value": values,
                "unit": "vehicles_per_15_minutes",
                "longitude": longitude,
                "latitude": latitude,
                "source_id": source_id,
                "snapshot_id": snapshot.snapshot_id,
                "retrieved_at": snapshot.retrieved_at,
                "valid_from": observed_at.dt.date,
                "valid_to": observed_at.dt.date,
                "geography_type": "road_segment",
                "geography_id": frame["segmentid"],
                "measure_type": "observed_traffic_count",
                "confidence": longitude.notna().map({True: 0.95, False: 0.75}),
                "method_version": "0.1.0",
            }
        )
        records = records.drop_duplicates("traffic_observation_id", keep="last")
        _replace_traffic_observations(conn, source_id, records, snapshot.snapshot_id)
        period_start = observed_at.min()
        period_end = observed_at.max()
        summaries = records.groupby("geography_id", as_index=False).agg(
            value=("value", "median"),
            longitude=("longitude", "first"),
            latitude=("latitude", "first"),
            confidence=("confidence", "mean"),
        )
        summaries["mobility_proxy_id"] = summaries["geography_id"].map(
            lambda value: stable_id("mobility", source_id, value, "median_vehicle_volume_15min")
        )
        summaries["proxy_type"] = "median_vehicle_volume_15min"
        summaries["unit"] = "vehicles_per_15_minutes"
        summaries["period"] = f"{period_start} to {period_end}"
        summaries["source_id"] = source_id
        summaries["snapshot_id"] = snapshot.snapshot_id
        summaries["retrieved_at"] = snapshot.retrieved_at
        summaries["valid_from"] = period_start.date() if pd.notna(period_start) else None
        summaries["valid_to"] = period_end.date() if pd.notna(period_end) else None
        summaries["geography_type"] = "road_segment"
        summaries["measure_type"] = "derived_observation_summary"
        summaries["method_version"] = "0.1.0"
        summaries = summaries[
            [
                "mobility_proxy_id",
                "proxy_type",
                "value",
                "unit",
                "period",
                "longitude",
                "latitude",
                "source_id",
                "snapshot_id",
                "retrieved_at",
                "valid_from",
                "valid_to",
                "geography_type",
                "geography_id",
                "measure_type",
                "confidence",
                "method_version",
            ]
        ]
        conn.execute("DELETE FROM mobility_proxy WHERE source_id=?", [source_id])
        conn.register("_traffic_volume_summaries", summaries)
        conn.execute("INSERT INTO mobility_proxy BY NAME SELECT * FROM _traffic_volume_summaries")
        conn.unregister("_traffic_volume_summaries")
        register_standardized_records(
            conn,
            summaries,
            entity_type="mobility_proxy",
            entity_id_column="mobility_proxy_id",
            source_id=source_id,
            snapshot_id=snapshot.snapshot_id,
        )
        records.to_parquet(standardized_path(source_id, "2025-present", "traffic_counts.parquet"), index=False)
        summaries.to_parquet(
            standardized_path(source_id, "2025-present", "road_segment_median_counts.parquet"),
            index=False,
        )
        finish_run(
            conn,
            run_id,
            "SUCCEEDED",
            request_count=2,
            downloaded_bytes=metadata.byte_size + snapshot.byte_size,
            inserted_records=len(records) + len(summaries),
        )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "observations": len(records),
            "segments": int(records["geography_id"].nunique()),
            "segment_summaries": len(summaries),
            "period_start": str(observed_at.min()),
            "period_end": str(observed_at.max()),
            "raw_bytes": metadata.byte_size + snapshot.byte_size,
        }
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise


def refresh_pedestrian_counts(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "nyc_dot_pedestrian_counts"
    run_id = begin_run(conn, source_id, "refresh latest seasonal pedestrian counts")
    try:
        metadata = fetch_metadata_snapshot(conn, source_id, "current", run_id)
        snapshot = download_socrata_csv(conn, source_id, "current", run_id, order="objectid", limit=10_000)
        frame = pd.read_csv(snapshot.path, dtype=str, keep_default_na=True, low_memory=False)
        year, month, fields = _latest_pedestrian_fields(frame.columns.tolist())
        update_snapshot_details(
            conn,
            snapshot.snapshot_id,
            frame,
            warnings=[f"Latest standardized seasonal fields: {year}-{month:02d} {fields}."],
        )
        longitude, latitude = _wgs84_point_coordinates(frame["the_geom"])
        rows: list[dict[str, Any]] = []
        hours = {"am": 8, "md": 12, "pm": 17}
        for period, column in sorted(fields.items()):
            values = as_number(frame[column])
            observed_at = pd.Timestamp(year=year, month=month, day=1, hour=hours[period])
            for index, row in frame.iterrows():
                rows.append(
                    {
                        "traffic_observation_id": stable_id(
                            "traffic", source_id, row.get("objectid"), column
                        ),
                        "observation_type": f"pedestrian_count_{period}",
                        "observed_at": observed_at,
                        "value": values.loc[index],
                        "unit": "pedestrians_per_published_count_period",
                        "longitude": longitude.loc[index],
                        "latitude": latitude.loc[index],
                        "source_id": source_id,
                        "snapshot_id": snapshot.snapshot_id,
                        "retrieved_at": snapshot.retrieved_at,
                        "valid_from": date(year, month, 1),
                        "valid_to": (pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd()).date(),
                        "geography_type": "pedestrian_count_site",
                        "geography_id": row.get("objectid"),
                        "measure_type": "published_pedestrian_count",
                        "confidence": 0.85,
                        "method_version": "0.1.0",
                    }
                )
        records = pd.DataFrame(rows)
        _replace_traffic_observations(conn, source_id, records, snapshot.snapshot_id)
        records.to_parquet(standardized_path(source_id, f"{year}-{month:02d}", "pedestrian_counts.parquet"), index=False)
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=2,
            downloaded_bytes=metadata.byte_size + snapshot.byte_size,
            inserted_records=len(records),
            warnings=[
                "Observed_at encodes the published month and AM/midday/PM band, not an exact field timestamp.",
                "The 114 fixed sites are a sparse corridor sample and do not represent every street.",
            ],
        )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "latest_period": f"{year}-{month:02d}",
            "sites": int(records["geography_id"].nunique()),
            "observations": len(records),
            "raw_bytes": metadata.byte_size + snapshot.byte_size,
        }
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise


def refresh_current_traffic_speed(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "nyc_dot_traffic_speed_current"
    run_id = begin_run(conn, source_id, "refresh latest monitored-link speeds")
    try:
        metadata = fetch_metadata_snapshot(conn, source_id, "current", run_id)
        maximum_snapshot = download_socrata_csv(
            conn,
            source_id,
            "current",
            run_id,
            select="max(data_as_of) as max_data_as_of",
            limit=1,
        )
        maximum_frame = pd.read_csv(maximum_snapshot.path, dtype=str)
        maximum_timestamp = maximum_frame.loc[0, "max_data_as_of"]
        if not maximum_timestamp:
            raise ValueError("Traffic speed source returned no maximum timestamp")
        maximum_datetime = pd.to_datetime(maximum_timestamp, errors="raise")
        window_start = maximum_datetime - pd.Timedelta(minutes=30)
        window_start_text = window_start.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        select = "id,speed,travel_time,status,data_as_of,link_id,link_points,borough,link_name"
        snapshot = download_socrata_csv(
            conn,
            source_id,
            "current",
            run_id,
            select=select,
            where=f"data_as_of >= '{window_start_text}'",
            order="data_as_of,link_id,id",
            limit=100_000,
        )
        frame = pd.read_csv(snapshot.path, dtype=str, keep_default_na=True, low_memory=False)
        window_rows = len(frame)
        frame["_observed_at"] = pd.to_datetime(frame["data_as_of"], errors="coerce")
        frame = frame.sort_values(["_observed_at", "id"]).drop_duplicates("link_id", keep="last").copy()
        update_snapshot_details(
            conn,
            snapshot.snapshot_id,
            frame,
            warnings=[
                f"Raw query returned {window_rows} rows from the latest 30-minute window; standardized to {len(frame)} latest-per-link rows."
            ],
        )
        coordinates = frame["link_points"].map(_link_centroid)
        longitude = coordinates.map(lambda value: value[0])
        latitude = coordinates.map(lambda value: value[1])
        values = as_number(frame["speed"])
        invalid_speed = (~values.between(0, 120)) & values.notna()
        values = values.where(~invalid_speed)
        observed_at = frame["_observed_at"]
        records = pd.DataFrame(
            {
                "traffic_observation_id": [
                    stable_id("traffic", source_id, link_id, timestamp)
                    for link_id, timestamp in zip(frame["link_id"], observed_at)
                ],
                "observation_type": "current_traffic_speed",
                "observed_at": observed_at,
                "value": values,
                "unit": "miles_per_hour",
                "longitude": longitude,
                "latitude": latitude,
                "source_id": source_id,
                "snapshot_id": snapshot.snapshot_id,
                "retrieved_at": snapshot.retrieved_at,
                "valid_from": observed_at.dt.date,
                "valid_to": observed_at.dt.date,
                "geography_type": "monitored_road_link",
                "geography_id": frame["link_id"],
                "measure_type": "real_time_sensor_estimate",
                "confidence": longitude.notna().map({True: 0.85, False: 0.6}),
                "method_version": "0.1.0",
            }
        ).drop_duplicates("traffic_observation_id", keep="last")
        _replace_traffic_observations(conn, source_id, records, snapshot.snapshot_id)
        records.to_parquet(standardized_path(source_id, "current", "latest_link_speeds.parquet"), index=False)
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=3,
            downloaded_bytes=metadata.byte_size + maximum_snapshot.byte_size + snapshot.byte_size,
            inserted_records=len(records),
            warnings=[
                "Only a latest-per-link snapshot was retained; the 111-million-row history was not mirrored.",
                "Each monitored link uses its own latest observation within 30 minutes of the source maximum timestamp.",
                "Link centroids are spatial proxies and coverage is concentrated on major arterials and highways.",
                f"Out-of-range speed values set to missing: {int(invalid_speed.sum())}.",
            ],
        )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "source_timestamp": maximum_timestamp,
            "window_start": window_start_text,
            "raw_window_rows": window_rows,
            "monitored_links": len(records),
            "links_with_coordinates": int(records["longitude"].notna().sum()),
            "invalid_speed_values": int(invalid_speed.sum()),
            "raw_bytes": metadata.byte_size + maximum_snapshot.byte_size + snapshot.byte_size,
        }
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise
