from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import geopandas as gpd
import pandas as pd
from shapely import from_wkt

from ..db import begin_run, finish_run
from ..provenance import Snapshot, download_snapshot
from ..storage import replace_source_from_relation
from ..util import sha256_file
from .common import (
    GREATER_NYC_COUNTY_FIPS,
    as_number,
    register_table_lineage,
    stable_id,
    standardized_path,
    update_snapshot_details,
)


STATES = ("ct", "nj", "ny", "pa")
KINDS = ("wac", "rac", "od_main", "od_aux", "xwalk")
BASE_URL = "https://lehd.ces.census.gov/data/lodes/LODES8"

INDUSTRY_LABELS = {
    "C000": "all_jobs",
    "CNS01": "agriculture_forestry_fishing_hunting",
    "CNS02": "mining_quarrying_oil_gas",
    "CNS03": "utilities",
    "CNS04": "construction",
    "CNS05": "manufacturing",
    "CNS06": "wholesale_trade",
    "CNS07": "retail_trade",
    "CNS08": "transportation_warehousing",
    "CNS09": "information",
    "CNS10": "finance_insurance",
    "CNS11": "real_estate_rental_leasing",
    "CNS12": "professional_scientific_technical",
    "CNS13": "management_of_companies",
    "CNS14": "administrative_support_waste",
    "CNS15": "educational_services",
    "CNS16": "health_care_social_assistance",
    "CNS17": "arts_entertainment_recreation",
    "CNS18": "accommodation_food_services",
    "CNS19": "other_services",
    "CNS20": "public_administration",
}


def _relative_path(state: str, kind: str) -> str:
    if kind == "xwalk":
        return f"{state}_xwalk.csv.gz"
    if kind in {"wac", "rac"}:
        return f"{kind}/{state}_{kind}_S000_JT00_2023.csv.gz"
    suffix = "main" if kind == "od_main" else "aux"
    return f"od/{state}_od_{suffix}_JT00_2023.csv.gz"


def _reusable_snapshot(
    conn: duckdb.DuckDBPyConnection,
    *,
    url: str,
    vintage: str,
) -> Snapshot | None:
    row = conn.execute(
        """
        SELECT snapshot_id, source_id, raw_path, metadata_path, sha256,
               byte_size, retrieved_at, vintage
        FROM dataset_snapshot
        WHERE source_id='lodes_2023' AND source_url=? AND vintage=? AND status='SUCCEEDED'
        ORDER BY retrieved_at DESC LIMIT 1
        """,
        [url, vintage],
    ).fetchone()
    if not row:
        return None
    path = Path(row[2])
    if not path.is_file() or sha256_file(path) != row[4]:
        return None
    return Snapshot(
        snapshot_id=row[0],
        source_id=row[1],
        path=path,
        metadata_path=Path(row[3]),
        sha256=row[4],
        byte_size=row[5],
        retrieved_at=row[6],
        vintage=row[7],
    )


def _download_files(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
) -> tuple[dict[tuple[str, str], Snapshot], int, int]:
    snapshots: dict[tuple[str, str], Snapshot] = {}
    downloaded_bytes = 0
    request_count = 0
    for state in STATES:
        for kind in KINDS:
            relative = _relative_path(state, kind)
            url = f"{BASE_URL}/{state}/{relative}"
            snapshot = _reusable_snapshot(conn, url=url, vintage="2023")
            if snapshot is None:
                snapshot = download_snapshot(
                    conn,
                    source_id="lodes_2023",
                    vintage="2023",
                    url=url,
                    filename=Path(relative).name,
                    run_id=run_id,
                    valid_from=date(2023, 1, 1),
                    valid_to=date(2023, 12, 31),
                    timeout_seconds=900,
                )
                downloaded_bytes += snapshot.byte_size
                request_count += 1
            snapshots[(state, kind)] = snapshot
    return snapshots, downloaded_bytes, request_count


def _prefix_sql(column: str) -> str:
    values = ",".join(f"'{value}'" for value in sorted(GREATER_NYC_COUNTY_FIPS))
    return f"substr(CAST({column} AS VARCHAR), 1, 5) IN ({values})"


