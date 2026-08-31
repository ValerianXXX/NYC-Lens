from __future__ import annotations

import json
import math
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable

import duckdb
import geopandas as gpd
import httpx
import pandas as pd

from ..config import get_source
from ..db import begin_run, finish_run
from ..provenance import Snapshot, save_bytes_snapshot
from ..util import json_dumps, sha256_file
from ..storage import replace_source_from_parquet
from .common import register_table_lineage, stable_id, standardized_path, update_snapshot_details


MAPPLUTO_SOURCE_ID = "mappluto_26v2"
MAPPLUTO_LAYER = (
    "https://services5.arcgis.com/GfwWNkhOj9bNBqoJ/arcgis/rest/services/"
    "MAPPLUTO/FeatureServer/0"
)
MAPPLUTO_PARTITION_SIZE = 2_000
BUILDING_SOURCE_ID = "nyc_building_footprints"
BUILDING_DATASET_ID = "5zhs-2jue"
BUILDING_PARTITION_SIZE = 50_000
Progress = Callable[[int, int, str], None]


def _request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    max_attempts: int = 5,
) -> httpx.Response:
    response: httpx.Response | None = None
    for attempt in range(max_attempts):
        try:
            candidate = client.request(method, url, params=params, data=data)
            if candidate.status_code not in {429, 500, 502, 503, 504}:
                candidate.raise_for_status()
                return candidate
            response = candidate
        except httpx.TransportError:
            if attempt == max_attempts - 1:
                raise
        if attempt < max_attempts - 1:
            retry_after = 0.0
            if response is not None:
                try:
                    retry_after = float(response.headers.get("retry-after", "0"))
                except ValueError:
                    retry_after = 0.0
            time.sleep(min(max(retry_after, 0.5 * (2**attempt)), 8.0))
    if response is None:
        raise RuntimeError(f"No response from {url}")
    response.raise_for_status()
    return response


def _reusable_snapshot(
    conn: duckdb.DuckDBPyConnection,
    source_id: str,
    vintage: str,
) -> Snapshot | None:
    rows = conn.execute(
        """
        SELECT snapshot_id, source_id, raw_path, metadata_path, sha256,
               byte_size, retrieved_at, vintage
        FROM dataset_snapshot
        WHERE source_id=? AND vintage=? AND status='SUCCEEDED'
        ORDER BY retrieved_at DESC
        """,
        [source_id, vintage],
    ).fetchall()
    for row in rows:
        path = Path(row[2])
        metadata_path = Path(row[3])
        if (
            path.is_file()
            and metadata_path.is_file()
            and path.stat().st_size == row[5]
            and sha256_file(path) == row[4]
        ):
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


def _column(frame: pd.DataFrame, name: str, default: Any = None) -> pd.Series:
    if name in frame.columns:
        return frame[name]
    return pd.Series(default, index=frame.index)


