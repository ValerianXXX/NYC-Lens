from __future__ import annotations

from datetime import date
from typing import Any

import duckdb
import pandas as pd

from ..config import get_source
from ..db import begin_run, finish_run
from .common import (
    GREATER_NYC_COUNTY_FIPS,
    as_number,
    register_table_lineage,
    stable_id,
    standardized_path,
    update_snapshot_details,
)
from .socrata import download_socrata_csv, fetch_metadata_snapshot


MEASURES = {
    "CASTHMA": ("Current asthma among adults", "casthma_crudeprev", "casthma_crude95ci"),
    "DIABETES": ("Diagnosed diabetes among adults", "diabetes_crudeprev", "diabetes_crude95ci"),
    "OBESITY": ("Obesity among adults", "obesity_crudeprev", "obesity_crude95ci"),
    "ACCESS2": ("Lack of health insurance among adults aged 18-64", "access2_crudeprev", "access2_crude95ci"),
    "FOODINSECU": ("Food insecurity in the past 12 months among adults", "foodinsecu_crudeprev", "foodinsecu_crude95ci"),
}


def _ci_half_width(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    extracted = series.fillna("").astype(str).str.extract(
        r"\(?\s*([-+]?[0-9]*\.?[0-9]+)\s*,\s*([-+]?[0-9]*\.?[0-9]+)\s*\)?"
    )
    low = pd.to_numeric(extracted[0], errors="coerce")
    high = pd.to_numeric(extracted[1], errors="coerce")
    return (high - low).abs() / 2.0, series.fillna("").astype(str)


def _standardize(
    frame: pd.DataFrame,
    *,
    source_id: str,
    snapshot_id: str,
    retrieved_at: Any,
    estimate_year: int,
    release_year: int,
) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for indicator_id, (name, value_field, ci_field) in MEASURES.items():
        margin, ci_text = _ci_half_width(frame[ci_field])
        values = as_number(frame[value_field])
        current = pd.DataFrame(
            {
                "health_indicator_id": [
                    stable_id("health", source_id, tract, indicator_id, str(release_year))
                    for tract in frame["tractfips"]
                ],
                "indicator_id": indicator_id,
                "name": name,
                "measure": "model-based crude prevalence",
                "value": values,
                "unit": "percent",
                "margin_of_error": margin,
                "message": [
                    f"Published 95% confidence interval {ci}; tract-level model estimate, not an individual attribute."
                    if ci
                    else "Published confidence interval unavailable; tract-level model estimate, not an individual attribute."
                    for ci in ci_text
                ],
                "period": f"{estimate_year} estimate / {release_year} release",
                "source_id": source_id,
                "snapshot_id": snapshot_id,
                "retrieved_at": retrieved_at,
                "valid_from": date(estimate_year, 1, 1),
                "valid_to": date(estimate_year, 12, 31),
                "geography_type": f"census_tract_{estimate_year}",
                "geography_id": frame["tractfips"].astype(str),
                "measure_type": "small_area_model_based_crude_prevalence",
                "confidence": 0.68,
                "method_version": "0.2.0",
            }
        )
        rows.append(current)
    return pd.concat(rows, ignore_index=True)


def refresh_cdc_places(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "cdc_places_2025_tract"
    source = get_source(source_id)
    run_id = begin_run(conn, source_id, "refresh selected CDC PLACES 2025 tract estimates for CSA 408")
    request_count = 0
    downloaded_bytes = 0
    try:
        metadata = fetch_metadata_snapshot(conn, source_id, "2025-release-metadata", run_id)
        request_count += 1
        downloaded_bytes += metadata.byte_size
        base_fields = ["stateabbr", "countyname", "countyfips", "tractfips", "totalpopulation"]
        measure_fields = [field for spec in MEASURES.values() for field in spec[1:]]
        county_values = ",".join(f"'{value}'" for value in sorted(GREATER_NYC_COUNTY_FIPS))
        snapshot = download_socrata_csv(
            conn,
            source_id,
            "2025-release-csa408-selected-measures",
            run_id,
            select=",".join([*base_fields, *measure_fields]),
            where=f"countyfips in({county_values})",
            order="tractfips",
            limit=10_000,
            valid_from=date(2023, 1, 1),
            valid_to=date(2023, 12, 31),
        )
        request_count += 1
        downloaded_bytes += snapshot.byte_size
        frame = pd.read_csv(snapshot.path, dtype=str)
        expected = {*base_fields, *measure_fields}
        missing = sorted(expected - set(frame.columns))
        if missing:
            raise ValueError(f"CDC PLACES response is missing fields: {', '.join(missing)}")
        frame["countyfips"] = frame["countyfips"].astype(str).str.zfill(5)
        frame["tractfips"] = frame["tractfips"].astype(str).str.zfill(11)
        unexpected_counties = sorted(set(frame["countyfips"]) - set(GREATER_NYC_COUNTY_FIPS))
        if unexpected_counties:
            raise ValueError(f"CDC PLACES returned out-of-scope counties: {unexpected_counties}")
        frame = frame.drop_duplicates(subset=["tractfips"], keep="last").sort_values("tractfips")
        if len(frame) < 5_000 or frame["countyfips"].nunique() != 30:
            raise ValueError(
                f"CDC PLACES CSA coverage is unexpectedly low: {len(frame)} tracts / "
                f"{frame['countyfips'].nunique()} counties"
            )
        warnings = [
            "The Raw CSV intentionally projects only five selected health/access measures plus geography and population fields; the exact Socrata query is recorded in the snapshot manifest.",
            "PLACES values are small-area model estimates based on BRFSS, Census and ACS inputs; they are not measurements of a site and must never be assigned to an individual, household, address, parcel or building.",
            "Reported 95% confidence intervals are retained; the standardized margin_of_error field stores their half-width for transparent aggregation.",
        ]
        update_snapshot_details(conn, snapshot.snapshot_id, frame, warnings=warnings)
        health = _standardize(
            frame,
            source_id=source_id,
            snapshot_id=snapshot.snapshot_id,
            retrieved_at=snapshot.retrieved_at,
            estimate_year=2023,
            release_year=2025,
        )
        if health["health_indicator_id"].duplicated().any():
            raise ValueError("CDC PLACES standardized health IDs are not unique")
        parquet_path = standardized_path(source_id, "current", "health_indicators.parquet")
        health.to_parquet(parquet_path, index=False)
        previous = conn.execute(
            "SELECT count(*) FROM health_indicator WHERE source_id=?", [source_id]
        ).fetchone()[0]
        conn.execute("BEGIN")
        try:
            conn.execute(
                "DELETE FROM standardized_record WHERE entity_type='health_indicator' AND source_id=?",
                [source_id],
            )
            conn.execute("DELETE FROM health_indicator WHERE source_id=?", [source_id])
            conn.register("_cdc_places_health", health)
            conn.execute("INSERT INTO health_indicator BY NAME SELECT * FROM _cdc_places_health")
            conn.unregister("_cdc_places_health")
            register_table_lineage(
                conn,
                table_name="health_indicator",
                entity_type="health_indicator",
                id_column="health_indicator_id",
                source_id=source_id,
                snapshot_id=snapshot.snapshot_id,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            inserted_records=len(health),
            deleted_records=previous,
            warnings=warnings,
        )
        return {
            "run_id": run_id,
            "status": "SUCCEEDED_WITH_WARNINGS",
            "metadata_snapshot_id": metadata.snapshot_id,
            "data_snapshot_id": snapshot.snapshot_id,
            "tracts": len(frame),
            "counties": int(frame["countyfips"].nunique()),
            "health_indicator_rows": len(health),
            "raw_bytes": downloaded_bytes,
            "standardized_parquet": str(parquet_path),
            "warnings": warnings,
        }
    except Exception as exc:
        try:
            conn.unregister("_cdc_places_health")
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


def refresh_cdc_places_2024_fallback(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "cdc_places_2024_tract_fallback"
    run_id = begin_run(
        conn,
        source_id,
        "refresh CDC PLACES 2024 only for current-release missing CSA tracts",
    )
    request_count = 0
    downloaded_bytes = 0
    try:
        missing_rows = conn.execute(
            """
            SELECT DISTINCT geography_id
            FROM health_indicator
            WHERE source_id='cdc_places_2025_tract'
              AND indicator_id IN ('CASTHMA','DIABETES','OBESITY','ACCESS2','FOODINSECU')
            GROUP BY geography_id
            HAVING count(value)=0
            ORDER BY geography_id
            """
        ).fetchall()
        missing_tracts = {str(row[0]) for row in missing_rows}
        missing_counties = sorted({value[:5] for value in missing_tracts})
        if not missing_counties:
            previous = conn.execute(
                "SELECT count(*) FROM health_indicator WHERE source_id=?", [source_id]
            ).fetchone()[0]
            conn.execute(
                "DELETE FROM standardized_record WHERE entity_type='health_indicator' AND source_id=?",
                [source_id],
            )
            conn.execute("DELETE FROM health_indicator WHERE source_id=?", [source_id])
            finish_run(
                conn,
                run_id,
                "SKIPPED_NOT_DUE",
                deleted_records=previous,
                warnings=["The 2025 tract layer has no fully missing selected-measure tracts."],
            )
            return {
                "run_id": run_id,
                "status": "SKIPPED_NOT_DUE",
                "missing_tracts": 0,
                "fallback_rows": 0,
            }
        metadata = fetch_metadata_snapshot(conn, source_id, "2024-release-metadata", run_id)
        request_count += 1
        downloaded_bytes += metadata.byte_size
        base_fields = ["stateabbr", "countyname", "countyfips", "tractfips", "totalpopulation"]
        measure_fields = [field for spec in MEASURES.values() for field in spec[1:]]
        county_values = ",".join(f"'{value}'" for value in missing_counties)
        snapshot = download_socrata_csv(
            conn,
            source_id,
            "2024-release-current-missing-counties",
            run_id,
            select=",".join([*base_fields, *measure_fields]),
            where=f"countyfips in({county_values})",
            order="tractfips",
            limit=10_000,
            valid_from=date(2022, 1, 1),
            valid_to=date(2022, 12, 31),
        )
        request_count += 1
        downloaded_bytes += snapshot.byte_size
        frame = pd.read_csv(snapshot.path, dtype=str)
        frame["countyfips"] = frame["countyfips"].astype(str).str.zfill(5)
        frame["tractfips"] = frame["tractfips"].astype(str).str.zfill(11)
        frame = frame.loc[frame["tractfips"].isin(missing_tracts)].copy()
        frame = frame.drop_duplicates(subset=["tractfips"], keep="last").sort_values("tractfips")
        if len(frame) != len(missing_tracts):
            raise ValueError(
                f"CDC 2024 fallback coverage mismatch: expected {len(missing_tracts)}, found {len(frame)}"
            )
        warnings = [
            f"Fallback is limited to {len(missing_tracts)} tracts in current-release missing counties {missing_counties}; it is not used when 2025 values are available.",
            "These are 2022 small-area model estimates from the 2024 release and are always labeled as older fallback data in query results.",
            "Values are regional model estimates, never attributes of a person, household, address, parcel or building.",
        ]
        update_snapshot_details(conn, snapshot.snapshot_id, frame, warnings=warnings)
        health = _standardize(
            frame,
            source_id=source_id,
            snapshot_id=snapshot.snapshot_id,
            retrieved_at=snapshot.retrieved_at,
            estimate_year=2022,
            release_year=2024,
        )
        parquet_path = standardized_path(source_id, "current", "health_indicators.parquet")
        health.to_parquet(parquet_path, index=False)
        previous = conn.execute(
            "SELECT count(*) FROM health_indicator WHERE source_id=?", [source_id]
        ).fetchone()[0]
        conn.execute("BEGIN")
        try:
            conn.execute(
                "DELETE FROM standardized_record WHERE entity_type='health_indicator' AND source_id=?",
                [source_id],
            )
            conn.execute("DELETE FROM health_indicator WHERE source_id=?", [source_id])
            conn.register("_cdc_places_fallback_health", health)
            conn.execute(
                "INSERT INTO health_indicator BY NAME SELECT * FROM _cdc_places_fallback_health"
            )
            conn.unregister("_cdc_places_fallback_health")
            register_table_lineage(
                conn,
                table_name="health_indicator",
                entity_type="health_indicator",
                id_column="health_indicator_id",
                source_id=source_id,
                snapshot_id=snapshot.snapshot_id,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            inserted_records=len(health),
            deleted_records=previous,
            warnings=warnings,
        )
        return {
            "run_id": run_id,
            "status": "SUCCEEDED_WITH_WARNINGS",
            "metadata_snapshot_id": metadata.snapshot_id,
            "data_snapshot_id": snapshot.snapshot_id,
            "missing_counties": missing_counties,
            "fallback_tracts": len(frame),
            "health_indicator_rows": len(health),
            "raw_bytes": downloaded_bytes,
            "standardized_parquet": str(parquet_path),
            "warnings": warnings,
        }
    except Exception as exc:
        try:
            conn.unregister("_cdc_places_fallback_health")
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