def _read_filtered_csv(
    conn: duckdb.DuckDBPyConnection,
    path: Path,
    predicate: str,
    columns: str = "*",
) -> pd.DataFrame:
    escaped = str(path).replace("'", "''")
    return conn.execute(
        f"SELECT {columns} FROM read_csv_auto('{escaped}', header=true, all_varchar=true) WHERE {predicate}"
    ).fetchdf()


def _ct_target_xwalk(
    conn: duckdb.DuckDBPyConnection,
    path: Path,
    block_column: str,
) -> pd.DataFrame:
    """Map 2020 Connecticut blocks to the current planning-region county equivalents."""
    xwalk = _read_filtered_csv(conn, path, "TRUE", f"{block_column}, blklatdd, blklondd")
    xwalk[block_column] = xwalk[block_column].astype(str).str.zfill(15)
    xwalk["blklatdd"] = pd.to_numeric(xwalk["blklatdd"], errors="coerce")
    xwalk["blklondd"] = pd.to_numeric(xwalk["blklondd"], errors="coerce")
    xwalk = xwalk.loc[xwalk["blklatdd"].notna() & xwalk["blklondd"].notna()].copy()
    boundary_rows = conn.execute(
        """
        SELECT geography_id, geom_wkt
        FROM geo_boundary
        WHERE source_id='tiger_county_2024'
          AND geography_type='county_equivalent_2024'
          AND geography_id IN ('09120', '09190')
        ORDER BY geography_id
        """
    ).fetchall()
    if len(boundary_rows) != 2:
        raise ValueError("Load the official CSA 408 county boundaries before refreshing Connecticut LODES.")
    boundaries = gpd.GeoDataFrame(
        {
            "current_county_geoid": [row[0] for row in boundary_rows],
            "geometry": [from_wkt(row[1]) for row in boundary_rows],
        },
        geometry="geometry",
        crs="EPSG:4326",
    )
    points = gpd.GeoDataFrame(
        xwalk,
        geometry=gpd.points_from_xy(xwalk["blklondd"], xwalk["blklatdd"]),
        crs="EPSG:4326",
    )
    joined = gpd.sjoin(points, boundaries, how="inner", predicate="within")
    joined = joined.sort_values([block_column, "current_county_geoid"]).drop_duplicates(block_column)
    observed = set(joined["current_county_geoid"])
    if observed != {"09120", "09190"}:
        raise ValueError(f"Connecticut LODES planning-region mapping incomplete: observed={sorted(observed)}")
    return pd.DataFrame(joined[[block_column, "blklatdd", "blklondd", "current_county_geoid"]])


