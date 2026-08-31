from __future__ import annotations

from typing import Any

import duckdb
import geopandas as gpd
import pandas as pd

from ..config import geographies_config, get_source
from ..db import begin_run, finish_run
from ..provenance import download_snapshot
from .common import stable_id, standardized_path, update_snapshot_details
from .geographies import _prepare_geometry, _replace_boundaries
from .socrata import fetch_metadata_snapshot


HAZARD_LAYERS: dict[str, dict[str, Any]] = {
    "nyc_hurricane_evacuation_zones": {
        "geography_type": "hurricane_evacuation_zone",
        "minimum_rows": 6,
        "maximum_rows": 20,
        "limit": 100,
        "vintage": "current",
        "measure_type": "emergency_planning_zone",
    },
    "nyc_future_floodplain_2050s": {
        "geography_type": "future_2050_100yr_floodplain",
        "minimum_rows": 10_000,
        "maximum_rows": 15_000,
        "limit": 20_000,
        "vintage": "2050s",
        "measure_type": "long_range_climate_planning_projection",
    },
    "nyc_sea_level_rise_2100_floodplain": {
        "geography_type": "future_2100_100yr_floodplain",
        "minimum_rows": 1,
        "maximum_rows": 5,
        "limit": 10,
        "vintage": "2100",
        "measure_type": "long_range_climate_planning_projection",
    },
}


def _feature_identity(source_id: str, row: pd.Series) -> tuple[str, str]:
    if source_id == "nyc_hurricane_evacuation_zones":
        zone = str(row.get("hurricane_") or "").strip().upper()
        if not zone:
            raise ValueError("Hurricane evacuation feature has no zone identifier")
        name = (
            f"Hurricane Evacuation Zone {zone}"
            if zone != "X"
            else "Outside numbered hurricane evacuation zones (published X area)"
        )
        return zone, name
    if source_id == "nyc_future_floodplain_2050s":
        geography_id = stable_id(
            "hazard",
            source_id,
            row.get("gridcode"),
            row.get("join_count"),
            row.get("fld_zone"),
            row.get("static_bfe"),
            row.get("abfe_0_2pc"),
            row.get("shape_leng"),
            row.get("shape_area"),
            row.geometry.wkb_hex,
        )
        zone = str(row.get("fld_zone") or "projected expansion area").strip()
        return geography_id, f"2050s 100-year floodplain: {zone}"
    source_feature_id = str(row.get("id") or "2100-citywide").strip()
    return source_feature_id, "2100 100-year floodplain"


def _hazard_boundary_rows(
    frame: gpd.GeoDataFrame,
    *,
    source_id: str,
    snapshot_id: str,
    retrieved_at: Any,
) -> pd.DataFrame:
    definition = HAZARD_LAYERS[source_id]
    metric = frame.to_crs(geographies_config()["analysis_crs"])
    rows: list[dict[str, Any]] = []
    for position, (_, row) in enumerate(frame.iterrows()):
        geography_id, name = _feature_identity(source_id, row)
        west, south, east, north = row.geometry.bounds
        rows.append(
            {
                "boundary_id": stable_id("boundary", source_id, geography_id),
                "geography_type": definition["geography_type"],
                "geography_id": geography_id,
                "name": name,
                "parent_id": None,
                "geom_wkt": row.geometry.wkt,
                "area_m2": float(metric.geometry.iloc[position].area),
                "source_id": source_id,
                "snapshot_id": snapshot_id,
                "retrieved_at": retrieved_at,
                "valid_from": None,
                "valid_to": None,
                "measure_type": definition["measure_type"],
                "confidence": 0.9,
                "method_version": "0.1.0",
                "min_longitude": float(west),
                "min_latitude": float(south),
                "max_longitude": float(east),
                "max_latitude": float(north),
            }
        )
    result = pd.DataFrame(rows)
    return result.drop_duplicates("boundary_id", keep="first").reset_index(drop=True)


def refresh_hazard_layer(
    conn: duckdb.DuckDBPyConnection,
    source_id: str,
) -> dict[str, Any]:
    if source_id not in HAZARD_LAYERS:
        raise ValueError(f"Unsupported hazard source: {source_id}")
    definition = HAZARD_LAYERS[source_id]
    source = get_source(source_id)
    run_id = begin_run(conn, source_id, "refresh official planning geometry")
    downloaded_bytes = 0
    request_count = 0
    try:
        metadata = fetch_metadata_snapshot(
            conn, source_id, f"{definition['vintage']}-metadata", run_id
        )
        request_count += 1
        downloaded_bytes += metadata.byte_size
        snapshot = download_snapshot(
            conn,
            source_id=source_id,
            vintage=str(definition["vintage"]),
            url=source["api_url"],
            filename=f"{source['dataset_id']}.geojson",
            run_id=run_id,
            params={"$limit": definition["limit"]},
            timeout_seconds=600,
        )
        request_count += 1
        downloaded_bytes += snapshot.byte_size
        frame = gpd.read_file(snapshot.path)
        frame, invalid_geometry_count, removed_geometry_count = _prepare_geometry(frame)
        if not definition["minimum_rows"] <= len(frame) <= definition["maximum_rows"]:
            raise ValueError(
                f"{source_id} feature count outside expected range: {len(frame)}"
            )
        boundaries = _hazard_boundary_rows(
            frame,
            source_id=source_id,
            snapshot_id=snapshot.snapshot_id,
            retrieved_at=snapshot.retrieved_at,
        )
        duplicate_features_removed = len(frame) - len(boundaries)
        previous, inserted = _replace_boundaries(
            conn, source_id, boundaries, snapshot.snapshot_id
        )
        warnings = [
            "Planning-screening layer only; not an engineering, insurance, legal, regulatory, or parcel-level determination.",
            "Coverage percentages describe geometric overlap with the selected catchment, not event probability or expected loss.",
            f"Invalid geometries repaired: {invalid_geometry_count}; empty or missing geometries removed: {removed_geometry_count}.",
            f"Exact duplicate source features removed during standardization: {duplicate_features_removed}.",
        ]
        if source_id == "nyc_hurricane_evacuation_zones":
            zones = sorted(boundaries["geography_id"].astype(str).unique().tolist())
            warnings.append(
                "Evacuation-zone overlap excludes the published X area; actual evacuation orders must be checked with NYC Emergency Management."
            )
        else:
            zones = []
            warnings.append(
                "This long-range sea-level-rise scenario uses historical preliminary FEMA and NPCC model assumptions and is not a current flood map."
            )
        update_snapshot_details(
            conn,
            snapshot.snapshot_id,
            boundaries,
            warnings=warnings,
        )
        frame.to_parquet(
            standardized_path(
                source_id,
                str(definition["vintage"]),
                f"{definition['geography_type']}.parquet",
            ),
            index=False,
        )
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            inserted_records=inserted,
            deleted_records=int(previous),
            warnings=warnings,
        )
        return {
            "run_id": run_id,
            "metadata_snapshot_id": metadata.snapshot_id,
            "snapshot_id": snapshot.snapshot_id,
            "source_id": source_id,
            "source_features_after_geometry_validation": len(frame),
            "features": inserted,
            "exact_duplicate_features_removed": duplicate_features_removed,
            "published_zone_ids": zones,
            "invalid_geometries_repaired": invalid_geometry_count,
            "removed_geometries": removed_geometry_count,
            "raw_bytes": downloaded_bytes,
        }
    except Exception as exc:
        finish_run(
            conn,
            run_id,
            "FAILED",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        raise
