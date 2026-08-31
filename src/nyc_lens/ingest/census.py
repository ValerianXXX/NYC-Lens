from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import geopandas as gpd
import httpx
import pandas as pd

from ..config import get_source, geographies_config
from ..db import begin_run, finish_run
from ..provenance import Snapshot, download_snapshot, save_bytes_snapshot
from ..util import sha256_file, utc_now
from .common import (
    GREATER_NYC_COUNTY_FIPS,
    GREATER_NYC_STATE_FIPS,
    as_number,
    register_table_lineage,
    stable_id,
    standardized_path,
    update_snapshot_details,
)


ACS_SELECTIONS: dict[str, dict[str, str]] = {
    "B01001": {
        "001": "population_total",
        **{f"{line:03d}": f"male_age_band_{line:03d}" for line in range(3, 26)},
        **{f"{line:03d}": f"female_age_band_{line:03d}" for line in range(27, 50)},
    },
    "B01002": {"001": "median_age"},
    "B01003": {"001": "population_total"},
    "B03002": {
        "001": "population_race_ethnicity_total",
        "003": "not_hispanic_white_alone",
        "004": "not_hispanic_black_alone",
        "006": "not_hispanic_asian_alone",
        "012": "hispanic_or_latino",
    },
    "B05002": {"001": "nativity_total", "002": "native", "013": "foreign_born"},
    "B11001": {"001": "households_total", "002": "family_households", "007": "nonfamily_households"},
    "B15003": {
        "001": "education_population_25_plus",
        "022": "bachelors_degree",
        "023": "masters_degree",
        "024": "professional_degree",
        "025": "doctorate_degree",
    },
    "C16001": {
        "001": "language_population_5_plus",
        "002": "english_only",
        "003": "spanish",
        "006": "other_indo_european",
        "009": "asian_pacific_languages",
        "012": "other_languages",
    },
    "B19013": {"001": "median_household_income"},
    "B19301": {"001": "per_capita_income"},
    "B23025": {
        "001": "employment_population_16_plus",
        "003": "civilian_labor_force",
        "004": "employed",
        "005": "unemployed",
    },
    "C24010": {
        "001": "civilian_employed_16_plus",
        "003": "male_management_business_science_arts",
        "019": "male_service",
        "027": "male_sales_office",
        "030": "male_natural_resources_construction_maintenance",
        "034": "male_production_transport_material_moving",
        "039": "female_management_business_science_arts",
        "055": "female_service",
        "063": "female_sales_office",
        "066": "female_natural_resources_construction_maintenance",
        "070": "female_production_transport_material_moving",
    },
    "B08301": {
        "001": "workers_commute_total",
        "003": "drove_alone",
        "010": "public_transportation",
        "019": "walked",
        "021": "worked_from_home",
    },
    "B08201": {"001": "households_vehicle_total", "002": "no_vehicle_available"},
    "B25003": {"001": "occupied_housing_units", "002": "owner_occupied", "003": "renter_occupied"},
}