def _identifier(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").round().astype("Int64").astype("string")
    return values.mask(values.eq("<NA>"))


def _valid_geometry_frame(frame: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, int, int]:
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


def _mappluto_partition(frame: gpd.GeoDataFrame, snapshot: Snapshot) -> tuple[pd.DataFrame, int, int]:
    frame, invalid, removed = _valid_geometry_frame(frame)
    bbl = _identifier(_column(frame, "BBL"))
    keep = bbl.notna()
    removed += int((~keep).sum())
    frame = frame.loc[keep].copy()
    bbl = bbl.loc[keep]
    bounds = frame.geometry.bounds
    parcels = pd.DataFrame(
        {
            "parcel_id": bbl.map(lambda value: f"parcel_{value}"),
            "bbl": bbl,
            "borough": _column(frame, "Borough"),
            "block": _identifier(_column(frame, "Block")),
            "lot": _identifier(_column(frame, "Lot")),
            "address": _column(frame, "Address"),
            "land_use": _column(frame, "LandUse"),
            "zoning": _column(frame, "ZoneDist1"),
            "building_class": _column(frame, "BldgClass"),
            "lot_area_sqft": pd.to_numeric(_column(frame, "LotArea"), errors="coerce"),
            "building_area_sqft": pd.to_numeric(_column(frame, "BldgArea"), errors="coerce"),
            "residential_area_sqft": pd.to_numeric(_column(frame, "ResArea"), errors="coerce"),
            "office_area_sqft": pd.to_numeric(_column(frame, "OfficeArea"), errors="coerce"),
            "retail_area_sqft": pd.to_numeric(_column(frame, "RetailArea"), errors="coerce"),
            "units_residential": pd.to_numeric(_column(frame, "UnitsRes"), errors="coerce"),
            "units_total": pd.to_numeric(_column(frame, "UnitsTotal"), errors="coerce"),
            "year_built": pd.to_numeric(_column(frame, "YearBuilt"), errors="coerce").astype("Int64"),
            "min_longitude": bounds["minx"],
            "min_latitude": bounds["miny"],
            "max_longitude": bounds["maxx"],
            "max_latitude": bounds["maxy"],
            "geom_wkt": frame.geometry.to_wkt(rounding_precision=7, trim=True),
            "source_id": MAPPLUTO_SOURCE_ID,
            "snapshot_id": snapshot.snapshot_id,
            "retrieved_at": snapshot.retrieved_at,
            "valid_from": date(2026, 1, 1),
            "valid_to": None,
            "geography_type": "tax_lot",
            "geography_id": bbl,
            "measure_type": "administrative_record",
            "confidence": 0.98,
            "method_version": "0.2.0",
        }
    )
    return parcels, invalid, removed


def _building_partition(frame: gpd.GeoDataFrame, snapshot: Snapshot) -> tuple[pd.DataFrame, int, int]:
    frame, invalid, removed = _valid_geometry_frame(frame)
    object_id = _identifier(_column(frame, "objectid"))
    keep = object_id.notna()
    removed += int((~keep).sum())
    frame = frame.loc[keep].copy()
    object_id = object_id.loc[keep]
    bin_value = _identifier(_column(frame, "bin"))
    bbl = _identifier(_column(frame, "mappluto_bbl"))
    bbl = bbl.fillna(_identifier(_column(frame, "base_bbl")))
    attribute_columns = [
        "objectid", "doitt_id", "feature_code", "construction_year", "height_roof",
        "ground_elevation", "last_status_type", "last_edited_date", "geom_source",
        "base_bbl", "mappluto_bbl",
    ]
    attribute_records = frame[[column for column in attribute_columns if column in frame.columns]].to_dict("records")
    attributes = [json_dumps({key: value for key, value in record.items() if pd.notna(value)}) for record in attribute_records]
    building_id = object_id.map(lambda value: stable_id("building", BUILDING_SOURCE_ID, value))
    valid_bin = bin_value.notna() & ~bin_value.str.endswith("000000", na=False)
    bounds = frame.geometry.bounds
    buildings = pd.DataFrame(
        {
            "building_id": building_id,
            "bin": bin_value,
            "bbl": bbl,
            "name": _column(frame, "name"),
            "address": None,
            "min_longitude": bounds["minx"],
            "min_latitude": bounds["miny"],
            "max_longitude": bounds["maxx"],
            "max_latitude": bounds["maxy"],
            "geom_wkt": frame.geometry.to_wkt(rounding_precision=7, trim=True),
            "attributes_json": attributes,
            "source_id": BUILDING_SOURCE_ID,
            "snapshot_id": snapshot.snapshot_id,
            "retrieved_at": snapshot.retrieved_at,
            "valid_from": None,
            "valid_to": None,
            "geography_type": "building",
            "geography_id": bin_value.fillna(building_id),
            "measure_type": "administrative_record",
            "confidence": valid_bin.map({True: 0.98, False: 0.8}),
            "method_version": "0.1.0",
        }
    )
    return buildings, invalid, removed


def _replace_table_from_parquet(
    conn: duckdb.DuckDBPyConnection,
    *,
    table_name: str,
    id_column: str,
    entity_type: str,
    source_id: str,
    parquet_glob: Path,
) -> tuple[int, int]:
    if table_name == "building":
        previous, inserted = replace_source_from_parquet(
            conn,
            table_name=table_name,
            source_id=source_id,
            parquet_glob=parquet_glob,
        )
        conn.execute("BEGIN")
        try:
            conn.execute(
                "DELETE FROM standardized_record WHERE entity_type=? AND source_id=?",
                [entity_type, source_id],
            )
            register_table_lineage(
                conn,
                table_name=table_name,
                entity_type=entity_type,
                id_column=id_column,
                source_id=source_id,
                snapshot_id=None,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return previous, inserted
    previous = conn.execute(f"SELECT count(*) FROM {table_name} WHERE source_id=?", [source_id]).fetchone()[0]
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM standardized_record WHERE entity_type=? AND source_id=?", [entity_type, source_id])
        conn.execute(f"DELETE FROM {table_name} WHERE source_id=?", [source_id])
        conn.execute(
            f"INSERT INTO {table_name} BY NAME SELECT * FROM read_parquet(?)",
            [str(parquet_glob)],
        )
        register_table_lineage(
            conn,
            table_name=table_name,
            entity_type=entity_type,
            id_column=id_column,
            source_id=source_id,
            snapshot_id=None,
        )
        inserted = conn.execute(f"SELECT count(*) FROM {table_name} WHERE source_id=?", [source_id]).fetchone()[0]
        conn.execute("COMMIT")
        return previous, inserted
    except Exception:
        conn.execute("ROLLBACK")
        raise


def refresh_mappluto(
    conn: duckdb.DuckDBPyConnection,
    *,
    progress: Progress | None = None,
) -> dict[str, Any]:
    source_id = MAPPLUTO_SOURCE_ID
    source = get_source(source_id)
    run_id = begin_run(conn, source_id, "citywide refresh")
    request_count = 0
    downloaded_bytes = 0
    reused = 0
    snapshots: list[Snapshot] = []
    stage_dir = standardized_path(source_id, "26v2", "_placeholder").parent / run_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=180,
            headers={"User-Agent": "NYC-Lens/0.1 local-research"},
            limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
        ) as client:
            metadata_response = _request_with_retry(client, "GET", MAPPLUTO_LAYER, params={"f": "json"})
            metadata = metadata_response.json()
            if metadata.get("error"):
                raise RuntimeError(f"MapPLUTO metadata error: {metadata['error']}")
            metadata_snapshot = save_bytes_snapshot(
                conn,
                source_id=source_id,
                vintage="26v2-metadata",
                filename="layer.json",
                content=metadata_response.content,
                source_url=MAPPLUTO_LAYER,
                run_id=run_id,
                content_type="application/json",
                query={"f": "json"},
                http_status=metadata_response.status_code,
            )
            request_count += 1
            downloaded_bytes += metadata_snapshot.byte_size
            edit_timestamp = str((metadata.get("editingInfo") or {}).get("lastEditDate") or metadata.get("lastEditDate") or "unknown")
            object_id_field = metadata.get("objectIdField") or "OBJECTID"
            field_names = [field["name"] for field in metadata.get("fields", [])]
            out_fields = [name for name in field_names if name.lower() != "ownername"]
            if object_id_field not in out_fields:
                out_fields.append(object_id_field)

            ids_params = {"where": "1=1", "returnIdsOnly": "true", "f": "json"}
            ids_response = _request_with_retry(client, "POST", f"{MAPPLUTO_LAYER}/query", data=ids_params)
            ids_payload = ids_response.json()
            if ids_payload.get("error"):
                raise RuntimeError(f"MapPLUTO ID query error: {ids_payload['error']}")
            object_ids = sorted(int(value) for value in ids_payload.get("objectIds", []))
            if len(object_ids) < 800_000:
                raise ValueError(f"MapPLUTO citywide coverage is unexpectedly low: {len(object_ids)}")
            ids_snapshot = save_bytes_snapshot(
                conn,
                source_id=source_id,
                vintage=f"26v2-{edit_timestamp}-ids",
                filename="object_ids.json",
                content=ids_response.content,
                source_url=f"{MAPPLUTO_LAYER}/query",
                run_id=run_id,
                content_type="application/json",
                query=ids_params,
                http_status=ids_response.status_code,
            )
            request_count += 1
            downloaded_bytes += ids_snapshot.byte_size

            total_parts = math.ceil(len(object_ids) / MAPPLUTO_PARTITION_SIZE)
            total_features = 0
            total_rows = 0
            invalid_repaired = 0
            removed_geometries_or_ids = 0
            for part_index, start in enumerate(range(0, len(object_ids), MAPPLUTO_PARTITION_SIZE), start=1):
                part_ids = object_ids[start : start + MAPPLUTO_PARTITION_SIZE]
                vintage = f"26v2-{edit_timestamp}-oids-{part_ids[0]}-{part_ids[-1]}"
                snapshot = _reusable_snapshot(conn, source_id, vintage)
                if snapshot is None:
                    query = {
                        "objectIds": ",".join(str(value) for value in part_ids),
                        "outFields": ",".join(out_fields),
                        "returnGeometry": "true",
                        "outSR": "4326",
                        "f": "geojson",
                    }
                    response = _request_with_retry(client, "POST", f"{MAPPLUTO_LAYER}/query", data=query)
                    payload = response.json()
                    if payload.get("error"):
                        raise RuntimeError(f"MapPLUTO partition {part_index} error: {payload['error']}")
                    if len(payload.get("features", [])) != len(part_ids):
                        raise ValueError(
                            f"MapPLUTO partition {part_index} returned {len(payload.get('features', []))} "
                            f"of {len(part_ids)} requested features"
                        )
                    snapshot = save_bytes_snapshot(
                        conn,
                        source_id=source_id,
                        vintage=vintage,
                        filename=f"mappluto_part_{part_index:04d}.geojson",
                        content=response.content,
                        source_url=f"{MAPPLUTO_LAYER}/query",
                        run_id=run_id,
                        content_type="application/geo+json",
                        query={**query, "objectIds": {"count": len(part_ids), "first": part_ids[0], "last": part_ids[-1]}},
                        valid_from=date(2026, 1, 1),
                        http_status=response.status_code,
                        warnings=["OwnerName intentionally excluded under the NYC Lens privacy rules."],
                    )
                    request_count += 1
                    downloaded_bytes += snapshot.byte_size
                else:
                    reused += 1
                snapshots.append(snapshot)
                raw_frame = gpd.read_file(snapshot.path)
                total_features += len(raw_frame)
                parcels, invalid, removed = _mappluto_partition(raw_frame, snapshot)
                invalid_repaired += invalid
                removed_geometries_or_ids += removed
                if parcels["parcel_id"].duplicated().any():
                    raise ValueError(f"Duplicate BBL within MapPLUTO partition {part_index}")
                output = stage_dir / f"parcel_part_{part_index:04d}.parquet"
                parcels.to_parquet(output, index=False)
                total_rows += len(parcels)
                update_snapshot_details(
                    conn,
                    snapshot.snapshot_id,
                    parcels,
                    warnings=[
                        f"invalid_geometries_repaired={invalid}",
                        f"records_removed_missing_geometry_or_bbl={removed}",
                        "OwnerName intentionally excluded under the NYC Lens privacy rules.",
                    ],
                )
                if progress:
                    progress(part_index, total_parts, f"MapPLUTO {part_ids[0]}-{part_ids[-1]}")

        if total_features != len(object_ids):
            raise ValueError(f"MapPLUTO feature count mismatch: {total_features}/{len(object_ids)}")
        if total_rows < int(len(object_ids) * 0.99):
            raise ValueError(f"MapPLUTO standardized coverage below 99%: {total_rows}/{len(object_ids)}")
        previous, inserted = _replace_table_from_parquet(
            conn,
            table_name="parcel",
            id_column="parcel_id",
            entity_type="parcel",
            source_id=source_id,
            parquet_glob=stage_dir / "parcel_part_*.parquet",
        )
        if inserted != total_rows:
            raise ValueError(f"MapPLUTO database count mismatch: {inserted}/{total_rows}")
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            inserted_records=inserted,
            deleted_records=previous,
            warnings=[
                f"Reused {reused} verified partitions for unchanged official service content.",
                f"Repaired {invalid_repaired} invalid geometries; removed {removed_geometries_or_ids} records without usable geometry or BBL.",
                "OwnerName was intentionally excluded under the project privacy rules.",
            ],
        )
        return {
            "run_id": run_id,
            "metadata_snapshot_id": metadata_snapshot.snapshot_id,
            "ids_snapshot_id": ids_snapshot.snapshot_id,
            "partition_snapshots": len(snapshots),
            "reused_partitions": reused,
            "published_features": len(object_ids),
            "parcel_rows": inserted,
            "invalid_geometries_repaired": invalid_repaired,
            "removed_missing_geometry_or_bbl": removed_geometries_or_ids,
            "downloaded_bytes_this_run": downloaded_bytes,
            "raw_partition_bytes": sum(snapshot.byte_size for snapshot in snapshots),
            "privacy_exclusion": "OwnerName",
        }
    except Exception as exc:
        finish_run(
            conn,
            run_id,
            "FAILED",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            inserted_records=0,
            error={"type": type(exc).__name__, "message": str(exc)},
            warnings=[f"Completed Raw partitions remain reusable: {len(snapshots)}"],
        )
        raise


