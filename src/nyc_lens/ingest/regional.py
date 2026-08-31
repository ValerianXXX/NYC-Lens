from __future__ import annotations

import re
from datetime import date
from typing import Any

import duckdb
import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

from ..config import geographies_config, get_source
from ..db import begin_run, finish_run
from ..provenance import Snapshot, download_snapshot
from ..util import json_dumps
from .common import (
    GREATER_NYC_COUNTY_FIPS,
    GREATER_NYC_STATE_FIPS,
    register_table_lineage,
    stable_id,
    standardized_path,
    update_snapshot_details,
)


STATE_ABBREVIATIONS = {"09": "CT", "34": "NJ", "36": "NY", "42": "PA"}
TIGER_BASE = "https://www2.census.gov/geo/tiger/TIGER2024"


def _normalized_code(value: Any, width: int) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(width)


def _normalized_columns(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame.columns = [re.sub(r"\s+", " ", str(column)).strip() for column in frame.columns]
    return frame


def _column(frame: pd.DataFrame, *needles: str) -> str:
    for column in frame.columns:
        lowered = column.lower()
        if all(needle.lower() in lowered for needle in needles):
            return column
    raise ValueError(f"Missing expected official delineation column containing: {needles}")


def _read_delineation(path: Any) -> pd.DataFrame:
    preview = pd.read_excel(path, header=None, nrows=10)
    header_row = None
    for index, row in preview.iterrows():
        text = " | ".join(str(value) for value in row.tolist())
        if "CSA Code" in text and "FIPS State Code" in text:
            header_row = int(index)
            break
    if header_row is None:
        raise ValueError("Could not locate the official delineation workbook header row.")
    return _normalized_columns(pd.read_excel(path, header=header_row, dtype=str))


def refresh_region_definition(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "omb_2023_cbsa_delineation"
    run_id = begin_run(conn, source_id, "refresh")
    source = get_source(source_id)
    regional = geographies_config()["greater_nyc"]
    try:
        snapshot = download_snapshot(
            conn,
            source_id=source_id,
            vintage=str(regional["definition_vintage"]),
            url=source["url"],
            filename="list1_2023.xlsx",
            run_id=run_id,
            valid_from=date(2023, 7, 21),
            timeout_seconds=180,
        )
        frame = _read_delineation(snapshot.path)
        csa_column = _column(frame, "CSA Code")
        state_column = _column(frame, "FIPS State Code")
        county_code_column = _column(frame, "FIPS County Code")
        county_name_column = _column(frame, "County/County Equivalent")
        role_column = next((column for column in frame.columns if "Central/Outlying" in column), None)
        selected = frame.loc[
            frame[csa_column].map(lambda value: _normalized_code(value, 3)) == regional["csa_code"]
        ].copy()
        selected["state_fips"] = selected[state_column].map(lambda value: _normalized_code(value, 2))
        selected["county_fips"] = selected[county_code_column].map(lambda value: _normalized_code(value, 3))
        selected["county_geoid"] = selected["state_fips"] + selected["county_fips"]
        selected = selected.drop_duplicates("county_geoid")
        observed = set(selected["county_geoid"])
        expected = set(GREATER_NYC_COUNTY_FIPS)
        if observed != expected:
            raise ValueError(
                "Official CSA 408 membership differs from configured scope: "
                f"missing={sorted(expected - observed)}, unexpected={sorted(observed - expected)}"
            )

        member_records: list[dict[str, Any]] = []
        for _, row in selected.sort_values("county_geoid").iterrows():
            geoid = row["county_geoid"]
            definition = regional["counties"][geoid]
            member_records.append(
                {
                    "region_member_id": stable_id("region_member", f"CSA{regional['csa_code']}", geoid),
                    "region_code": f"CSA{regional['csa_code']}",
                    "region_name": regional["name"],
                    "geography_type": "county_equivalent",
                    "geography_id": geoid,
                    "geography_name": str(row[county_name_column]).strip(),
                    "state_fips": row["state_fips"],
                    "state_abbr": definition["state"],
                    "subregion": definition["subregion"],
                    "member_role": str(row[role_column]).strip() if role_column and pd.notna(row[role_column]) else None,
                    "source_id": source_id,
                    "snapshot_id": snapshot.snapshot_id,
                    "retrieved_at": snapshot.retrieved_at,
                    "valid_from": date(2023, 7, 21),
                    "valid_to": None,
                    "measure_type": "official_definition",
                    "confidence": 1.0,
                    "method_version": "0.2.0",
                }
            )
        members = pd.DataFrame(member_records)
        definition_record = pd.DataFrame(
            [
                {
                    "region_definition_id": stable_id("region", f"CSA{regional['csa_code']}", regional["definition_vintage"]),
                    "region_code": f"CSA{regional['csa_code']}",
                    "name": regional["name"],
                    "definition_vintage": str(regional["definition_vintage"]),
                    "member_count": len(members),
                    "definition_json": json_dumps(
                        {
                            "csa_code": regional["csa_code"],
                            "counties": members["geography_id"].tolist(),
                            "official_workbook": source["url"],
                        }
                    ),
                    "source_id": source_id,
                    "snapshot_id": snapshot.snapshot_id,
                    "retrieved_at": snapshot.retrieved_at,
                    "valid_from": date(2023, 7, 21),
                    "valid_to": None,
                    "geography_type": "combined_statistical_area",
                    "geography_id": regional["csa_code"],
                    "measure_type": "official_definition",
                    "confidence": 1.0,
                    "method_version": "0.2.0",
                }
            ]
        )
        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM standardized_record WHERE source_id=? AND entity_type IN ('region_definition','region_member')", [source_id])
            conn.execute("DELETE FROM region_member WHERE source_id=?", [source_id])
            conn.execute("DELETE FROM region_definition WHERE source_id=?", [source_id])
            conn.register("_region_definition", definition_record)
            conn.register("_region_members", members)
            conn.execute("INSERT INTO region_definition BY NAME SELECT * FROM _region_definition")
            conn.execute("INSERT INTO region_member BY NAME SELECT * FROM _region_members")
            conn.unregister("_region_definition")
            conn.unregister("_region_members")
            for table_name, id_column in (
                ("region_definition", "region_definition_id"),
                ("region_member", "region_member_id"),
            ):
                register_table_lineage(
                    conn,
                    table_name=table_name,
                    entity_type=table_name,
                    id_column=id_column,
                    source_id=source_id,
                    snapshot_id=snapshot.snapshot_id,
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        members.to_parquet(
            standardized_path(source_id, str(regional["definition_vintage"]), "csa408_membership.parquet"),
            index=False,
        )
        update_snapshot_details(conn, snapshot.snapshot_id, members)
        finish_run(
            conn,
            run_id,
            "SUCCEEDED",
            request_count=1,
            downloaded_bytes=snapshot.byte_size,
            inserted_records=len(members) + 1,
        )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "region_code": f"CSA{regional['csa_code']}",
            "county_equivalents": len(members),
            "raw_bytes": snapshot.byte_size,
        }
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise


def _prepare_tiger_frame(frame: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, int]:
    if frame.crs is None:
        frame = frame.set_crs("EPSG:4269")
    frame = frame.to_crs("EPSG:4326")
    frame = frame.loc[frame.geometry.notna() & ~frame.geometry.is_empty].copy()
    invalid_mask = ~frame.geometry.is_valid
    invalid = int(invalid_mask.sum())
    if invalid:
        frame.loc[invalid_mask, "geometry"] = frame.loc[invalid_mask].geometry.make_valid()
    return frame.reset_index(drop=True), invalid


def _regional_boundary_rows(
    frame: gpd.GeoDataFrame,
    *,
    source_id: str,
    geography_type: str,
    parent_getter: Any,
    snapshot_getter: Any,
    retrieved_getter: Any,
) -> pd.DataFrame:
    metric = frame.to_crs(geographies_config()["regional_analysis_crs"])
    records: list[dict[str, Any]] = []
    for position, (_, row) in enumerate(frame.iterrows()):
        geometry = row.geometry
        geoid = str(row["GEOID"])
        records.append(
            {
                "boundary_id": stable_id("boundary", geography_type, geoid),
                "geography_type": geography_type,
                "geography_id": geoid,
                "name": str(row.get("NAMELSAD") or row.get("NAME") or geoid),
                "parent_id": parent_getter(row),
                "geom_wkt": geometry.wkt,
                "area_m2": float(metric.geometry.iloc[position].area),
                "source_id": source_id,
                "snapshot_id": snapshot_getter(row),
                "retrieved_at": retrieved_getter(row),
                "valid_from": date(2024, 1, 1),
                "valid_to": date(2024, 12, 31),
                "measure_type": "administrative_boundary",
                "confidence": 1.0,
                "method_version": "0.2.0",
                "min_longitude": float(geometry.bounds[0]),
                "min_latitude": float(geometry.bounds[1]),
                "max_longitude": float(geometry.bounds[2]),
                "max_latitude": float(geometry.bounds[3]),
            }
        )
    return pd.DataFrame(records)


def _replace_boundary_source(
    conn: duckdb.DuckDBPyConnection,
    *,
    source_id: str,
    boundaries: pd.DataFrame,
    snapshots: list[Snapshot],
) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM standardized_record WHERE source_id=? AND entity_type='geo_boundary'", [source_id])
        conn.execute("DELETE FROM geo_boundary WHERE source_id=?", [source_id])
        conn.register("_regional_boundaries", boundaries)
        conn.execute("INSERT INTO geo_boundary BY NAME SELECT * FROM _regional_boundaries")
        conn.unregister("_regional_boundaries")
        for snapshot in snapshots:
            register_table_lineage(
                conn,
                table_name="geo_boundary",
                entity_type="geo_boundary",
                id_column="boundary_id",
                source_id=source_id,
                snapshot_id=snapshot.snapshot_id,
            )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.unregister("_regional_boundaries")
        except Exception:
            pass
        conn.execute("ROLLBACK")
        raise


def refresh_regional_counties(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "tiger_county_2024"
    run_id = begin_run(conn, source_id, "refresh")
    source = get_source(source_id)
    regional = geographies_config()["greater_nyc"]
    try:
        snapshot = download_snapshot(
            conn,
            source_id=source_id,
            vintage="2024",
            url=source["url"],
            filename="tl_2024_us_county.zip",
            run_id=run_id,
            valid_from=date(2024, 1, 1),
            valid_to=date(2024, 12, 31),
            timeout_seconds=600,
        )
        frame = gpd.read_file(snapshot.path)
        frame = frame.loc[frame["GEOID"].isin(GREATER_NYC_COUNTY_FIPS)].copy()
        frame, invalid = _prepare_tiger_frame(frame)
        if set(frame["GEOID"]) != set(GREATER_NYC_COUNTY_FIPS):
            raise ValueError("TIGER county extract does not cover all 30 configured CSA 408 county equivalents.")
        boundaries = _regional_boundary_rows(
            frame,
            source_id=source_id,
            geography_type="county_equivalent_2024",
            parent_getter=lambda row: f"CSA{regional['csa_code']}",
            snapshot_getter=lambda row: snapshot.snapshot_id,
            retrieved_getter=lambda row: snapshot.retrieved_at,
        )
        union_geometry = unary_union(frame.geometry.tolist())
        region_metric = gpd.GeoSeries([union_geometry], crs="EPSG:4326").to_crs(
            geographies_config()["regional_analysis_crs"]
        ).iloc[0]
        region_row = pd.DataFrame(
            [
                {
                    "boundary_id": stable_id("boundary", "combined_statistical_area", regional["csa_code"]),
                    "geography_type": "combined_statistical_area",
                    "geography_id": regional["csa_code"],
                    "name": regional["name"],
                    "parent_id": None,
                    "geom_wkt": union_geometry.wkt,
                    "area_m2": float(region_metric.area),
                    "source_id": source_id,
                    "snapshot_id": snapshot.snapshot_id,
                    "retrieved_at": snapshot.retrieved_at,
                    "valid_from": date(2024, 1, 1),
                    "valid_to": date(2024, 12, 31),
                    "measure_type": "derived_union_of_official_boundaries",
                    "confidence": 1.0,
                    "method_version": "0.2.0",
                    "min_longitude": float(union_geometry.bounds[0]),
                    "min_latitude": float(union_geometry.bounds[1]),
                    "max_longitude": float(union_geometry.bounds[2]),
                    "max_latitude": float(union_geometry.bounds[3]),
                }
            ]
        )
        boundaries = pd.concat([boundaries, region_row], ignore_index=True)
        _replace_boundary_source(conn, source_id=source_id, boundaries=boundaries, snapshots=[snapshot])
        frame.to_parquet(standardized_path(source_id, "2024", "csa408_counties.parquet"), index=False)
        update_snapshot_details(conn, snapshot.snapshot_id, frame, warnings=[f"invalid_geometries_repaired={invalid}"])
        finish_run(
            conn,
            run_id,
            "SUCCEEDED",
            request_count=1,
            downloaded_bytes=snapshot.byte_size,
            inserted_records=len(boundaries),
        )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "county_equivalents": len(frame),
            "region_union": 1,
            "invalid_repaired": invalid,
            "raw_bytes": snapshot.byte_size,
        }
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise


def refresh_regional_tracts(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "tiger_tract_2024"
    run_id = begin_run(conn, source_id, "refresh")
    snapshots: list[Snapshot] = []
    frames: list[gpd.GeoDataFrame] = []
    total_invalid = 0
    try:
        for state_fips in sorted(GREATER_NYC_STATE_FIPS):
            filename = f"tl_2024_{state_fips}_tract.zip"
            snapshot = download_snapshot(
                conn,
                source_id=source_id,
                vintage="2024",
                url=f"{TIGER_BASE}/TRACT/{filename}",
                filename=filename,
                run_id=run_id,
                valid_from=date(2024, 1, 1),
                valid_to=date(2024, 12, 31),
                timeout_seconds=300,
            )
            state_frame = gpd.read_file(snapshot.path)
            state_frame = state_frame.loc[
                (state_frame["STATEFP"] + state_frame["COUNTYFP"]).isin(GREATER_NYC_COUNTY_FIPS)
            ].copy()
            state_frame, invalid = _prepare_tiger_frame(state_frame)
            state_frame["_snapshot_id"] = snapshot.snapshot_id
            state_frame["_retrieved_at"] = snapshot.retrieved_at
            snapshots.append(snapshot)
            frames.append(state_frame)
            total_invalid += invalid
        frame = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs="EPSG:4326")
        observed_counties = set(frame["STATEFP"] + frame["COUNTYFP"])
        if observed_counties != set(GREATER_NYC_COUNTY_FIPS):
            raise ValueError(
                f"Regional tract coverage mismatch: missing={sorted(set(GREATER_NYC_COUNTY_FIPS) - observed_counties)}"
            )
        boundaries = _regional_boundary_rows(
            frame,
            source_id=source_id,
            geography_type="tract_2024",
            parent_getter=lambda row: str(row["GEOID"])[:5],
            snapshot_getter=lambda row: row["_snapshot_id"],
            retrieved_getter=lambda row: row["_retrieved_at"],
        )
        _replace_boundary_source(conn, source_id=source_id, boundaries=boundaries, snapshots=snapshots)
        frame.to_parquet(standardized_path(source_id, "2024", "csa408_tracts.parquet"), index=False)
        for snapshot in snapshots:
            state_rows = frame.loc[frame["_snapshot_id"] == snapshot.snapshot_id]
            update_snapshot_details(conn, snapshot.snapshot_id, state_rows, warnings=[f"invalid_geometries_repaired={total_invalid}"])
        finish_run(
            conn,
            run_id,
            "SUCCEEDED",
            request_count=len(snapshots),
            downloaded_bytes=sum(snapshot.byte_size for snapshot in snapshots),
            inserted_records=len(boundaries),
        )
        return {
            "run_id": run_id,
            "snapshots": [snapshot.snapshot_id for snapshot in snapshots],
            "tracts": len(boundaries),
            "county_equivalents": len(observed_counties),
            "invalid_repaired": total_invalid,
            "raw_bytes": sum(snapshot.byte_size for snapshot in snapshots),
        }
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise
