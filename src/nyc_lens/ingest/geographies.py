from __future__ import annotations

import json
from datetime import date
from typing import Any

import duckdb
import geopandas as gpd
import h3
import httpx
import pandas as pd
from shapely import from_wkt
from shapely.geometry import Polygon

from ..config import geographies_config, get_source
from ..db import begin_run, finish_run
from ..provenance import download_snapshot, save_bytes_snapshot
from ..publication import publication_transaction
from ..util import json_dumps
from .common import registered_frame, register_table_lineage, stable_id, standardized_path, update_snapshot_details
from .socrata import fetch_metadata_snapshot


NYC_GEOGRAPHY_DEFINITIONS: dict[str, dict[str, Any]] = {
    "nyc_census_blocks_2020": {
        "geography_type": "census_block",
        "id_field": "geoid",
        "name_field": "geoid",
        "parent": lambda row: str(row.get("geoid", ""))[:11],
        "minimum_rows": 35_000,
        "valid_from": date(2020, 1, 1),
    },
    "nyc_census_tracts_2020": {
        "geography_type": "tract",
        "id_field": "geoid",
        "name_field": "ctlabel",
        "parent": lambda row: str(row.get("geoid", ""))[:5],
        "minimum_rows": 2_000,
        "valid_from": date(2020, 1, 1),
    },
    "nyc_nta_2020": {
        "geography_type": "nta",
        "id_field": "nta2020",
        "name_field": "ntaname",
        "parent": lambda row: row.get("cdta2020"),
        "minimum_rows": 250,
        "valid_from": date(2020, 1, 1),
    },
    "nyc_cdta_2020": {
        "geography_type": "cdta",
        "id_field": "cdta2020",
        "name_field": "cdtaname",
        "parent": lambda row: row.get("countyfips"),
        "minimum_rows": 70,
        "valid_from": date(2020, 1, 1),
    },
}

BOROUGH_TO_COUNTY = {
    "1": ("36061", "New York County"),
    "2": ("36005", "Bronx County"),
    "3": ("36047", "Kings County"),
    "4": ("36081", "Queens County"),
    "5": ("36085", "Richmond County"),
}


def _prepare_geometry(frame: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, int, int]:
    if frame.crs is None:
        frame = frame.set_crs("EPSG:4326")
    else:
        frame = frame.to_crs("EPSG:4326")
    missing = int(frame.geometry.isna().sum())
    frame = frame.loc[frame.geometry.notna()].copy()
    invalid_mask = ~frame.geometry.is_valid
    invalid = int(invalid_mask.sum())
    if invalid:
        frame.loc[invalid_mask, "geometry"] = frame.loc[invalid_mask].geometry.make_valid()
    empty_mask = frame.geometry.is_empty
    empty = int(empty_mask.sum())
    if empty:
        frame = frame.loc[~empty_mask].copy()
    return frame, invalid, missing + empty


def _boundary_rows(
    frame: gpd.GeoDataFrame,
    *,
    source_id: str,
    snapshot_id: str,
    retrieved_at: Any,
    geography_type: str,
    id_field: str,
    name_field: str,
    parent: Any,
    valid_from: date | None,
) -> pd.DataFrame:
    metric = frame.to_crs(geographies_config()["analysis_crs"])
    records: list[dict[str, Any]] = []
    for position, (_, row) in enumerate(frame.iterrows()):
        geography_id = str(row.get(id_field, "")).strip()
        if not geography_id:
            continue
        records.append(
            {
                "boundary_id": stable_id("boundary", geography_type, geography_id),
                "geography_type": geography_type,
                "geography_id": geography_id,
                "name": str(row.get(name_field) or geography_id),
                "parent_id": parent(row) if callable(parent) else parent,
                "geom_wkt": row.geometry.wkt,
                "area_m2": float(metric.geometry.iloc[position].area),
                "source_id": source_id,
                "snapshot_id": snapshot_id,
                "retrieved_at": retrieved_at,
                "valid_from": valid_from,
                "valid_to": None,
                "measure_type": "administrative_boundary",
                "confidence": 1.0,
                "method_version": "0.1.0",
            }
        )
    return pd.DataFrame(records)


def _replace_boundaries(
    conn: duckdb.DuckDBPyConnection,
    source_id: str,
    boundaries: pd.DataFrame,
    snapshot_id: str,
) -> tuple[int, int]:
    with publication_transaction(conn):
        previous = conn.execute("SELECT count(*) FROM geo_boundary WHERE source_id=?", [source_id]).fetchone()[0]
        conn.execute("DELETE FROM geo_boundary WHERE source_id=?", [source_id])
        with registered_frame(conn, "_new_boundaries", boundaries):
            conn.execute("INSERT INTO geo_boundary BY NAME SELECT * FROM _new_boundaries")
        register_table_lineage(
            conn,
            table_name="geo_boundary",
            entity_type="geo_boundary",
            id_column="boundary_id",
            source_id=source_id,
            snapshot_id=snapshot_id,
        )
        inserted = conn.execute("SELECT count(*) FROM geo_boundary WHERE source_id=?", [source_id]).fetchone()[0]
        return previous, inserted


def refresh_nyc_geography(
    conn: duckdb.DuckDBPyConnection,
    source_id: str,
) -> dict[str, Any]:
    if source_id not in NYC_GEOGRAPHY_DEFINITIONS and source_id not in {
        "nyc_community_districts",
        "nyc_borough_boundaries",
    }:
        raise ValueError(f"Unsupported NYC geography source: {source_id}")
    source = get_source(source_id)
    run_id = begin_run(conn, source_id, "refresh")
    downloaded_bytes = 0
    try:
        metadata_snapshot = fetch_metadata_snapshot(conn, source_id, "current-metadata", run_id)
        downloaded_bytes += metadata_snapshot.byte_size
        snapshot = download_snapshot(
            conn,
            source_id=source_id,
            vintage="current",
            url=source["api_url"],
            filename=f"{source['dataset_id']}.geojson",
            run_id=run_id,
            params={"$limit": 100_000},
            timeout_seconds=300,
        )
        downloaded_bytes += snapshot.byte_size
        frame = gpd.read_file(snapshot.path)
        frame, invalid, removed = _prepare_geometry(frame)

        if source_id in NYC_GEOGRAPHY_DEFINITIONS:
            definition = NYC_GEOGRAPHY_DEFINITIONS[source_id]
            if len(frame) < definition["minimum_rows"]:
                raise ValueError(f"{source_id} coverage unexpectedly low: {len(frame)}")
            boundaries = _boundary_rows(
                frame,
                source_id=source_id,
                snapshot_id=snapshot.snapshot_id,
                retrieved_at=snapshot.retrieved_at,
                geography_type=definition["geography_type"],
                id_field=definition["id_field"],
                name_field=definition["name_field"],
                parent=definition["parent"],
                valid_from=definition["valid_from"],
            )
        elif source_id == "nyc_community_districts":
            metric = frame.to_crs(geographies_config()["analysis_crs"])
            records: list[dict[str, Any]] = []
            for position, (_, row) in enumerate(frame.iterrows()):
                code = str(int(float(row["boro_cd"])))
                district_number = int(code[-2:])
                geography_type = "community_district" if district_number <= 18 else "joint_interest_area"
                records.append(
                    {
                        "boundary_id": stable_id("boundary", geography_type, code),
                        "geography_type": geography_type,
                        "geography_id": code,
                        "name": f"Community District {code}" if geography_type == "community_district" else f"Joint Interest Area {code}",
                        "parent_id": code[0],
                        "geom_wkt": row.geometry.wkt,
                        "area_m2": float(metric.geometry.iloc[position].area),
                        "source_id": source_id,
                        "snapshot_id": snapshot.snapshot_id,
                        "retrieved_at": snapshot.retrieved_at,
                        "valid_from": None,
                        "valid_to": None,
                        "measure_type": "administrative_boundary",
                        "confidence": 1.0,
                        "method_version": "0.1.0",
                    }
                )
            boundaries = pd.DataFrame(records)
            if int(boundaries["geography_type"].eq("community_district").sum()) != 59:
                raise ValueError("Expected exactly 59 NYC community districts")
        else:
            metric = frame.to_crs(geographies_config()["analysis_crs"])
            records = []
            for position, (_, row) in enumerate(frame.iterrows()):
                boro_code = str(int(float(row["borocode"])))
                county_id, county_name = BOROUGH_TO_COUNTY[boro_code]
                common = {
                    "geom_wkt": row.geometry.wkt,
                    "area_m2": float(metric.geometry.iloc[position].area),
                    "source_id": source_id,
                    "snapshot_id": snapshot.snapshot_id,
                    "retrieved_at": snapshot.retrieved_at,
                    "valid_from": None,
                    "valid_to": None,
                    "measure_type": "administrative_boundary",
                    "confidence": 1.0,
                    "method_version": "0.1.0",
                }
                records.extend(
                    [
                        {
                            "boundary_id": stable_id("boundary", "borough", boro_code),
                            "geography_type": "borough",
                            "geography_id": boro_code,
                            "name": str(row["boroname"]),
                            "parent_id": "36",
                            **common,
                        },
                        {
                            "boundary_id": stable_id("boundary", "county", county_id),
                            "geography_type": "county",
                            "geography_id": county_id,
                            "name": county_name,
                            "parent_id": "36",
                            **common,
                        },
                    ]
                )
            boundaries = pd.DataFrame(records)
            if len(boundaries) != 10:
                raise ValueError(f"Expected five borough and five county boundaries, got {len(boundaries)}")

        if boundaries.empty or boundaries["boundary_id"].duplicated().any():
            raise ValueError(f"{source_id} produced empty or duplicate boundaries")
        output = standardized_path(source_id, snapshot.snapshot_id, f"{source['dataset_id']}.parquet")
        frame.to_parquet(output, index=False)
        with publication_transaction(conn):
            previous, inserted = _replace_boundaries(conn, source_id, boundaries, snapshot.snapshot_id)
            update_snapshot_details(
                conn,
                snapshot.snapshot_id,
                boundaries,
                warnings=[f"invalid_geometries_repaired={invalid}", f"empty_geometries_removed={removed}"],
            )
            finish_run(
                conn,
                run_id,
                "SUCCEEDED_WITH_WARNINGS" if invalid or removed else "SUCCEEDED",
                request_count=2,
                downloaded_bytes=downloaded_bytes,
                inserted_records=inserted,
                deleted_records=previous,
                warnings=[f"Repaired {invalid} invalid geometries; removed {removed} empty geometries."],
            )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "source_rows": len(frame),
            "boundary_rows": inserted,
            "geography_types": boundaries.groupby("geography_type").size().to_dict(),
            "invalid_geometries_repaired": invalid,
            "empty_geometries_removed": removed,
            "raw_bytes": downloaded_bytes,
            "standardized_parquet": str(output),
        }
    except Exception as exc:
        finish_run(
            conn,
            run_id,
            "FAILED",
            request_count=2 if downloaded_bytes else 0,
            downloaded_bytes=downloaded_bytes,
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        raise


def refresh_zcta(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "census_zcta_2020"
    source = get_source(source_id)
    run_id = begin_run(conn, source_id, "NYC intersecting extract")
    try:
        params = {
            "where": "1=1",
            "geometry": "-74.3,40.45,-73.65,40.95",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
        }
        response = httpx.post(
            source["api_url"],
            data=params,
            follow_redirects=True,
            timeout=180,
            headers={"User-Agent": "NYC-Lens/0.1 local-research"},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise RuntimeError(f"TIGERweb ZCTA error: {payload['error']}")
        snapshot = save_bytes_snapshot(
            conn,
            source_id=source_id,
            vintage="2020-current-service",
            filename="nyc_bbox_zcta.geojson",
            content=response.content,
            source_url=source["api_url"],
            run_id=run_id,
            content_type="application/geo+json",
            query=params,
            valid_from=date(2020, 1, 1),
            http_status=response.status_code,
            warnings=["Raw extract is a bounding-box superset; standardized rows intersect the official NYC borough union."],
        )
        frame = gpd.read_file(snapshot.path)
        frame, invalid, removed = _prepare_geometry(frame)
        borough_rows = conn.execute(
            "SELECT geom_wkt FROM geo_boundary WHERE source_id='nyc_borough_boundaries' AND geography_type='borough'"
        ).fetchall()
        if len(borough_rows) != 5:
            raise ValueError("Load the five NYC borough boundaries before refreshing ZCTA")
        borough_union = gpd.GeoSeries([from_wkt(row[0]) for row in borough_rows], crs="EPSG:4326").union_all()
        frame = frame.loc[frame.geometry.intersects(borough_union)].copy()
        if not 100 <= len(frame) <= 300:
            raise ValueError(f"NYC-intersecting ZCTA coverage is unexpected: {len(frame)}")
        boundaries = _boundary_rows(
            frame,
            source_id=source_id,
            snapshot_id=snapshot.snapshot_id,
            retrieved_at=snapshot.retrieved_at,
            geography_type="zcta",
            id_field="GEOID",
            name_field="NAME",
            parent=None,
            valid_from=date(2020, 1, 1),
        )
        output = standardized_path(source_id, snapshot.snapshot_id, "nyc_intersecting_zcta.parquet")
        frame.to_parquet(output, index=False)
        with publication_transaction(conn):
            previous, inserted = _replace_boundaries(conn, source_id, boundaries, snapshot.snapshot_id)
            update_snapshot_details(
                conn,
                snapshot.snapshot_id,
                boundaries,
                warnings=[
                    f"bounding_box_features={len(payload.get('features', []))}",
                    f"nyc_intersecting_features={inserted}",
                    "ZCTAs approximate ZIP delivery areas and are not USPS boundaries.",
                ],
            )
            finish_run(
                conn,
                run_id,
                "SUCCEEDED_WITH_WARNINGS",
                request_count=1,
                downloaded_bytes=snapshot.byte_size,
                inserted_records=inserted,
                deleted_records=previous,
                warnings=["ZCTAs are statistical ZIP approximations; the Raw bbox extract is filtered by the official NYC borough union."],
            )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "bbox_features": len(payload.get("features", [])),
            "nyc_intersecting_zcta": inserted,
            "raw_bytes": snapshot.byte_size,
            "standardized_parquet": str(output),
        }
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise


def refresh_h3_grid(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "derived_h3_grid"
    run_id = begin_run(conn, source_id, "deterministic rebuild")
    try:
        borough_rows = conn.execute(
            """
            SELECT geom_wkt, snapshot_id
            FROM geo_boundary
            WHERE source_id='nyc_borough_boundaries' AND geography_type='borough'
            ORDER BY geography_id
            """
        ).fetchall()
        if len(borough_rows) != 5:
            raise ValueError("Load the five NYC borough boundaries before building the H3 grid")
        borough_union = gpd.GeoSeries([from_wkt(row[0]) for row in borough_rows], crs="EPSG:4326").union_all()
        resolution = int(geographies_config()["h3_resolution"])
        cells = sorted(h3.geo_to_cells(borough_union.__geo_interface__, resolution))
        if len(cells) < 40_000:
            raise ValueError(f"H3 resolution {resolution} NYC coverage unexpectedly low: {len(cells)}")
        parent_snapshot_ids = sorted({row[1] for row in borough_rows})
        derivation = {
            "method": "h3.geo_to_cells over the union of five official NYC borough land boundaries",
            "h3_resolution": resolution,
            "parent_boundary_snapshot_ids": parent_snapshot_ids,
            "cell_count": len(cells),
        }
        snapshot = save_bytes_snapshot(
            conn,
            source_id=source_id,
            vintage=f"r{resolution}",
            filename="derivation.json",
            content=json.dumps(derivation, ensure_ascii=False, indent=2).encode("utf-8"),
            source_url=get_source(source_id)["url"],
            run_id=run_id,
            content_type="application/json",
            query=derivation,
            warnings=["Grid cells are modeled analysis units and do not follow administrative boundaries."],
        )
        records: list[dict[str, Any]] = []
        for cell in cells:
            boundary = h3.cell_to_boundary(cell)
            polygon = Polygon([(longitude, latitude) for latitude, longitude in boundary])
            latitude, longitude = h3.cell_to_latlng(cell)
            records.append(
                {
                    "cell_id": cell,
                    "h3_resolution": resolution,
                    "geom_wkt": polygon.wkt,
                    "centroid_lon": longitude,
                    "centroid_lat": latitude,
                    "source_id": source_id,
                    "snapshot_id": snapshot.snapshot_id,
                    "retrieved_at": snapshot.retrieved_at,
                    "valid_from": None,
                    "valid_to": None,
                    "geography_type": "h3",
                    "geography_id": cell,
                    "measure_type": "model_grid",
                    "confidence": 1.0,
                    "method_version": "0.1.0",
                }
            )
        grid = pd.DataFrame(records)
        output = standardized_path(source_id, snapshot.snapshot_id, "nyc_h3_grid.parquet")
        grid.to_parquet(output, index=False)
        with publication_transaction(conn):
            previous = conn.execute(
                "SELECT count(*) FROM geo_cell WHERE source_id IN ('derived_h3', 'derived_h3_grid')"
            ).fetchone()[0]
            conn.execute("DELETE FROM geo_cell WHERE source_id IN ('derived_h3', 'derived_h3_grid')")
            with registered_frame(conn, "_h3_grid", grid):
                conn.execute("INSERT INTO geo_cell BY NAME SELECT * FROM _h3_grid")
            register_table_lineage(
                conn,
                table_name="geo_cell",
                entity_type="geo_cell",
                id_column="cell_id",
                source_id=source_id,
                snapshot_id=snapshot.snapshot_id,
            )
            update_snapshot_details(conn, snapshot.snapshot_id, grid)
            finish_run(
                conn,
                run_id,
                "SUCCEEDED_WITH_WARNINGS",
                request_count=0,
                downloaded_bytes=0,
                inserted_records=len(grid),
                deleted_records=previous,
                warnings=["H3 cells are modeled analysis units; parent borough snapshot IDs are stored in the derivation snapshot."],
            )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "resolution": resolution,
            "cells": len(grid),
            "parent_boundary_snapshot_ids": parent_snapshot_ids,
            "standardized_parquet": str(output),
        }
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise
