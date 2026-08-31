from __future__ import annotations

import json
from typing import Any

import duckdb
import httpx
import pandas as pd
from pyproj import Transformer
from shapely import from_wkt
from shapely.geometry import Point, shape
from shapely.ops import transform

from ..db import begin_run, finish_run
from ..provenance import save_bytes_snapshot
from ..util import new_id, utc_now
from ..ingest.common import register_table_lineage


FEATURE_SERVICE = "https://services5.arcgis.com/GfwWNkhOj9bNBqoJ/arcgis/rest/services/MAPPLUTO/FeatureServer/0/query"
OUT_FIELDS = (
    "Borough,Block,Lot,Address,ZoneDist1,BldgClass,LandUse,LotArea,BldgArea,"
    "ResArea,OfficeArea,RetailArea,UnitsRes,UnitsTotal,YearBuilt,BBL,Version"
)
LOCAL_PARCEL_COLUMNS = [
    "parcel_id", "bbl", "borough", "block", "lot", "address", "land_use", "zoning",
    "building_class", "lot_area_sqft", "building_area_sqft", "residential_area_sqft",
    "office_area_sqft", "retail_area_sqft", "units_residential", "units_total", "year_built",
    "min_longitude", "min_latitude", "max_longitude", "max_latitude", "geom_wkt", "source_id",
    "snapshot_id", "retrieved_at", "valid_from", "valid_to", "geography_type", "geography_id",
    "measure_type", "confidence", "method_version",
]


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _local_parcel_for_address(
    conn: duckdb.DuckDBPyConnection,
    address_record: dict[str, Any],
) -> dict[str, Any] | None:
    coverage = conn.execute(
        "SELECT count(*) FROM parcel WHERE source_id='mappluto_26v2' AND min_longitude IS NOT NULL"
    ).fetchone()[0]
    if coverage < 800_000:
        return None
    longitude = float(address_record["longitude"])
    latitude = float(address_record["latitude"])
    delta = 0.00075
    candidates = conn.execute(
        f"""
        SELECT {','.join(LOCAL_PARCEL_COLUMNS)}
        FROM parcel
        WHERE source_id='mappluto_26v2'
          AND max_longitude >= ? AND min_longitude <= ?
          AND max_latitude >= ? AND min_latitude <= ?
        """,
        [longitude - delta, longitude + delta, latitude - delta, latitude + delta],
    ).fetchdf()
    if candidates.empty:
        return None
    source_text = (address_record.get("standardized_address") or "").split(",", 1)[0].upper().strip()
    project = Transformer.from_crs("EPSG:4326", "EPSG:32118", always_xy=True).transform
    point_metric = transform(project, Point(longitude, latitude))
    ranked: list[tuple[int, float, int]] = []
    for position, row in candidates.iterrows():
        geometry = from_wkt(row["geom_wkt"])
        exact = int(str(row.get("address") or "").upper().strip() == source_text)
        ranked.append((-exact, point_metric.distance(transform(project, geometry)), position))
    selected_position = min(ranked)[2]
    selected = {
        key: None if pd.isna(value) else value
        for key, value in candidates.loc[selected_position].to_dict().items()
    }
    conn.execute(
        "UPDATE address SET bbl=? WHERE address_id=?",
        [selected["bbl"], address_record["address_id"]],
    )
    return selected


def fetch_parcel_for_address(
    conn: duckdb.DuckDBPyConnection,
    address_record: dict[str, Any],
) -> dict[str, Any] | None:
    if address_record.get("county_geoid") not in {"36005", "36047", "36061", "36081", "36085"}:
        # Regional parcel systems are fragmented and are not silently replaced
        # by a non-authoritative building compilation.
        return None
    local = _local_parcel_for_address(conn, address_record)
    if local is not None:
        return local
    source_id = "mappluto_26v2"
    run_id = begin_run(conn, source_id, "parcel spatial join")
    longitude = float(address_record["longitude"])
    latitude = float(address_record["latitude"])
    delta = 0.00075
    params = {
        "where": "1=1",
        "geometry": f"{longitude-delta},{latitude-delta},{longitude+delta},{latitude+delta}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": OUT_FIELDS,
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }
    try:
        response = httpx.get(FEATURE_SERVICE, params=params, timeout=90, follow_redirects=True, headers={"User-Agent": "NYC-Lens/0.1"})
        response.raise_for_status()
        snapshot = save_bytes_snapshot(
            conn,
            source_id=source_id,
            vintage="26v2",
            filename=f"parcel_query_{address_record['address_id']}.geojson",
            content=response.content,
            source_url=str(response.url),
            run_id=run_id,
            content_type="application/geo+json",
            query=params,
            http_status=response.status_code,
        )
        features = response.json().get("features", [])
        if not features:
            finish_run(conn, run_id, "SUCCEEDED_WITH_WARNINGS", request_count=1, downloaded_bytes=snapshot.byte_size, warnings=["No parcel found near matched address coordinate."])
            return None
        source_text = (address_record.get("standardized_address") or "").split(",", 1)[0].upper().strip()
        project = Transformer.from_crs("EPSG:4326", "EPSG:32118", always_xy=True).transform
        point_metric = transform(project, Point(longitude, latitude))

        def rank(feature: dict[str, Any]) -> tuple[int, float]:
            properties = feature.get("properties", {})
            exact = int((properties.get("Address") or "").upper().strip() == source_text)
            geometry_metric = transform(project, shape(feature["geometry"]))
            return (-exact, point_metric.distance(geometry_metric))

        features = sorted(features, key=rank)
        selected: dict[str, Any] | None = None
        for feature in features:
            properties = feature.get("properties", {})
            geometry = shape(feature["geometry"])
            if not geometry.is_valid:
                geometry = geometry.buffer(0)
            bbl = str(properties.get("BBL") or "").split(".")[0]
            if not bbl:
                continue
            parcel_id = f"parcel_{bbl}"
            record = {
                "parcel_id": parcel_id,
                "bbl": bbl,
                "borough": properties.get("Borough"),
                "block": str(properties.get("Block") or ""),
                "lot": str(properties.get("Lot") or ""),
                "address": properties.get("Address"),
                "land_use": properties.get("LandUse"),
                "zoning": properties.get("ZoneDist1"),
                "building_class": properties.get("BldgClass"),
                "lot_area_sqft": _number(properties.get("LotArea")),
                "building_area_sqft": _number(properties.get("BldgArea")),
                "residential_area_sqft": _number(properties.get("ResArea")),
                "office_area_sqft": _number(properties.get("OfficeArea")),
                "retail_area_sqft": _number(properties.get("RetailArea")),
                "units_residential": _number(properties.get("UnitsRes")),
                "units_total": _number(properties.get("UnitsTotal")),
                "year_built": _int(properties.get("YearBuilt")),
                "min_longitude": geometry.bounds[0],
                "min_latitude": geometry.bounds[1],
                "max_longitude": geometry.bounds[2],
                "max_latitude": geometry.bounds[3],
                "geom_wkt": geometry.wkt,
                "source_id": source_id,
                "snapshot_id": snapshot.snapshot_id,
                "retrieved_at": snapshot.retrieved_at,
                "valid_from": None,
                "valid_to": None,
                "geography_type": "tax_lot",
                "geography_id": bbl,
                "measure_type": "administrative_record",
                "confidence": 0.98,
                "method_version": "0.1.0",
            }
            conn.execute("DELETE FROM parcel WHERE parcel_id=?", [parcel_id])
            conn.execute(
                "INSERT INTO parcel BY NAME SELECT " + ",".join(f"? AS {column}" for column in record),
                list(record.values()),
            )
            if feature is features[0]:
                selected = record
        if selected:
            conn.execute(
                "UPDATE address SET bbl=? WHERE address_id=?",
                [selected["bbl"], address_record["address_id"]],
            )
            register_table_lineage(
                conn,
                table_name="parcel",
                entity_type="parcel",
                id_column="parcel_id",
                source_id=source_id,
                snapshot_id=snapshot.snapshot_id,
            )
        finish_run(conn, run_id, "SUCCEEDED", request_count=1, downloaded_bytes=snapshot.byte_size, inserted_records=len(features))
        return selected
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise
