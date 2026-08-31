from __future__ import annotations

from typing import Any

import duckdb
import pandas as pd
from shapely import from_wkt
from shapely.geometry import Point
from shapely.ops import transform

from ..spatial.catchments import analysis_crs_for_point


BUILDING_COLUMNS = [
    "building_id", "bin", "bbl", "name", "address", "min_longitude", "min_latitude",
    "max_longitude", "max_latitude", "geom_wkt", "attributes_json", "source_id", "snapshot_id",
    "retrieved_at", "valid_from", "valid_to", "geography_type", "geography_id", "measure_type",
    "confidence", "method_version",
]


def fetch_building_for_address(
    conn: duckdb.DuckDBPyConnection,
    address_record: dict[str, Any],
    parcel_record: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    is_nyc = bool(address_record.get("borough"))
    source_id = "nyc_building_footprints" if is_nyc else "overture_buildings"
    minimum_coverage = 1_000_000
    coverage = conn.execute(
        "SELECT count(*) FROM building WHERE source_id=? AND min_longitude IS NOT NULL",
        [source_id],
    ).fetchone()[0]
    if coverage < minimum_coverage:
        return None
    longitude = float(address_record["longitude"])
    latitude = float(address_record["latitude"])
    delta = 0.00075
    if parcel_record and parcel_record.get("geom_wkt"):
        parcel_geometry = from_wkt(parcel_record["geom_wkt"])
        west, south, east, north = parcel_geometry.bounds
    else:
        parcel_geometry = None
        west, south, east, north = (
            longitude - delta,
            latitude - delta,
            longitude + delta,
            latitude + delta,
        )
    candidates = conn.execute(
        f"""
        SELECT {','.join(BUILDING_COLUMNS)}
        FROM building
        WHERE source_id=?
          AND max_longitude >= ? AND min_longitude <= ?
          AND max_latitude >= ? AND min_latitude <= ?
        """,
        [source_id, west - delta, east + delta, south - delta, north + delta],
    ).fetchdf()
    if candidates.empty:
        return None
    point = Point(longitude, latitude)
    from pyproj import Transformer

    project = Transformer.from_crs(
        "EPSG:4326",
        analysis_crs_for_point(longitude, latitude),
        always_xy=True,
    ).transform
    point_metric = transform(project, point)
    parcel_metric = transform(project, parcel_geometry) if parcel_geometry is not None else None
    target_bbl = str((parcel_record or {}).get("bbl") or "")
    ranked: list[tuple[int, float, int, float, int]] = []
    match_details: dict[int, tuple[str, float]] = {}
    for position, row in candidates.iterrows():
        geometry = from_wkt(row["geom_wkt"])
        geometry_metric = transform(project, geometry)
        same_bbl = int(bool(target_bbl) and str(row.get("bbl") or "") == target_bbl)
        contains_point = int(geometry.covers(point))
        overlap_ratio = 0.0
        if parcel_metric is not None and geometry_metric.area > 0:
            overlap_ratio = float(parcel_metric.intersection(geometry_metric).area / geometry_metric.area)
        distance = point_metric.distance(geometry_metric)
        ranked.append((-same_bbl, -overlap_ratio, -contains_point, distance, position))
        method = "exact_bbl" if same_bbl else ("parcel_geometry_overlap" if overlap_ratio > 0 else "nearest_geometry")
        match_details[position] = (method, overlap_ratio)
    selected_position = min(ranked)[4]
    selected = {
        key: None if pd.isna(value) else value
        for key, value in candidates.loc[selected_position].to_dict().items()
    }
    method, overlap_ratio = match_details[selected_position]
    selected["address_match_method"] = method
    selected["parcel_overlap_ratio"] = overlap_ratio
    return selected
