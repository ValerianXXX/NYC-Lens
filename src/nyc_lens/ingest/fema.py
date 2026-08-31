from __future__ import annotations

import json
import math
from typing import Any

import duckdb
import geopandas as gpd
import httpx
import pandas as pd
from shapely import from_wkt, union_all

from ..config import get_source
from ..db import begin_run, finish_run
from ..provenance import download_snapshot
from .common import register_table_lineage, stable_id, standardized_path, update_snapshot_details
from .geographies import _prepare_geometry


LAYER_URL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28"
PAGE_SIZE = 500
GEOMETRY_PRECISION_DEGREES = 5
MAX_ALLOWABLE_OFFSET_DEGREES = 0.00001


def _csa_union_and_envelopes(
    conn: duckdb.DuckDBPyConnection,
) -> tuple[Any, list[tuple[str, float, float, float, float]]]:
    rows = conn.execute(
        """
        SELECT geography_id, geom_wkt, min_longitude, min_latitude,
               max_longitude, max_latitude
        FROM geo_boundary
        WHERE source_id='tiger_county_2024'
          AND geography_type='county_equivalent_2024'
          AND geography_id IN (
              SELECT geography_id FROM region_member WHERE region_code='CSA408'
          )
        ORDER BY geography_id
        """
    ).fetchall()
    if len(rows) != 30:
        raise ValueError(f"Expected 30 official CSA 408 county equivalents, found {len(rows)}")
    geometry = union_all(from_wkt([row[1] for row in rows]))
    envelopes = [
        (str(row[0]), float(row[2]), float(row[3]), float(row[4]), float(row[5]))
        for row in rows
    ]
    return geometry, envelopes


def _field(frame: pd.DataFrame, name: str) -> pd.Series:
    lookup = {str(column).lower(): column for column in frame.columns}
    column = lookup.get(name.lower())
    return frame[column] if column is not None else pd.Series(None, index=frame.index)


def _text(value: Any, default: str = "") -> str:
    return str(value).strip() if pd.notna(value) and str(value).strip() else default


def _page_boundaries(
    frame: gpd.GeoDataFrame,
    *,
    csa_union,
    snapshot_id: str,
    retrieved_at: Any,
    seen: set[str],
) -> tuple[pd.DataFrame, int, int, int]:
    frame, invalid, removed = _prepare_geometry(frame)
    if frame.empty:
        return pd.DataFrame(), invalid, removed, 0
    intersects = frame.geometry.intersects(csa_union)
    outside = int((~intersects).sum())
    frame = frame.loc[intersects].copy()
    if frame.empty:
        return pd.DataFrame(), invalid, removed, outside
    frame.geometry = frame.geometry.intersection(csa_union)
    frame = frame.loc[~frame.geometry.is_empty & frame.geometry.notna()].copy()
    metric_geometry = frame.to_crs("EPSG:26918").geometry
    global_ids = _field(frame, "GlobalID")
    gfid = _field(frame, "GFID")
    flood_ids = _field(frame, "FLD_AR_ID")
    object_ids = _field(frame, "OBJECTID")
    dfirm_ids = _field(frame, "DFIRM_ID")
    zones = _field(frame, "FLD_ZONE")
    subtypes = _field(frame, "ZONE_SUBTY")
    rows: list[dict[str, Any]] = []
    for position, (_, row) in enumerate(frame.iterrows()):
        candidates = (
            global_ids.loc[row.name],
            gfid.loc[row.name],
            flood_ids.loc[row.name],
            f"{_text(dfirm_ids.loc[row.name])}:{_text(object_ids.loc[row.name])}",
        )
        source_feature_id = next(
            (_text(value) for value in candidates if _text(value)),
            None,
        )
        if source_feature_id is None:
            continue
        boundary_id = stable_id(
            "boundary", "fema_nfhl_special_flood_hazard_areas", source_feature_id
        )
        if boundary_id in seen:
            continue
        seen.add(boundary_id)
        zone = _text(zones.loc[row.name], "UNKNOWN")
        subtype = _text(subtypes.loc[row.name])
        west, south, east, north = row.geometry.bounds
        rows.append(
            {
                "boundary_id": boundary_id,
                "geography_type": "fema_nfhl_special_flood_hazard_area",
                "geography_id": source_feature_id,
                "name": f"FEMA NFHL SFHA {zone}" + (f" — {subtype}" if subtype else ""),
                "parent_id": f"flood_zone:{zone}",
                "geom_wkt": row.geometry.wkt,
                "area_m2": float(metric_geometry.iloc[position].area),
                "source_id": "fema_nfhl_special_flood_hazard_areas",
                "snapshot_id": snapshot_id,
                "retrieved_at": retrieved_at,
                "valid_from": None,
                "valid_to": None,
                "measure_type": "effective_flood_hazard_polygon",
                "confidence": 0.95,
                "method_version": "0.2.0",
                "min_longitude": float(west),
                "min_latitude": float(south),
                "max_longitude": float(east),
                "max_latitude": float(north),
            }
        )
    return pd.DataFrame(rows), invalid, removed, outside


