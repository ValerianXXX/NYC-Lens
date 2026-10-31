from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from ..config import geographies_config
from ..util import json_dumps, utc_now


NYC_COUNTIES = {"005", "047", "061", "081", "085"}
NYC_COUNTY_FIPS = {f"36{county}" for county in NYC_COUNTIES}
GREATER_NYC_COUNTY_FIPS = frozenset(geographies_config()["greater_nyc"]["counties"])
GREATER_NYC_STATE_FIPS = frozenset(geographies_config()["greater_nyc"]["state_fips"])


def as_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace(
        {-666666666.0: pd.NA, -555555555.0: pd.NA, -333333333.0: pd.NA, -222222222.0: pd.NA}
    )


def as_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.date


def normalized_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return re.sub(r"[^A-Z0-9]+", " ", str(value).upper()).strip()


def stable_id(prefix: str, *parts: Any) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def update_snapshot_details(
    conn: duckdb.DuckDBPyConnection,
    snapshot_id: str,
    frame: pd.DataFrame,
    *,
    warnings: list[str] | None = None,
) -> None:
    schema = {column: str(dtype) for column, dtype in frame.dtypes.items()}
    conn.execute(
        "UPDATE dataset_snapshot SET row_count=?, schema_json=?::JSON, warnings_json=?::JSON WHERE snapshot_id=?",
        [len(frame), json_dumps(schema), json_dumps(warnings or []), snapshot_id],
    )


@contextmanager
def registered_frame(conn: duckdb.DuckDBPyConnection, name: str, frame: pd.DataFrame):
    """Release the temporary relation even when a publication is rolled back."""
    conn.register(name, frame)
    try:
        yield
    finally:
        conn.unregister(name)


def register_standardized_records(
    conn: duckdb.DuckDBPyConnection,
    frame: pd.DataFrame,
    *,
    entity_type: str,
    entity_id_column: str,
    source_id: str,
    snapshot_id: str,
    source_record_id_column: str | None = None,
    method_version: str = "0.1.0",
) -> None:
    if frame.empty:
        return
    records = pd.DataFrame(
        {
            "standardized_record_id": frame[entity_id_column].astype(str).map(
                lambda entity_id: stable_id("std", entity_type, entity_id, snapshot_id)
            ),
            "entity_type": entity_type,
            "entity_id": frame[entity_id_column].astype(str),
            "source_id": source_id,
            "snapshot_id": snapshot_id,
            "source_record_id": (
                frame[source_record_id_column].astype(str) if source_record_id_column else frame[entity_id_column].astype(str)
            ),
            "method_version": method_version,
            "standardized_at": utc_now(),
        }
    )
    records = records.drop_duplicates("standardized_record_id", keep="last")
    with registered_frame(conn, "_standardized_records", records):
        conn.execute(
            """
            INSERT INTO standardized_record
            SELECT incoming.*
            FROM _standardized_records incoming
            WHERE NOT EXISTS (
                SELECT 1 FROM standardized_record existing
                WHERE existing.standardized_record_id=incoming.standardized_record_id
            )
            """
        )


def register_table_lineage(
    conn: duckdb.DuckDBPyConnection,
    *,
    table_name: str,
    entity_type: str,
    id_column: str,
    source_id: str,
    snapshot_id: str | None,
) -> None:
    """Register deterministic entity-to-snapshot links after a table refresh."""
    allowed = {
        "address": "address_id",
        "region_definition": "region_definition_id",
        "region_member": "region_member_id",
        "parcel": "parcel_id",
        "building": "building_id",
        "geo_boundary": "boundary_id",
        "geo_cell": "cell_id",
        "census_estimate": "census_estimate_id",
        "workforce_estimate": "workforce_estimate_id",
        "business_location": "business_location_id",
        "poi": "poi_id",
        "license": "license_id",
        "inspection": "inspection_id",
        "transit_stop": "transit_stop_id",
        "transit_ridership": "transit_ridership_id",
        "hotel": "hotel_id",
        "short_term_rental": "short_term_rental_id",
        "traffic_observation": "traffic_observation_id",
        "mobility_proxy": "mobility_proxy_id",
        "health_indicator": "health_indicator_id",
        "environment_indicator": "environment_indicator_id",
        "property_record": "property_record_id",
        "regulatory_record": "regulatory_record_id",
        "transaction_case": "transaction_case_id",
    }
    if allowed.get(table_name) != id_column:
        raise ValueError(f"Unsupported lineage table: {table_name}.{id_column}")
    snapshot_filter = " AND snapshot_id=?" if snapshot_id is not None else ""
    parameters: list[Any] = [utc_now(), source_id]
    if snapshot_id is not None:
        parameters.append(snapshot_id)
    conn.execute(
        f"""
        INSERT INTO standardized_record
        WITH incoming AS (
            SELECT
                'std_' || substr(sha256('{entity_type}|' || {id_column} || '|' || snapshot_id), 1, 24)
                    AS standardized_record_id,
                '{entity_type}' AS entity_type,
                {id_column} AS entity_id,
                source_id,
                snapshot_id,
                {id_column} AS source_record_id,
                method_version,
                coalesce(retrieved_at, ?) AS standardized_at
            FROM {table_name}
            WHERE source_id=?{snapshot_filter}
        )
        SELECT incoming.*
        FROM incoming
        WHERE NOT EXISTS (
            SELECT 1 FROM standardized_record existing
            WHERE existing.standardized_record_id=incoming.standardized_record_id
        )
        """,
        parameters,
    )


def standardized_path(source_id: str, vintage: str, filename: str) -> Path:
    from ..paths import data_dir

    path = data_dir() / "standardized" / source_id / vintage
    path.mkdir(parents=True, exist_ok=True)
    return path / filename