def _copy_filtered_csv(
    conn: duckdb.DuckDBPyConnection,
    path: Path,
    output: Path,
    predicate: str,
) -> int:
    source_path = str(path).replace("'", "''")
    output_path = str(output).replace("'", "''")
    conn.execute(
        f"COPY (SELECT * FROM read_csv_auto('{source_path}', header=true, all_varchar=true) WHERE {predicate}) "
        f"TO '{output_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    return int(conn.execute(f"SELECT count(*) FROM read_parquet('{output_path}')").fetchone()[0])


def _combine_parquet(conn: duckdb.DuckDBPyConnection, paths: list[Path], output: Path) -> int:
    escaped_paths = ",".join("'" + str(path).replace("'", "''") + "'" for path in paths)
    output_path = str(output).replace("'", "''")
    conn.execute(
        f"COPY (SELECT * FROM read_parquet([{escaped_paths}], union_by_name=true)) "
        f"TO '{output_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    return int(conn.execute(f"SELECT count(*) FROM read_parquet('{output_path}')").fetchone()[0])


def refresh_lodes(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "lodes_2023"
    run_id = begin_run(conn, source_id, "refresh")
    downloaded_bytes = 0
    request_count = 0
    try:
        snapshots, downloaded_bytes, request_count = _download_files(conn, run_id)
        wac_frames: list[pd.DataFrame] = []
        xwalk_frames: list[pd.DataFrame] = []
        for state in STATES:
            wac_snapshot = snapshots[(state, "wac")]
            xwalk_snapshot = snapshots[(state, "xwalk")]
            header = pd.read_csv(xwalk_snapshot.path, compression="gzip", nrows=0)
            block_column = "tabblk2020" if "tabblk2020" in header.columns else "tabblk2010"
            if state == "ct":
                xwalk = _ct_target_xwalk(conn, xwalk_snapshot.path, block_column)
                ct_target_blocks = xwalk[[block_column]].rename(columns={block_column: "block_geoid"})
                conn.register("_ct_target_blocks", ct_target_blocks)
                wac = _read_filtered_csv(
                    conn,
                    wac_snapshot.path,
                    "CAST(w_geocode AS VARCHAR) IN (SELECT block_geoid FROM _ct_target_blocks)",
                )
            else:
                xwalk = _read_filtered_csv(
                    conn,
                    xwalk_snapshot.path,
                    _prefix_sql(block_column),
                    f"{block_column}, blklatdd, blklondd",
                )
                xwalk["current_county_geoid"] = xwalk[block_column].astype(str).str.zfill(15).str[:5]
                wac = _read_filtered_csv(conn, wac_snapshot.path, _prefix_sql("w_geocode"))
            xwalk[block_column] = xwalk[block_column].astype(str).str.zfill(15)
            xwalk["blklatdd"] = pd.to_numeric(xwalk["blklatdd"], errors="coerce")
            xwalk["blklondd"] = pd.to_numeric(xwalk["blklondd"], errors="coerce")
            wac["w_geocode"] = wac["w_geocode"].astype(str).str.zfill(15)
            merged = wac.merge(
                xwalk[[block_column, "blklatdd", "blklondd", "current_county_geoid"]],
                left_on="w_geocode",
                right_on=block_column,
                how="left",
            )
            merged["_snapshot_id"] = wac_snapshot.snapshot_id
            merged["_retrieved_at"] = pd.to_datetime(wac_snapshot.retrieved_at, utc=True).tz_convert(None)
            merged["_state"] = state.upper()
            xwalk["_snapshot_id"] = xwalk_snapshot.snapshot_id
            xwalk["_state"] = state.upper()
            wac_frames.append(merged)
            xwalk_frames.append(xwalk)

        wac_region = pd.concat(wac_frames, ignore_index=True)
        xwalk_region = pd.concat(xwalk_frames, ignore_index=True)
        available_measures = [column for column in INDUSTRY_LABELS if column in wac_region.columns]
        long = wac_region.melt(
            id_vars=[
                "w_geocode", "blklatdd", "blklondd", "current_county_geoid",
                "_state", "_snapshot_id", "_retrieved_at",
            ],
            value_vars=available_measures,
            var_name="measure",
            value_name="value",
        )
        workforce = pd.DataFrame(
            {
                "workforce_estimate_id": long.apply(
                    lambda row: stable_id("lodes", row["w_geocode"], row["measure"]), axis=1
                ),
                "geography_id": long["w_geocode"],
                "geography_type": "census_block_2020",
                "segment": "S000",
                "job_type": "JT00",
                "measure": long["measure"],
                "value": as_number(long["value"]),
                "longitude": as_number(long["blklondd"]),
                "latitude": as_number(long["blklatdd"]),
                "current_county_geoid": long["current_county_geoid"],
                "state_abbr": long["_state"],
                "source_id": source_id,
                "snapshot_id": long["_snapshot_id"],
                "retrieved_at": long["_retrieved_at"],
                "valid_from": date(2023, 1, 1),
                "valid_to": date(2023, 12, 31),
                "measure_type": "administrative_estimate",
                "confidence": 0.9,
                "method_version": "0.2.0",
            }
        )
        if workforce["workforce_estimate_id"].duplicated().any():
            raise ValueError("Regional LODES transformation produced duplicate block-measure identifiers.")

        conn.register("_workforce", workforce)
        try:
            previous, published = replace_source_from_relation(
                conn,
                table_name="workforce_estimate",
                source_id=source_id,
                relation="_workforce",
                region="greater_nyc",
            )
        finally:
            conn.unregister("_workforce")
        if published != len(workforce):
            raise ValueError(f"LODES published row mismatch: {published:,}/{len(workforce):,}")

        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM standardized_record WHERE source_id=? AND entity_type='workforce_estimate'", [source_id])
            for state in STATES:
                register_table_lineage(
                    conn,
                    table_name="workforce_estimate",
                    entity_type="workforce_estimate",
                    id_column="workforce_estimate_id",
                    source_id=source_id,
                    snapshot_id=snapshots[(state, "wac")].snapshot_id,
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        wac_region.to_parquet(
            standardized_path(source_id, "2023", "greater_nyc_wac_s000_jt00.parquet"), index=False
        )
        xwalk_region.to_parquet(
            standardized_path(source_id, "2023", "greater_nyc_xwalk.parquet"), index=False
        )

        state_outputs: dict[str, list[Path]] = {"rac": [], "od_main": [], "od_aux": []}
        snapshot_rows: dict[tuple[str, str], int] = {}
        for state in STATES:
            for kind in ("rac", "od_main", "od_aux"):
                if state == "ct":
                    predicate = (
                        "CAST(h_geocode AS VARCHAR) IN (SELECT block_geoid FROM _ct_target_blocks)"
                        if kind == "rac"
                        else "(CAST(h_geocode AS VARCHAR) IN (SELECT block_geoid FROM _ct_target_blocks) "
                        "OR CAST(w_geocode AS VARCHAR) IN (SELECT block_geoid FROM _ct_target_blocks))"
                    )
                else:
                    predicate = _prefix_sql("h_geocode") if kind == "rac" else (
                        f"({_prefix_sql('h_geocode')} OR {_prefix_sql('w_geocode')})"
                    )
                output = standardized_path(source_id, "2023", f"{state}_{kind}_jt00_greater_nyc.parquet")
                rows = _copy_filtered_csv(conn, snapshots[(state, kind)].path, output, predicate)
                state_outputs[kind].append(output)
                snapshot_rows[(state, kind)] = rows

        combined_counts: dict[str, int] = {}
        for kind, paths in state_outputs.items():
            output = standardized_path(source_id, "2023", f"greater_nyc_{kind}_jt00.parquet")
            combined_counts[kind] = _combine_parquet(conn, paths, output)

        for state in STATES:
            wac_snapshot = snapshots[(state, "wac")]
            xwalk_snapshot = snapshots[(state, "xwalk")]
            update_snapshot_details(
                conn,
                wac_snapshot.snapshot_id,
                wac_region.loc[wac_region["_snapshot_id"] == wac_snapshot.snapshot_id],
            )
            update_snapshot_details(
                conn,
                xwalk_snapshot.snapshot_id,
                xwalk_region.loc[xwalk_region["_snapshot_id"] == xwalk_snapshot.snapshot_id],
            )
            for kind in ("rac", "od_main", "od_aux"):
                conn.execute(
                    "UPDATE dataset_snapshot SET row_count=? WHERE snapshot_id=?",
                    [snapshot_rows[(state, kind)], snapshots[(state, kind)].snapshot_id],
                )

        warnings = [
            "LODES C000 counts jobs; they are not guaranteed to represent distinct workers.",
            "Connecticut 2020 Census blocks are assigned to current planning-region county equivalents by their official LODES crosswalk internal points and 2024 TIGER boundaries.",
            "Auxiliary OD files cover workplaces in CT, NJ, NY and PA; outbound jobs in other states are represented in RAC totals but not in the locally retained OD detail.",
        ]
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            inserted_records=len(workforce),
            deleted_records=previous,
            warnings=warnings,
        )
        return {
            "run_id": run_id,
            "snapshots": {f"{state}_{kind}": value.snapshot_id for (state, kind), value in snapshots.items()},
            "states": [state.upper() for state in STATES],
            "county_equivalents": len(set(wac_region["current_county_geoid"])),
            "wac_blocks": len(wac_region),
            "workforce_measure_rows": len(workforce),
            "rac_rows_greater_nyc": combined_counts["rac"],
            "od_main_rows_involving_greater_nyc": combined_counts["od_main"],
            "od_aux_rows_involving_greater_nyc": combined_counts["od_aux"],
            "raw_bytes": downloaded_bytes,
            "reused_snapshots": len(snapshots) - request_count,
            "warnings": warnings,
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
    finally:
        try:
            conn.unregister("_ct_target_blocks")
        except Exception:
            pass