def refresh_fema_sfha(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "fema_nfhl_special_flood_hazard_areas"
    source = get_source(source_id)
    run_id = begin_run(conn, source_id, "refresh FEMA NFHL SFHA polygons clipped to official CSA 408")
    request_count = 0
    downloaded_bytes = 0
    invalid_total = 0
    removed_total = 0
    outside_total = 0
    duplicate_total = 0
    envelope_count_total = 0
    seen: set[str] = set()
    snapshot_ids: list[str] = []
    conn.execute(
        "CREATE OR REPLACE TEMP TABLE _fema_sfha_staging AS "
        "SELECT * FROM geo_boundary WHERE FALSE"
    )
    try:
        csa_union, envelopes = _csa_union_and_envelopes(conn)
        with httpx.Client(
            follow_redirects=True,
            timeout=300,
            limits=httpx.Limits(max_keepalive_connections=2, max_connections=2),
        ) as client:
            metadata = download_snapshot(
                conn,
                source_id=source_id,
                vintage="service-metadata",
                url=LAYER_URL,
                filename="nfhl_layer_28_metadata.json",
                run_id=run_id,
                params={"f": "json"},
                timeout_seconds=300,
                client=client,
            )
            request_count += 1
            downloaded_bytes += metadata.byte_size
            snapshot_ids.append(metadata.snapshot_id)
            for county_geoid, min_x, min_y, max_x, max_y in envelopes:
                envelope = f"{min_x},{min_y},{max_x},{max_y}"
                common_params = {
                    "where": "SFHA_TF='T'",
                    "geometry": envelope,
                    "geometryType": "esriGeometryEnvelope",
                    "inSR": "4326",
                    "spatialRel": "esriSpatialRelIntersects",
                    "f": "geojson",
                }
                count_snapshot = download_snapshot(
                    conn,
                    source_id=source_id,
                    vintage=f"count-{county_geoid}",
                    url=source["api_url"],
                    filename=f"count_{county_geoid}.json",
                    run_id=run_id,
                    params={
                        **common_params,
                        "f": "json",
                        "returnCountOnly": "true",
                        "returnGeometry": "false",
                    },
                    timeout_seconds=300,
                    client=client,
                )
                request_count += 1
                downloaded_bytes += count_snapshot.byte_size
                snapshot_ids.append(count_snapshot.snapshot_id)
                count_payload = json.loads(count_snapshot.path.read_text(encoding="utf-8"))
                if "error" in count_payload:
                    raise RuntimeError(
                        f"FEMA count query failed for {county_geoid}: {count_payload['error']}"
                    )
                count = int(count_payload.get("count", 0))
                envelope_count_total += count
                for page_index in range(math.ceil(count / PAGE_SIZE)):
                    offset = page_index * PAGE_SIZE
                    snapshot = download_snapshot(
                        conn,
                        source_id=source_id,
                        vintage=f"sfha-{county_geoid}-offset-{offset:07d}",
                        url=source["api_url"],
                        filename=f"sfha_{county_geoid}_{offset:07d}.geojson",
                        run_id=run_id,
                        params={
                            **common_params,
                            "outFields": "OBJECTID,DFIRM_ID,FLD_AR_ID,FLD_ZONE,ZONE_SUBTY,SFHA_TF,GFID,GlobalID",
                            "returnGeometry": "true",
                            "outSR": "4326",
                            "geometryPrecision": GEOMETRY_PRECISION_DEGREES,
                            "maxAllowableOffset": MAX_ALLOWABLE_OFFSET_DEGREES,
                            "orderByFields": "OBJECTID ASC",
                            "resultOffset": offset,
                            "resultRecordCount": PAGE_SIZE,
                        },
                        timeout_seconds=300,
                        client=client,
                    )
                    request_count += 1
                    downloaded_bytes += snapshot.byte_size
                    snapshot_ids.append(snapshot.snapshot_id)
                    frame = gpd.read_file(snapshot.path)
                    if frame.empty and offset < count:
                        raise ValueError(
                            f"FEMA page unexpectedly empty for {county_geoid} at {offset}"
                        )
                    boundaries, invalid, removed, outside = _page_boundaries(
                        frame,
                        csa_union=csa_union,
                        snapshot_id=snapshot.snapshot_id,
                        retrieved_at=snapshot.retrieved_at,
                        seen=seen,
                    )
                    duplicate_total += max(0, len(frame) - outside - len(boundaries))
                    invalid_total += invalid
                    removed_total += removed
                    outside_total += outside
                    update_snapshot_details(
                        conn,
                        snapshot.snapshot_id,
                        boundaries,
                        warnings=[
                            f"County-envelope query basis: {county_geoid}; exact CSA union clipping applied after download.",
                            "ArcGIS retrieval uses 5-decimal coordinate precision and a 0.00001-degree maximum allowable offset (approximately one metre) to bound transfer size.",
                            "SFHA_TF='T' only; current NFHL screening geometry, not an insurance or engineering determination.",
                        ],
                    )
                    if not boundaries.empty:
                        conn.register("_fema_page_boundaries", boundaries)
                        conn.execute(
                            "INSERT INTO _fema_sfha_staging BY NAME "
                            "SELECT * FROM _fema_page_boundaries"
                        )
                        conn.unregister("_fema_page_boundaries")

        inserted = conn.execute("SELECT count(*) FROM _fema_sfha_staging").fetchone()[0]
        if inserted < 10_000:
            raise ValueError(f"FEMA CSA SFHA coverage is unexpectedly low: {inserted}")
        previous = conn.execute(
            "SELECT count(*) FROM geo_boundary WHERE source_id=?", [source_id]
        ).fetchone()[0]
        conn.execute("BEGIN")
        try:
            conn.execute(
                "DELETE FROM standardized_record "
                "WHERE entity_type='geo_boundary' AND source_id=?",
                [source_id],
            )
            conn.execute("DELETE FROM geo_boundary WHERE source_id=?", [source_id])
            conn.execute("INSERT INTO geo_boundary BY NAME SELECT * FROM _fema_sfha_staging")
            register_table_lineage(
                conn,
                table_name="geo_boundary",
                entity_type="geo_boundary",
                id_column="boundary_id",
                source_id=source_id,
                snapshot_id=None,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        parquet_path = standardized_path(source_id, "current", "sfha_boundaries.parquet")
        escaped = str(parquet_path).replace("'", "''")
        conn.execute(
            f"COPY (SELECT * FROM _fema_sfha_staging ORDER BY boundary_id) "
            f"TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        warnings = [
            "FEMA NFHL SFHA geometry is a screening layer, not an insurance, engineering, legal, regulatory, expected-loss or parcel-level determination.",
            "County envelopes are used only for bounded retrieval; every retained geometry is exactly intersected with the official CSA 408 union and deduplicated by stable source identifiers.",
            "Retrieved geometry is service-simplified to approximately one-metre tolerance before exact local intersection calculations.",
            "NFHL study coverage and effective dates vary by community and can change between refreshes.",
        ]
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            inserted_records=inserted,
            deleted_records=previous,
            warnings=warnings,
        )
        return {
            "run_id": run_id,
            "status": "SUCCEEDED_WITH_WARNINGS",
            "snapshot_count": len(snapshot_ids),
            "envelope_sfha_records": envelope_count_total,
            "standardized_sfha_polygons": inserted,
            "duplicate_or_nonpolygon_records_removed": duplicate_total,
            "outside_csa_records_removed": outside_total,
            "invalid_geometries_repaired": invalid_total,
            "empty_geometries_removed": removed_total,
            "raw_bytes": downloaded_bytes,
            "standardized_parquet": str(parquet_path),
            "warnings": warnings,
        }
    except Exception as exc:
        try:
            conn.unregister("_fema_page_boundaries")
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