def refresh_building_footprints(
    conn: duckdb.DuckDBPyConnection,
    *,
    progress: Progress | None = None,
) -> dict[str, Any]:
    source_id = BUILDING_SOURCE_ID
    source = get_source(source_id)
    run_id = begin_run(conn, source_id, "citywide refresh")
    request_count = 0
    downloaded_bytes = 0
    reused = 0
    snapshots: list[Snapshot] = []
    stage_dir = standardized_path(source_id, "current", "_placeholder").parent / run_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=300,
            headers={"User-Agent": "NYC-Lens/0.1 local-research"},
            limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
        ) as client:
            metadata_url = f"https://data.cityofnewyork.us/api/views/{BUILDING_DATASET_ID}"
            metadata_response = _request_with_retry(client, "GET", metadata_url)
            metadata = metadata_response.json()
            updated_at = str(metadata.get("rowsUpdatedAt") or metadata.get("dataUpdatedAt") or "unknown")
            metadata_snapshot = save_bytes_snapshot(
                conn,
                source_id=source_id,
                vintage=f"current-{updated_at}-metadata",
                filename="metadata.json",
                content=metadata_response.content,
                source_url=metadata_url,
                run_id=run_id,
                content_type="application/json",
                query={"kind": "Socrata dataset metadata"},
                http_status=metadata_response.status_code,
            )
            request_count += 1
            downloaded_bytes += metadata_snapshot.byte_size

            count_params = {"$select": "count(*) as total"}
            count_response = _request_with_retry(client, "GET", source["api_url"].replace(".geojson", ".json"), params=count_params)
            count_payload = count_response.json()
            published_count = int(count_payload[0]["total"])
            if published_count < 1_000_000:
                raise ValueError(f"Building Footprints citywide coverage is unexpectedly low: {published_count}")
            count_snapshot = save_bytes_snapshot(
                conn,
                source_id=source_id,
                vintage=f"current-{updated_at}-count",
                filename="count.json",
                content=count_response.content,
                source_url=source["api_url"].replace(".geojson", ".json"),
                run_id=run_id,
                content_type="application/json",
                query=count_params,
                http_status=count_response.status_code,
            )
            request_count += 1
            downloaded_bytes += count_snapshot.byte_size

            total_parts = math.ceil(published_count / BUILDING_PARTITION_SIZE)
            total_features = 0
            total_rows = 0
            invalid_repaired = 0
            removed_geometries_or_ids = 0
            for part_index, offset in enumerate(range(0, published_count, BUILDING_PARTITION_SIZE), start=1):
                expected = min(BUILDING_PARTITION_SIZE, published_count - offset)
                vintage = f"current-{updated_at}-offset-{offset}-limit-{BUILDING_PARTITION_SIZE}"
                snapshot = _reusable_snapshot(conn, source_id, vintage)
                if snapshot is None:
                    query = {
                        "$limit": BUILDING_PARTITION_SIZE,
                        "$offset": offset,
                        "$order": "objectid",
                    }
                    response = _request_with_retry(client, "GET", source["api_url"], params=query)
                    payload = response.json()
                    features = payload.get("features", [])
                    if len(features) != expected:
                        raise ValueError(
                            f"Building partition {part_index} returned {len(features)} of {expected} expected features"
                        )
                    snapshot = save_bytes_snapshot(
                        conn,
                        source_id=source_id,
                        vintage=vintage,
                        filename=f"building_part_{part_index:03d}.geojson",
                        content=response.content,
                        source_url=source["api_url"],
                        run_id=run_id,
                        content_type="application/geo+json",
                        query=query,
                        http_status=response.status_code,
                    )
                    request_count += 1
                    downloaded_bytes += snapshot.byte_size
                else:
                    reused += 1
                snapshots.append(snapshot)
                raw_frame = gpd.read_file(snapshot.path)
                total_features += len(raw_frame)
                buildings, invalid, removed = _building_partition(raw_frame, snapshot)
                invalid_repaired += invalid
                removed_geometries_or_ids += removed
                if buildings["building_id"].duplicated().any():
                    raise ValueError(f"Duplicate objectid within Building Footprints partition {part_index}")
                output = stage_dir / f"building_part_{part_index:03d}.parquet"
                buildings.to_parquet(output, index=False)
                total_rows += len(buildings)
                update_snapshot_details(
                    conn,
                    snapshot.snapshot_id,
                    buildings,
                    warnings=[
                        f"invalid_geometries_repaired={invalid}",
                        f"records_removed_missing_geometry_or_objectid={removed}",
                    ],
                )
                if progress:
                    progress(part_index, total_parts, f"Building Footprints offset {offset}")

        if total_features != published_count:
            raise ValueError(f"Building Footprints feature count mismatch: {total_features}/{published_count}")
        if total_rows < int(published_count * 0.99):
            raise ValueError(f"Building Footprints standardized coverage below 99%: {total_rows}/{published_count}")
        previous, inserted = _replace_table_from_parquet(
            conn,
            table_name="building",
            id_column="building_id",
            entity_type="building",
            source_id=source_id,
            parquet_glob=stage_dir / "building_part_*.parquet",
        )
        if inserted != total_rows:
            raise ValueError(f"Building Footprints database count mismatch: {inserted}/{total_rows}")
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS" if removed_geometries_or_ids or invalid_repaired else "SUCCEEDED",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            inserted_records=inserted,
            deleted_records=previous,
            warnings=[
                f"Reused {reused} verified partitions for unchanged official dataset content.",
                f"Repaired {invalid_repaired} invalid geometries; removed {removed_geometries_or_ids} records without usable geometry or objectid.",
            ],
        )
        return {
            "run_id": run_id,
            "metadata_snapshot_id": metadata_snapshot.snapshot_id,
            "count_snapshot_id": count_snapshot.snapshot_id,
            "partition_snapshots": len(snapshots),
            "reused_partitions": reused,
            "published_features": published_count,
            "building_rows": inserted,
            "invalid_geometries_repaired": invalid_repaired,
            "removed_missing_geometry_or_objectid": removed_geometries_or_ids,
            "downloaded_bytes_this_run": downloaded_bytes,
            "raw_partition_bytes": sum(snapshot.byte_size for snapshot in snapshots),
        }
    except Exception as exc:
        finish_run(
            conn,
            run_id,
            "FAILED",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            error={"type": type(exc).__name__, "message": str(exc)},
            warnings=[f"Completed Raw partitions remain reusable: {len(snapshots)}"],
        )
        raise