def refresh_tiger_boundaries(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "tiger_bg_2024"
    run_id = begin_run(conn, source_id, "refresh")
    try:
        snapshots: list[Snapshot] = []
        frames: list[gpd.GeoDataFrame] = []
        invalid_before = 0
        for state_fips in sorted(GREATER_NYC_STATE_FIPS):
            filename = f"tl_2024_{state_fips}_bg.zip"
            url = f"https://www2.census.gov/geo/tiger/TIGER2024/BG/{filename}"
            snapshot = _reusable_immutable_snapshot(conn, url, "2024")
            if snapshot is None:
                snapshot = download_snapshot(
                    conn,
                    source_id=source_id,
                    vintage="2024",
                    url=url,
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
            state_frame = state_frame.to_crs("EPSG:4326")
            invalid_mask = ~state_frame.geometry.is_valid
            invalid_before += int(invalid_mask.sum())
            if invalid_mask.any():
                state_frame.loc[invalid_mask, "geometry"] = state_frame.loc[invalid_mask].geometry.make_valid()
            state_frame["_snapshot_id"] = snapshot.snapshot_id
            state_frame["_retrieved_at"] = pd.to_datetime(snapshot.retrieved_at, utc=True).tz_convert(None)
            snapshots.append(snapshot)
            frames.append(state_frame)
        frame = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs="EPSG:4326")
        observed_counties = set(frame["STATEFP"] + frame["COUNTYFP"])
        if observed_counties != set(GREATER_NYC_COUNTY_FIPS):
            raise ValueError(
                f"Greater NYC TIGER block-group coverage mismatch: missing={sorted(set(GREATER_NYC_COUNTY_FIPS) - observed_counties)}"
            )
        metric = frame.to_crs(geographies_config()["regional_analysis_crs"])
        boundaries = pd.DataFrame(
            {
                "boundary_id": frame["GEOID"].map(lambda value: stable_id("boundary", "block_group", value)),
                "geography_type": "block_group",
                "geography_id": frame["GEOID"],
                "name": frame.get("NAMELSAD"),
                "parent_id": frame["STATEFP"] + frame["COUNTYFP"] + frame["TRACTCE"],
                "geom_wkt": frame.geometry.to_wkt(rounding_precision=7),
                "area_m2": metric.geometry.area,
                "source_id": source_id,
                "snapshot_id": frame["_snapshot_id"],
                "retrieved_at": frame["_retrieved_at"],
                "valid_from": date(2024, 1, 1),
                "valid_to": date(2024, 12, 31),
                "measure_type": "administrative_boundary",
                "confidence": 1.0,
                "method_version": "0.2.0",
                "min_longitude": frame.geometry.bounds["minx"],
                "min_latitude": frame.geometry.bounds["miny"],
                "max_longitude": frame.geometry.bounds["maxx"],
                "max_latitude": frame.geometry.bounds["maxy"],
            }
        )
        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM standardized_record WHERE source_id=? AND entity_type='geo_boundary'", [source_id])
            conn.execute("DELETE FROM geo_boundary WHERE source_id=? AND geography_type='block_group'", [source_id])
            conn.register("_boundaries", boundaries)
            conn.execute("INSERT INTO geo_boundary BY NAME SELECT * FROM _boundaries")
            conn.unregister("_boundaries")
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
            conn.execute("ROLLBACK")
            raise
        output = standardized_path(source_id, "2024", "greater_nyc_block_groups.parquet")
        frame[["GEOID", "STATEFP", "COUNTYFP", "TRACTCE", "BLKGRPCE", "NAMELSAD", "geometry"]].to_parquet(output, index=False)
        for snapshot in snapshots:
            state_rows = frame.loc[frame["_snapshot_id"] == snapshot.snapshot_id]
            update_snapshot_details(conn, snapshot.snapshot_id, state_rows, warnings=[f"invalid_geometries_repaired={invalid_before}"])
        downloaded_bytes = sum(
            snapshot.byte_size for snapshot in snapshots if conn.execute(
                "SELECT run_id=? FROM dataset_snapshot WHERE snapshot_id=?", [run_id, snapshot.snapshot_id]
            ).fetchone()[0]
        )
        finish_run(conn, run_id, "SUCCEEDED", request_count=4, downloaded_bytes=downloaded_bytes, inserted_records=len(boundaries))
        return {
            "run_id": run_id,
            "snapshots": [snapshot.snapshot_id for snapshot in snapshots],
            "block_groups": len(boundaries),
            "county_equivalents": len(observed_counties),
            "invalid_repaired": invalid_before,
            "raw_bytes": downloaded_bytes,
        }
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise


def _metadata_snapshot(
    conn: duckdb.DuckDBPyConnection,
    table_id: str,
    run_id: str,
    client: httpx.Client,
) -> tuple[dict[str, Any], int]:
    url = f"https://api.census.gov/data/2024/acs/acs5/groups/{table_id}.json"
    reusable = _reusable_immutable_snapshot(conn, url, "2020-2024")
    if reusable:
        return json.loads(reusable.path.read_text(encoding="utf-8")), 0
    response = client.get(url, headers={"User-Agent": "NYC-Lens/0.1"})
    response.raise_for_status()
    save_bytes_snapshot(
        conn,
        source_id="acs_2024_5yr",
        vintage="2020-2024",
        filename=f"{table_id}_dictionary.json",
        content=response.content,
        source_url=url,
        run_id=run_id,
        content_type="application/json",
        query={"kind": "ACS group data dictionary", "table_id": table_id},
        valid_from=date(2020, 1, 1),
        valid_to=date(2024, 12, 31),
        http_status=response.status_code,
    )
    return response.json(), len(response.content)


def _reusable_failed_snapshot(
    conn: duckdb.DuckDBPyConnection,
    source_url: str,
) -> Snapshot | None:
    """Reuse a verified download only while recovering an interrupted refresh."""
    row = conn.execute(
        """
        SELECT d.snapshot_id, d.source_id, d.raw_path, d.metadata_path,
               d.sha256, d.byte_size, d.retrieved_at, d.vintage
        FROM dataset_snapshot d
        JOIN ingestion_run r ON r.run_id=d.run_id
        WHERE d.source_id='acs_2024_5yr' AND d.source_url=?
          AND d.status='SUCCEEDED' AND r.status='FAILED'
          AND NOT EXISTS (
              SELECT 1 FROM ingestion_run completed
              WHERE completed.source_id=d.source_id
                AND completed.status='SUCCEEDED'
                AND completed.completed_at > d.retrieved_at
          )
        ORDER BY d.retrieved_at DESC LIMIT 1
        """,
        [source_url],
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


def _reusable_immutable_snapshot(
    conn: duckdb.DuckDBPyConnection,
    source_url: str,
    vintage: str,
) -> Snapshot | None:
    """Reuse an already verified local copy of a fixed-vintage official file."""
    row = conn.execute(
        """
        SELECT snapshot_id, source_id, raw_path, metadata_path, sha256,
               byte_size, retrieved_at, vintage
        FROM dataset_snapshot
        WHERE source_url=? AND vintage=? AND status='SUCCEEDED'
        ORDER BY retrieved_at DESC LIMIT 1
        """,
        [source_url, vintage],
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


def _confidence(estimate: pd.Series, moe: pd.Series) -> pd.Series:
    estimate_num = as_number(estimate)
    moe_num = as_number(moe).abs()
    ratio = moe_num / estimate_num.abs().clip(lower=1)
    confidence = pd.Series(0.9, index=estimate.index, dtype=float)
    confidence.loc[ratio > 0.2] = 0.8
    confidence.loc[ratio > 0.5] = 0.6
    confidence.loc[ratio > 1.0] = 0.4
    confidence.loc[estimate_num.isna() | moe_num.isna()] = 0.0
    return confidence


def _load_acs_table(
    path: Path,
    table_id: str,
    selected: dict[str, str],
    metadata: dict[str, Any],
    snapshot_id: str,
    retrieved_at: Any,
) -> pd.DataFrame:
    requested_columns = ["GEO_ID"]
    for line in selected:
        requested_columns.extend([f"{table_id}_E{line}", f"{table_id}_M{line}"])
    # The national table files are much larger than the NYC slice. Filter in
    # bounded chunks so refreshes remain safe on ordinary laptops.
    block_group_chunks: list[pd.DataFrame] = []
    tract_chunks: list[pd.DataFrame] = []
    for chunk in pd.read_csv(
        path,
        sep="|",
        usecols=requested_columns,
        dtype=str,
        chunksize=100_000,
    ):
        geography = chunk["GEO_ID"]
        county_geoid = geography.str.slice(9, 14)
        block_groups = chunk.loc[
            geography.str.startswith("1500000US", na=False) & county_geoid.isin(GREATER_NYC_COUNTY_FIPS)
        ]
        tracts = chunk.loc[
            geography.str.startswith("1400000US", na=False) & county_geoid.isin(GREATER_NYC_COUNTY_FIPS)
        ]
        if not block_groups.empty:
            block_group_chunks.append(block_groups.copy())
        if not tracts.empty:
            tract_chunks.append(tracts.copy())
    if block_group_chunks:
        frame = pd.concat(block_group_chunks, ignore_index=True)
        summary_level = "block_group"
        geoid_pattern = r"^1500000US"
    elif tract_chunks:
        frame = pd.concat(tract_chunks, ignore_index=True)
        summary_level = "tract"
        geoid_pattern = r"^1400000US"
    else:
        frame = pd.DataFrame(columns=requested_columns)
        summary_level = "unavailable"
        geoid_pattern = r"^(?:150|140)0000US"
    records: list[pd.DataFrame] = []
    variables = metadata.get("variables", {})
    for line, short_label in selected.items():
        estimate_column = f"{table_id}_E{line}"
        moe_column = f"{table_id}_M{line}"
        api_variable = f"{table_id}_{line}E"
        label = variables.get(api_variable, {}).get("label", short_label)
        estimate = as_number(frame[estimate_column])
        moe = as_number(frame[moe_column])
        records.append(
            pd.DataFrame(
                {
                    "census_estimate_id": frame["GEO_ID"].map(lambda value: stable_id("acs", value, table_id, line)),
                    "geography_id": frame["GEO_ID"].str.replace(geoid_pattern, "", regex=True),
                    "geography_type": summary_level,
                    "variable": f"{table_id}_{line}",
                    "label": label,
                    "estimate": estimate,
                    "margin_of_error": moe,
                    "unit": "USD" if table_id in {"B19013", "B19301"} else ("years" if table_id == "B01002" else "count"),
                    "survey_period": "2020-2024 ACS 5-Year",
                    "source_id": "acs_2024_5yr",
                    "snapshot_id": snapshot_id,
                    "retrieved_at": retrieved_at,
                    "valid_from": date(2020, 1, 1),
                    "valid_to": date(2024, 12, 31),
                    "measure_type": "survey_estimate",
                    "confidence": _confidence(estimate, moe),
                    "method_version": "0.2.0",
                }
            )
        )
    return pd.concat(records, ignore_index=True)


def refresh_acs(conn: duckdb.DuckDBPyConnection, tables: list[str] | None = None) -> dict[str, Any]:
    source_id = "acs_2024_5yr"
    run_id = begin_run(conn, source_id, "refresh")
    selected_tables = tables or list(ACS_SELECTIONS)
    total_bytes = 0
    total_records = 0
    snapshots: list[str] = []
    outputs: list[pd.DataFrame] = []
    try:
        with httpx.Client(follow_redirects=True, timeout=900) as client:
            for table_id in selected_tables:
                if table_id not in ACS_SELECTIONS:
                    raise ValueError(f"Unsupported configured ACS table: {table_id}")
                metadata, metadata_bytes = _metadata_snapshot(conn, table_id, run_id, client)
                total_bytes += metadata_bytes
                filename = f"acsdt5y2024-{table_id.lower()}.dat"
                url = f"https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/{filename}"
                snapshot = _reusable_immutable_snapshot(conn, url, "2020-2024")
                if snapshot is None:
                    snapshot = download_snapshot(
                        conn,
                        source_id=source_id,
                        vintage="2020-2024",
                        url=url,
                        filename=filename,
                        run_id=run_id,
                        valid_from=date(2020, 1, 1),
                        valid_to=date(2024, 12, 31),
                        timeout_seconds=900,
                        client=client,
                    )
                    total_bytes += snapshot.byte_size
                snapshots.append(snapshot.snapshot_id)
                long_frame = _load_acs_table(
                    snapshot.path,
                    table_id,
                    ACS_SELECTIONS[table_id],
                    metadata,
                    snapshot.snapshot_id,
                    snapshot.retrieved_at,
                )
                update_snapshot_details(conn, snapshot.snapshot_id, long_frame)
                long_frame.to_parquet(
                    standardized_path(
                        source_id,
                        "2020-2024",
                        f"{table_id.lower()}_greater_nyc_estimates.parquet",
                    ),
                    index=False,
                )
                outputs.append(long_frame)
                total_records += len(long_frame)
        combined = pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()
        if not combined.empty:
            # Reused DuckDB timestamps are naive while freshly downloaded
            # snapshot timestamps are timezone-aware. Normalize before Arrow
            # registration so DuckDB receives one unambiguous TIMESTAMP type.
            combined["retrieved_at"] = pd.to_datetime(
                combined["retrieved_at"], utc=True
            ).dt.tz_convert(None)
            variables = [f"{table}_{line}" for table in selected_tables for line in ACS_SELECTIONS[table]]
            placeholders = ",".join("?" for _ in variables)
            conn.execute(
                f"DELETE FROM census_estimate WHERE source_id=? AND variable IN ({placeholders})",
                [source_id, *variables],
            )
            conn.register("_acs_estimates", combined)
            conn.execute("INSERT INTO census_estimate BY NAME SELECT * FROM _acs_estimates")
            conn.unregister("_acs_estimates")
            for snapshot_id in snapshots:
                register_table_lineage(
                    conn,
                    table_name="census_estimate",
                    entity_type="census_estimate",
                    id_column="census_estimate_id",
                    source_id=source_id,
                    snapshot_id=snapshot_id,
                )
            if set(selected_tables) == set(ACS_SELECTIONS):
                combined.to_parquet(
                    standardized_path(
                        source_id, "2020-2024", "greater_nyc_selected_estimates.parquet"
                    ),
                    index=False,
                )
        finish_run(
            conn,
            run_id,
            "SUCCEEDED",
            request_count=len(selected_tables) * 2,
            downloaded_bytes=total_bytes,
            inserted_records=total_records,
            warnings=["Census Data API required a key; official table-based Summary Files were used instead."],
        )
        return {
            "run_id": run_id,
            "snapshots": snapshots,
            "tables": selected_tables,
            "estimate_rows": total_records,
            "raw_bytes": total_bytes,
            "access_method": "official_table_based_summary_files",
        }
    except Exception as exc:
        finish_run(
            conn,
            run_id,
            "FAILED",
            request_count=len(snapshots),
            downloaded_bytes=total_bytes,
            inserted_records=total_records,
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        raise
