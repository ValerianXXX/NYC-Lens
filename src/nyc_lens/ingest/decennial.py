from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import duckdb
import httpx
import pandas as pd

from ..config import get_source
from ..db import begin_run, finish_run
from ..provenance import Snapshot, download_snapshot
from ..util import sha256_file
from .common import as_number, register_table_lineage, stable_id, standardized_path, update_snapshot_details


FIELD_DEFINITIONS_URL = (
    "https://www2.census.gov/programs-surveys/decennial/rdo/about/2020-census-program/"
    "Phase3/SupportMaterials/2020_PLSummaryFile_FieldNames.xlsx"
)
DECENNIAL_VARIABLES = {
    "P0010001": "Total population",
    "P0020002": "Hispanic or Latino",
    "P0020005": "Not Hispanic or Latino: White alone",
    "P0020006": "Not Hispanic or Latino: Black or African American alone",
    "P0020008": "Not Hispanic or Latino: Asian alone",
    "P0020011": "Not Hispanic or Latino: Population of two or more races",
}
NYC_COUNTIES = {"005", "047", "061", "081", "085"}


def _reusable_failed_snapshot(
    conn: duckdb.DuckDBPyConnection,
    source_url: str,
) -> Snapshot | None:
    row = conn.execute(
        """
        SELECT d.snapshot_id, d.source_id, d.raw_path, d.metadata_path,
               d.sha256, d.byte_size, d.retrieved_at, d.vintage
        FROM dataset_snapshot d
        JOIN ingestion_run r ON r.run_id=d.run_id
        WHERE d.source_id='census_decennial_2020_pl' AND d.source_url=?
          AND d.status='SUCCEEDED' AND r.status='FAILED'
          AND NOT EXISTS (
              SELECT 1 FROM ingestion_run completed
              WHERE completed.source_id=d.source_id
                AND completed.status IN ('SUCCEEDED','SUCCEEDED_WITH_WARNINGS')
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


def _read_nyc_block_geography(
    archive: ZipFile,
    member: str,
    fields: list[str],
) -> pd.DataFrame:
    with archive.open(member) as handle:
        geography = pd.read_csv(
            handle,
            sep="|",
            names=fields,
            usecols=["SUMLEV", "LOGRECNO", "STATE", "COUNTY", "TRACT", "BLOCK"],
            dtype=str,
            low_memory=False,
        )
    geography = geography.loc[
        geography["SUMLEV"].eq("750")
        & geography["STATE"].eq("36")
        & geography["COUNTY"].isin(NYC_COUNTIES)
    ].copy()
    geography["geography_id"] = (
        geography["STATE"]
        + geography["COUNTY"]
        + geography["TRACT"]
        + geography["BLOCK"]
    )
    return geography[["LOGRECNO", "geography_id"]]


def _to_decennial_estimates(
    wide: pd.DataFrame,
    *,
    source_id: str,
    snapshot_id: str,
    retrieved_at: Any,
) -> pd.DataFrame:
    records: list[pd.DataFrame] = []
    for variable, label in DECENNIAL_VARIABLES.items():
        values = as_number(wide[variable])
        records.append(
            pd.DataFrame(
                {
                    "census_estimate_id": wide["geography_id"].map(
                        lambda geoid: stable_id("decennial", geoid, variable)
                    ),
                    "geography_id": wide["geography_id"],
                    "geography_type": "census_block",
                    "variable": variable,
                    "label": label,
                    "estimate": values,
                    "margin_of_error": None,
                    "unit": "people",
                    "survey_period": "2020 Census P.L. 94-171",
                    "source_id": source_id,
                    "snapshot_id": snapshot_id,
                    "retrieved_at": retrieved_at,
                    "valid_from": date(2020, 4, 1),
                    "valid_to": date(2020, 4, 1),
                    "measure_type": "decennial_enumeration_count",
                    "confidence": 0.99,
                    "method_version": "0.1.0",
                }
            )
        )
    return pd.concat(records, ignore_index=True)


def refresh_decennial_pl(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "census_decennial_2020_pl"
    source = get_source(source_id)
    run_id = begin_run(conn, source_id, "refresh official legacy summary file")
    request_count = 0
    downloaded_bytes = 0
    try:
        with httpx.Client(follow_redirects=True, timeout=900) as client:
            definitions = _reusable_failed_snapshot(conn, FIELD_DEFINITIONS_URL)
            if definitions is None:
                definitions = download_snapshot(
                    conn,
                    source_id=source_id,
                    vintage="2020",
                    url=FIELD_DEFINITIONS_URL,
                    filename="2020_PLSummaryFile_FieldNames.xlsx",
                    run_id=run_id,
                    valid_from=date(2020, 4, 1),
                    valid_to=date(2020, 4, 1),
                    client=client,
                )
                request_count += 1
                downloaded_bytes += definitions.byte_size
            snapshot = _reusable_failed_snapshot(conn, source["url"])
            if snapshot is None:
                snapshot = download_snapshot(
                    conn,
                    source_id=source_id,
                    vintage="2020",
                    url=source["url"],
                    filename="ny2020.pl.zip",
                    run_id=run_id,
                    valid_from=date(2020, 4, 1),
                    valid_to=date(2020, 4, 1),
                    timeout_seconds=900,
                    client=client,
                )
                request_count += 1
                downloaded_bytes += snapshot.byte_size
        geo_fields = pd.read_excel(
            definitions.path,
            sheet_name="2020 P.L. Geoheader Fields",
        ).columns.tolist()
        segment_fields = pd.read_excel(
            definitions.path,
            sheet_name="2020 P.L. Segment 1 Fields",
        ).columns.tolist()
        with ZipFile(snapshot.path) as archive:
            members = archive.namelist()
            geo_member = next(name for name in members if name.lower().endswith("geo2020.pl"))
            segment_member = next(name for name in members if name.lower().endswith("000012020.pl"))
            geography = _read_nyc_block_geography(archive, geo_member, geo_fields)
            with archive.open(segment_member) as handle:
                segment = pd.read_csv(
                    handle,
                    sep="|",
                    names=segment_fields,
                    usecols=["LOGRECNO", *DECENNIAL_VARIABLES],
                    dtype=str,
                    low_memory=False,
                )
        wide = geography.merge(segment, on="LOGRECNO", how="inner", validate="one_to_one")
        local_blocks = {
            row[0]
            for row in conn.execute(
                "SELECT geography_id FROM geo_boundary WHERE geography_type='census_block'"
            ).fetchall()
        }
        source_block_count = len(wide)
        wide = wide.loc[wide["geography_id"].isin(local_blocks)].copy()
        if len(wide) < 35_000:
            raise ValueError(f"Unexpected NYC block coverage after boundary alignment: {len(wide)}")
        if wide["geography_id"].duplicated().any():
            raise ValueError("Duplicate Census block identifiers in P.L. summary file")
        update_snapshot_details(
            conn,
            snapshot.snapshot_id,
            wide,
            warnings=[
                f"NYC county block records before shoreline-boundary alignment: {source_block_count}.",
                f"Blocks retained because they exist in the local official NYC planning boundary layer: {len(wide)}.",
            ],
        )
        estimates = _to_decennial_estimates(
            wide,
            source_id=source_id,
            snapshot_id=snapshot.snapshot_id,
            retrieved_at=snapshot.retrieved_at,
        )
        conn.execute("DELETE FROM census_estimate WHERE source_id=?", [source_id])
        conn.register("_decennial_estimates", estimates)
        conn.execute("INSERT INTO census_estimate BY NAME SELECT * FROM _decennial_estimates")
        conn.unregister("_decennial_estimates")
        register_table_lineage(
            conn,
            table_name="census_estimate",
            entity_type="census_estimate",
            id_column="census_estimate_id",
            source_id=source_id,
            snapshot_id=snapshot.snapshot_id,
        )
        estimates.to_parquet(
            standardized_path(source_id, "2020", "nyc_census_block_counts.parquet"),
            index=False,
        )
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            inserted_records=len(estimates),
            warnings=[
                "Census Data API required a key, so the official immutable legacy summary file was used.",
                "Decennial counts have no ACS-style sampling margin of error; disclosure-avoidance methods still apply.",
                "Only blocks present in the local official NYC shoreline-clipped boundary layer were standardized.",
            ],
        )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "nyc_county_blocks_in_source": source_block_count,
            "nyc_blocks_aligned_to_boundaries": len(wide),
            "variables": list(DECENNIAL_VARIABLES),
            "estimate_rows": len(estimates),
            "raw_artifact_bytes": definitions.byte_size + snapshot.byte_size,
            "downloaded_bytes_this_run": downloaded_bytes,
            "access_method": "official_legacy_summary_file",
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
