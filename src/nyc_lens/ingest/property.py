from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import duckdb
import pandas as pd

from ..db import begin_run, finish_run
from ..config import get_source
from ..provenance import Snapshot, save_file_snapshot
from ..util import json_dumps, utc_now
from ..storage import replace_source_from_relation
from .common import register_table_lineage, stable_id, standardized_path
from .socrata import download_socrata_csv, fetch_metadata_snapshot


CHUNK_SIZE = 100_000
MAX_SOCRATA_ROWS = 2_000_000

BOROUGH_CODES = {
    "1": "1",
    "MANHATTAN": "1",
    "MN": "1",
    "NEW YORK": "1",
    "2": "2",
    "BRONX": "2",
    "BX": "2",
    "3": "3",
    "BROOKLYN": "3",
    "BK": "3",
    "KINGS": "3",
    "4": "4",
    "QUEENS": "4",
    "QN": "4",
    "5": "5",
    "STATEN ISLAND": "5",
    "SI": "5",
    "RICHMOND": "5",
}

PROHIBITED_FIELD_TOKENS = {
    "applicant_first_name",
    "applicant_middle_name",
    "applicant_last_name",
    "applicant_business_name",
    "applicant_business_address",
    "filing_representative_first_name",
    "filing_representative_middle_initial",
    "filing_representative_last_name",
    "filing_representative_business_name",
    "owner_name",
    "owner_s_first_name",
    "owner_s_last_name",
    "owner_street_address",
    "owner_s_house_street_name",
    "permittee_s_first_name",
    "permittee_s_last_name",
    "permittee_s_phone__",
    "respondent_name",
    "respondent_house_number",
    "respondent_street",
    "respondent_city",
    "respondent_zip",
    "site_safety_mgr_s_first_name",
    "site_safety_mgr_s_last_name",
    "superintendent_first___last_name",
}


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    table_name: str
    entity_type: str
    id_column: str
    select_fields: tuple[str, ...]
    where: str | None
    output_filename: str
    builder: Callable[[pd.DataFrame, Snapshot], pd.DataFrame]
    warnings: tuple[str, ...] = ()


def _column(frame: pd.DataFrame, name: str, default: Any = None) -> pd.Series:
    if name in frame.columns:
        return frame[name]
    return pd.Series(default, index=frame.index, dtype="object")


def _identifier(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    digits = "".join(character for character in text if character.isdigit())
    return digits or None


def _normalize_bbl(
    borough: Any,
    block: Any,
    lot: Any,
    published_bbl: Any = None,
) -> str | None:
    candidate = _identifier(published_bbl)
    if candidate and len(candidate) == 10 and candidate[0] in "12345":
        return candidate
    borough_code = BOROUGH_CODES.get(str(borough).strip().upper()) if borough is not None else None
    block_id = _identifier(block)
    lot_id = _identifier(lot)
    if not borough_code or not block_id or not lot_id:
        return None
    block_number = int(block_id)
    lot_number = int(lot_id)
    if block_number <= 0 or lot_number <= 0 or block_number > 99_999 or lot_number > 9_999:
        return None
    return f"{borough_code}{block_number:05d}{lot_number:04d}"


def _bbl_series(
    frame: pd.DataFrame,
    *,
    borough: str,
    block: str,
    lot: str,
    published_bbl: str | None = None,
) -> pd.Series:
    published = _column(frame, published_bbl) if published_bbl else pd.Series(None, index=frame.index)
    return pd.Series(
        (
            _normalize_bbl(borough_value, block_value, lot_value, published_value)
            for borough_value, block_value, lot_value, published_value in zip(
                _column(frame, borough), _column(frame, block), _column(frame, lot), published
            )
        ),
        index=frame.index,
        dtype="string",
    )


def _mixed_dates(series: pd.Series) -> pd.Series:
    values = series.astype("string").str.strip()
    parsed = pd.to_datetime(values, format="mixed", errors="coerce")
    compact = values.str.fullmatch(r"\d{8}", na=False)
    if compact.any():
        parsed.loc[compact] = pd.to_datetime(values.loc[compact], format="%Y%m%d", errors="coerce")
    return parsed.dt.date


def _number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype("string").str.replace(",", "", regex=False), errors="coerce")


def _json_records(frame: pd.DataFrame, fields: tuple[str, ...]) -> pd.Series:
    selected = frame.reindex(columns=fields).astype(object).where(pd.notna(frame.reindex(columns=fields)), None)
    return pd.Series((json_dumps(record) for record in selected.to_dict("records")), index=frame.index)


def _base_regulatory(
    frame: pd.DataFrame,
    snapshot: Snapshot,
    *,
    record_type: str,
    status_column: str,
    issued_column: str,
    bbl: pd.Series,
    attribute_fields: tuple[str, ...],
) -> pd.DataFrame:
    source_id = snapshot.source_id
    source_ids = _column(frame, "source_row_id").astype("string")
    issued = _mixed_dates(_column(frame, issued_column))
    future_dated = issued.map(
        lambda value: pd.notna(value) and value > snapshot.retrieved_at.date()
    )
    issued = issued.mask(future_dated)
    return pd.DataFrame(
        {
            "regulatory_record_id": source_ids.map(lambda value: stable_id("reg", source_id, value)),
            "parcel_id": bbl.map(lambda value: f"parcel_{value}" if pd.notna(value) else None),
            "record_type": record_type,
            "status": _column(frame, status_column).astype("string").str.strip(),
            "issued_at": issued,
            "closed_at": None,
            "attributes_json": _json_records(frame, attribute_fields),
            "source_id": source_id,
            "snapshot_id": snapshot.snapshot_id,
            "retrieved_at": snapshot.retrieved_at,
            "valid_from": issued,
            "valid_to": None,
            "geography_type": bbl.notna().map({True: "tax_lot", False: "unresolved_property"}),
            "geography_id": bbl,
            "measure_type": "administrative_record",
            "confidence": bbl.notna().map({True: 0.95, False: 0.65}),
            "method_version": "0.2.0",
        }
    )


def _build_sales(frame: pd.DataFrame, snapshot: Snapshot) -> pd.DataFrame:
    bbl = _bbl_series(frame, borough="borough", block="block", lot="lot")
    dates = _mixed_dates(_column(frame, "sale_date"))
    source_ids = _column(frame, "source_row_id").astype("string")
    attributes = (
        "source_row_id",
        "borough",
        "neighborhood",
        "building_class_category",
        "tax_class_at_present",
        "block",
        "lot",
        "building_class_at_present",
        "zip_code",
        "residential_units",
        "commercial_units",
        "total_units",
        "land_square_feet",
        "gross_square_feet",
        "year_built",
        "tax_class_at_time_of_sale",
        "building_class_at_time_of",
        "sale_price",
        "sale_date",
    )
    return pd.DataFrame(
        {
            "property_record_id": source_ids.map(lambda value: stable_id("property", snapshot.source_id, value)),
            "parcel_id": bbl.map(lambda value: f"parcel_{value}" if pd.notna(value) else None),
            "record_type": "rolling_calendar_sale",
            "record_date": dates,
            "amount": _number(_column(frame, "sale_price")),
            "attributes_json": _json_records(frame, attributes),
            "source_id": snapshot.source_id,
            "snapshot_id": snapshot.snapshot_id,
            "retrieved_at": snapshot.retrieved_at,
            "valid_from": dates,
            "valid_to": None,
            "geography_type": bbl.notna().map({True: "tax_lot", False: "unresolved_property"}),
            "geography_id": bbl,
            "measure_type": "administrative_transaction_record",
            "confidence": bbl.notna().map({True: 0.95, False: 0.65}),
            "method_version": "0.2.0",
        }
    )


def _build_bis_permits(frame: pd.DataFrame, snapshot: Snapshot) -> pd.DataFrame:
    bbl = _bbl_series(frame, borough="borough", block="block", lot="lot", published_bbl="bbl")
    return _base_regulatory(
        frame,
        snapshot,
        record_type="building_permit_bis",
        status_column="permit_status",
        issued_column="issuance_date",
        bbl=bbl,
        attribute_fields=tuple(field for field in BIS_PERMIT_FIELDS if field != ":id as source_row_id"),
    )


def _build_now_permits(frame: pd.DataFrame, snapshot: Snapshot) -> pd.DataFrame:
    bbl = _bbl_series(frame, borough="borough", block="block", lot="lot", published_bbl="bbl")
    return _base_regulatory(
        frame,
        snapshot,
        record_type="building_permit_dob_now",
        status_column="permit_status",
        issued_column="issued_date",
        bbl=bbl,
        attribute_fields=tuple(field for field in NOW_PERMIT_FIELDS if field != ":id as source_row_id"),
    )


def _build_legacy_violations(frame: pd.DataFrame, snapshot: Snapshot) -> pd.DataFrame:
    bbl = _bbl_series(frame, borough="boro", block="block", lot="lot")
    return _base_regulatory(
        frame,
        snapshot,
        record_type="dob_violation_active_legacy",
        status_column="violation_category",
        issued_column="issue_date",
        bbl=bbl,
        attribute_fields=tuple(field for field in LEGACY_VIOLATION_FIELDS if field != ":id as source_row_id"),
    )


def _build_safety_violations(frame: pd.DataFrame, snapshot: Snapshot) -> pd.DataFrame:
    bbl = _bbl_series(frame, borough="borough", block="block", lot="lot", published_bbl="bbl")
    return _base_regulatory(
        frame,
        snapshot,
        record_type="dob_safety_violation_active",
        status_column="violation_status",
        issued_column="violation_issue_date",
        bbl=bbl,
        attribute_fields=tuple(field for field in SAFETY_VIOLATION_FIELDS if field != ":id as source_row_id"),
    )


def _build_ecb_violations(frame: pd.DataFrame, snapshot: Snapshot) -> pd.DataFrame:
    bbl = _bbl_series(frame, borough="boro", block="block", lot="lot")
    return _base_regulatory(
        frame,
        snapshot,
        record_type="dob_ecb_violation_active",
        status_column="ecb_violation_status",
        issued_column="issue_date",
        bbl=bbl,
        attribute_fields=tuple(field for field in ECB_VIOLATION_FIELDS if field != ":id as source_row_id"),
    )


def _build_legacy_co(frame: pd.DataFrame, snapshot: Snapshot) -> pd.DataFrame:
    bbl = _bbl_series(frame, borough="borough", block="block", lot="lot", published_bbl="bbl")
    return _base_regulatory(
        frame,
        snapshot,
        record_type="certificate_of_occupancy_bis",
        status_column="application_status_raw",
        issued_column="c_o_issue_date",
        bbl=bbl,
        attribute_fields=tuple(field for field in LEGACY_CO_FIELDS if field != ":id as source_row_id"),
    )


def _build_now_co(frame: pd.DataFrame, snapshot: Snapshot) -> pd.DataFrame:
    bbl = _bbl_series(frame, borough="borough", block="block", lot="lot", published_bbl="bbl")
    return _base_regulatory(
        frame,
        snapshot,
        record_type="certificate_of_occupancy_dob_now",
        status_column="c_of_o_status",
        issued_column="c_of_o_issuance_date",
        bbl=bbl,
        attribute_fields=tuple(field for field in NOW_CO_FIELDS if field != ":id as source_row_id"),
    )


SALES_FIELDS = (
    ":id as source_row_id",
    "borough",
    "neighborhood",
    "building_class_category",
    "tax_class_at_present",
    "block",
    "lot",
    "building_class_at_present",
    "zip_code",
    "residential_units",
    "commercial_units",
    "total_units",
    "land_square_feet",
    "gross_square_feet",
    "year_built",
    "tax_class_at_time_of_sale",
    "building_class_at_time_of",
    "sale_price",
    "sale_date",
)

BIS_PERMIT_FIELDS = (
    ":id as source_row_id",
    "borough",
    "bin__",
    "house__",
    "street_name",
    "job__",
    "job_doc___",
    "job_type",
    "block",
    "lot",
    "community_board",
    "zip_code",
    "bldg_type",
    "residential",
    "work_type",
    "permit_status",
    "filing_status",
    "permit_type",
    "permit_sequence__",
    "permit_subtype",
    "filing_date",
    "issuance_date",
    "expiration_date",
    "job_start_date",
    "gis_latitude",
    "gis_longitude",
    "gis_census_tract",
    "bbl",
    "gis_nta_name",
    "dobrundate",
)

NOW_PERMIT_FIELDS = (
    ":id as source_row_id",
    "job_filing_number",
    "work_permit",
    "sequence_number",
    "filing_reason",
    "house_no",
    "street_name",
    "borough",
    "lot",
    "bin",
    "block",
    "c_b_no",
    "work_type",
    "approved_date",
    "issued_date",
    "expired_date",
    "estimated_job_costs",
    "permit_status",
    "tracking_number",
    "zip_code",
    "latitude",
    "longitude",
    "community_board",
    "council_district",
    "bbl",
    "census_tract",
    "nta",
)

LEGACY_VIOLATION_FIELDS = (
    ":id as source_row_id",
    "isn_dob_bis_viol",
    "boro",
    "bin",
    "block",
    "lot",
    "issue_date",
    "violation_type_code",
    "violation_number",
    "description",
    "ecb_number",
    "violation_category",
    "violation_type",
)

SAFETY_VIOLATION_FIELDS = (
    ":id as source_row_id",
    "bin",
    "violation_issue_date",
    "violation_number",
    "violation_type",
    "violation_status",
    "device_number",
    "device_type",
    "cycle_end_date",
    "borough",
    "block",
    "lot",
    "latitude",
    "longitude",
    "community_board",
    "council_district",
    "bbl",
    "census_tract_2020_",
    "neighborhood_tabulation_area_nta_2020_",
)

ECB_VIOLATION_FIELDS = (
    ":id as source_row_id",
    "isn_dob_bis_extract",
    "ecb_violation_number",
    "ecb_violation_status",
    "dob_violation_number",
    "bin",
    "boro",
    "block",
    "lot",
    "hearing_date",
    "served_date",
    "issue_date",
    "severity",
    "violation_type",
    "violation_description",
    "penality_imposed",
    "amount_paid",
    "balance_due",
    "aggravated_level",
    "hearing_status",
    "certification_status",
)

LEGACY_CO_FIELDS = (
    ":id as source_row_id",
    "job_number",
    "job_type",
    "c_o_issue_date",
    "bin_number",
    "borough",
    "block",
    "lot",
    "pr_dwelling_unit",
    "ex_dwelling_unit",
    "application_status_raw",
    "filing_status_raw",
    "item_number",
    "issue_type",
    "latitude",
    "longitude",
    "community_board",
    "census_tract",
    "bin",
    "bbl",
    "nta",
)

NOW_CO_FIELDS = (
    ":id as source_row_id",
    "job_filing_name",
    "job_type",
    "bin",
    "borough",
    "block",
    "lot",
    "submitted_date",
    "c_of_o_status",
    "c_of_o_sequence",
    "c_of_o_filing_type",
    "community_board",
    "c_of_o_issuance_date",
    "application_number",
    "number_of_dwelling_units",
    "c_of_o_number",
    "latitude",
    "longitude",
    "bbl",
    "censustract2010",
    "ntaname",
)


SOURCE_SPECS = {
    "nyc_rolling_sales": SourceSpec(
        "nyc_rolling_sales",
        "property_record",
        "property_record",
        "property_record_id",
        SALES_FIELDS,
        None,
        "rolling_sales.parquet",
        _build_sales,
        ("Zero-dollar and non-market transfers remain in the record layer but are excluded from median-price metrics.",),
    ),
    "dob_permit_issuance_recent": SourceSpec(
        "dob_permit_issuance_recent",
        "regulatory_record",
        "regulatory_record",
        "regulatory_record_id",
        BIS_PERMIT_FIELDS,
        " OR ".join(f"issuance_date like '%/{year}'" for year in range(2021, date.today().year + 1)),
        "bis_permits_2021_onward.parquet",
        _build_bis_permits,
        ("Legacy BIS permits only; combine with DOB NOW Build permits for current coverage.",),
    ),
    "dob_now_approved_permits_recent": SourceSpec(
        "dob_now_approved_permits_recent",
        "regulatory_record",
        "regulatory_record",
        "regulatory_record_id",
        NOW_PERMIT_FIELDS,
        "issued_date >= '2021-01-01T00:00:00.000'",
        "dob_now_permits_2021_onward.parquet",
        _build_now_permits,
        ("Electrical, elevator and Limited Alteration Application permit systems are outside this source.",),
    ),
    "dob_violations_active": SourceSpec(
        "dob_violations_active",
        "regulatory_record",
        "regulatory_record",
        "regulatory_record_id",
        LEGACY_VIOLATION_FIELDS,
        "disposition_date is null",
        "active_legacy_dob_violations.parquet",
        _build_legacy_violations,
        ("No disposition date is treated only as the source's active-state proxy.",),
    ),
    "dob_safety_violations_active": SourceSpec(
        "dob_safety_violations_active",
        "regulatory_record",
        "regulatory_record",
        "regulatory_record_id",
        SAFETY_VIOLATION_FIELDS,
        "upper(violation_status)='ACTIVE'",
        "active_dob_safety_violations.parquet",
        _build_safety_violations,
        ("Some newer safety violations may overlap records in the legacy DOB Violations source.",),
    ),
    "dob_ecb_violations_active": SourceSpec(
        "dob_ecb_violations_active",
        "regulatory_record",
        "regulatory_record",
        "regulatory_record_id",
        ECB_VIOLATION_FIELDS,
        "upper(ecb_violation_status)='ACTIVE'",
        "active_dob_ecb_violations.parquet",
        _build_ecb_violations,
        ("An active summons record is an administrative status, not an NYC Lens legal finding.",),
    ),
    "dob_certificate_occupancy_legacy": SourceSpec(
        "dob_certificate_occupancy_legacy",
        "regulatory_record",
        "regulatory_record",
        "regulatory_record_id",
        LEGACY_CO_FIELDS,
        "c_o_issue_date is not null",
        "bis_certificates_of_occupancy.parquet",
        _build_legacy_co,
        ("Publisher documentation directs post-March-2021 use to DOB NOW; later legacy BIS-job issuances remain in this extract and both systems must be combined.",),
    ),
    "dob_now_certificate_occupancy": SourceSpec(
        "dob_now_certificate_occupancy",
        "regulatory_record",
        "regulatory_record",
        "regulatory_record_id",
        NOW_CO_FIELDS,
        "c_of_o_issuance_date is not null",
        "dob_now_certificates_of_occupancy.parquet",
        _build_now_co,
    ),
}


def selected_api_fields(spec: SourceSpec) -> set[str]:
    return {
        field.split(" as ", 1)[0].strip().lower()
        for field in spec.select_fields
        if not field.startswith(":")
    }


def _assert_privacy_contract(spec: SourceSpec) -> None:
    prohibited = selected_api_fields(spec) & PROHIBITED_FIELD_TOKENS
    if prohibited:
        raise ValueError(f"Prohibited personal fields selected for {spec.source_id}: {sorted(prohibited)}")


def _snapshot_row_count(snapshot: Snapshot) -> int:
    frame = pd.read_csv(snapshot.path, dtype=str, keep_default_na=True)
    return int(frame.iloc[0, 0])


def _replace_table_from_csv(
    conn: duckdb.DuckDBPyConnection,
    spec: SourceSpec,
    snapshot: Snapshot,
    expected_rows: int,
) -> tuple[int, int, int, date | None, date | None]:
    raw_rows = 0
    standardized_rows = 0
    schema: dict[str, str] = {}
    minimum_date: date | None = None
    maximum_date: date | None = None
    incoming_relation = "_property_incoming"
    conn.execute(f"DROP TABLE IF EXISTS {incoming_relation}")
    conn.execute(
        f"CREATE TEMP TABLE {incoming_relation} AS "
        f"SELECT * FROM {spec.table_name} WHERE FALSE"
    )
    try:
        for chunk in pd.read_csv(
            snapshot.path,
            dtype=str,
            keep_default_na=True,
            low_memory=False,
            chunksize=CHUNK_SIZE,
        ):
            raw_rows += len(chunk)
            if not schema:
                schema = {column: str(dtype) for column, dtype in chunk.dtypes.items()}
            standardized = spec.builder(chunk, snapshot)
            if standardized[spec.id_column].isna().any():
                raise ValueError(f"Missing standardized ids in {spec.source_id}")
            if standardized[spec.id_column].duplicated().any():
                raise ValueError(f"Duplicate standardized ids inside a source chunk for {spec.source_id}")
            date_column = "record_date" if spec.table_name == "property_record" else "issued_at"
            dated = standardized[date_column].dropna()
            if not dated.empty:
                chunk_min = min(dated)
                chunk_max = max(dated)
                minimum_date = chunk_min if minimum_date is None else min(minimum_date, chunk_min)
                maximum_date = chunk_max if maximum_date is None else max(maximum_date, chunk_max)
            conn.register("_property_chunk", standardized)
            conn.execute(f"INSERT INTO {incoming_relation} BY NAME SELECT * FROM _property_chunk")
            conn.unregister("_property_chunk")
            standardized_rows += len(standardized)
        if raw_rows != expected_rows:
            raise ValueError(
                f"Socrata row-count mismatch for {spec.source_id}: expected {expected_rows}, downloaded {raw_rows}"
            )
        deleted, published = replace_source_from_relation(
            conn,
            table_name=spec.table_name,
            source_id=spec.source_id,
            relation=incoming_relation,
        )
        if published != standardized_rows:
            raise ValueError(
                f"Published row mismatch for {spec.source_id}: {published}/{standardized_rows}"
            )
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                """
                UPDATE dataset_snapshot
                SET row_count=?, schema_json=?::JSON, valid_from=?, valid_to=?, warnings_json=?::JSON
                WHERE snapshot_id=?
                """,
                [raw_rows, json_dumps(schema), minimum_date, maximum_date, json_dumps(spec.warnings), snapshot.snapshot_id],
            )
            register_table_lineage(
                conn,
                table_name=spec.table_name,
                entity_type=spec.entity_type,
                id_column=spec.id_column,
                source_id=spec.source_id,
                snapshot_id=snapshot.snapshot_id,
            )
            output = standardized_path(spec.source_id, "current", spec.output_filename)
            escaped_output = str(output).replace("'", "''")
            conn.execute(
                f"COPY (SELECT * FROM {spec.table_name} WHERE source_id=?) TO '{escaped_output}' "
                "(FORMAT PARQUET, COMPRESSION ZSTD)",
                [spec.source_id],
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return raw_rows, standardized_rows, deleted, minimum_date, maximum_date
    except Exception:
        try:
            conn.unregister("_property_chunk")
        except Exception:
            pass
        raise
    finally:
        conn.execute(f"DROP TABLE IF EXISTS {incoming_relation}")


def refresh_property_source(conn: duckdb.DuckDBPyConnection, source_id: str) -> dict[str, Any]:
    if source_id not in SOURCE_SPECS:
        raise ValueError(f"Unsupported property/regulatory source: {source_id}")
    spec = SOURCE_SPECS[source_id]
    _assert_privacy_contract(spec)
    run_id = begin_run(conn, source_id, "refresh selected non-personal property/regulatory facts")
    try:
        metadata = fetch_metadata_snapshot(conn, source_id, "current", run_id)
        count_snapshot = download_socrata_csv(
            conn,
            source_id,
            "current",
            run_id,
            select="count(*) as expected_rows",
            where=spec.where,
            limit=1,
        )
        expected_rows = _snapshot_row_count(count_snapshot)
        if expected_rows > MAX_SOCRATA_ROWS:
            raise ValueError(
                f"Filtered source has {expected_rows:,} rows, above the single-query safety limit of {MAX_SOCRATA_ROWS:,}"
            )
        data_snapshot = download_socrata_csv(
            conn,
            source_id,
            "current",
            run_id,
            select=",".join(spec.select_fields),
            where=spec.where,
            order=":id",
            limit=max(1, expected_rows + 1),
        )
        raw_rows, inserted, deleted, valid_from, valid_to = _replace_table_from_csv(
            conn, spec, data_snapshot, expected_rows
        )
        warnings = list(spec.warnings)
        unresolved = int(
            conn.execute(
                f"SELECT count(*) FROM {spec.table_name} WHERE source_id=? AND geography_id IS NULL",
                [source_id],
            ).fetchone()[0]
        )
        if unresolved:
            warnings.append(f"Records without a valid 10-digit BBL: {unresolved}.")
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS" if warnings else "SUCCEEDED",
            request_count=3,
            downloaded_bytes=metadata.byte_size + count_snapshot.byte_size + data_snapshot.byte_size,
            inserted_records=inserted,
            deleted_records=deleted,
            warnings=warnings,
        )
        return {
            "run_id": run_id,
            "snapshot_id": data_snapshot.snapshot_id,
            "source_rows": raw_rows,
            "standardized_rows": inserted,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "unresolved_bbl": unresolved,
            "raw_bytes": metadata.byte_size + count_snapshot.byte_size + data_snapshot.byte_size,
            "selected_fields": sorted(selected_api_fields(spec)),
            "excluded_personal_fields": True,
        }
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise


def import_property_source_files(
    conn: duckdb.DuckDBPyConnection,
    source_id: str,
    *,
    metadata_path: Path,
    count_path: Path,
    data_path: Path,
) -> dict[str, Any]:
    """Import curl-downloaded recovery files through the normal append-only Raw and standard layers."""
    if source_id not in SOURCE_SPECS:
        raise ValueError(f"Unsupported property/regulatory source: {source_id}")
    spec = SOURCE_SPECS[source_id]
    _assert_privacy_contract(spec)
    for path in (metadata_path, count_path, data_path):
        if not path.is_file() or path.stat().st_size <= 0:
            raise FileNotFoundError(f"Missing or empty recovery file: {path}")
    source = get_source(source_id)
    run_id = begin_run(conn, source_id, "import verified WSL-curl recovery snapshot")
    try:
        metadata = save_file_snapshot(
            conn,
            source_id=source_id,
            vintage="current",
            filename=f"{source['dataset_id']}_metadata.json",
            source_path=metadata_path,
            source_url=f"https://data.cityofnewyork.us/api/views/{source['dataset_id']}",
            run_id=run_id,
            content_type="application/json",
            query={"kind": "Socrata dataset metadata", "transport": "WSL curl recovery"},
            http_status=200,
        )
        count_snapshot = save_file_snapshot(
            conn,
            source_id=source_id,
            vintage="current",
            filename=f"{source['dataset_id']}_count.csv",
            source_path=count_path,
            source_url=source["api_url"],
            run_id=run_id,
            content_type="text/csv",
            query={"$select": "count(*) as expected_rows", "$where": spec.where, "$limit": 1, "transport": "WSL curl recovery"},
            http_status=200,
        )
        expected_rows = _snapshot_row_count(count_snapshot)
        if expected_rows > MAX_SOCRATA_ROWS:
            raise ValueError(
                f"Filtered source has {expected_rows:,} rows, above the single-query safety limit of {MAX_SOCRATA_ROWS:,}"
            )
        data_snapshot = save_file_snapshot(
            conn,
            source_id=source_id,
            vintage="current",
            filename=f"{source['dataset_id']}.csv",
            source_path=data_path,
            source_url=source["api_url"],
            run_id=run_id,
            content_type="text/csv",
            query={
                "$select": ",".join(spec.select_fields),
                "$where": spec.where,
                "$order": ":id",
                "$limit": max(1, expected_rows + 1),
                "transport": "WSL curl recovery",
            },
            http_status=200,
        )
        raw_rows, inserted, deleted, valid_from, valid_to = _replace_table_from_csv(
            conn, spec, data_snapshot, expected_rows
        )
        unresolved = int(
            conn.execute(
                f"SELECT count(*) FROM {spec.table_name} WHERE source_id=? AND geography_id IS NULL",
                [source_id],
            ).fetchone()[0]
        )
        warnings = [*spec.warnings, "Transport recovery used WSL curl after Windows ephemeral-port exhaustion."]
        if unresolved:
            warnings.append(f"Records without a valid 10-digit BBL: {unresolved}.")
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=3,
            downloaded_bytes=metadata.byte_size + count_snapshot.byte_size + data_snapshot.byte_size,
            inserted_records=inserted,
            deleted_records=deleted,
            warnings=warnings,
        )
        return {
            "run_id": run_id,
            "snapshot_id": data_snapshot.snapshot_id,
            "source_rows": raw_rows,
            "standardized_rows": inserted,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "unresolved_bbl": unresolved,
            "raw_bytes": metadata.byte_size + count_snapshot.byte_size + data_snapshot.byte_size,
            "selected_fields": sorted(selected_api_fields(spec)),
            "excluded_personal_fields": True,
            "transport": "WSL curl recovery",
        }
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise


def rebuild_property_source(
    conn: duckdb.DuckDBPyConnection,
    source_id: str,
) -> dict[str, Any]:
    """Rebuild a standard table from the latest successful record-data snapshot without network access."""
    if source_id not in SOURCE_SPECS:
        raise ValueError(f"Unsupported property/regulatory source: {source_id}")
    spec = SOURCE_SPECS[source_id]
    row = conn.execute(
        """
        SELECT snapshot_id, source_id, raw_path, metadata_path, sha256,
               byte_size, retrieved_at, vintage, row_count
        FROM dataset_snapshot
        WHERE source_id=? AND status='SUCCEEDED'
          AND json_extract_string(query_json, '$.\"$select\"') IS NOT NULL
          AND json_extract_string(query_json, '$.\"$select\"') NOT ILIKE 'count(%'
        ORDER BY retrieved_at DESC LIMIT 1
        """,
        [source_id],
    ).fetchone()
    if not row:
        raise ValueError(f"No reusable record-data snapshot for {source_id}")
    snapshot = Snapshot(
        snapshot_id=row[0],
        source_id=row[1],
        path=Path(row[2]),
        metadata_path=Path(row[3]),
        sha256=row[4],
        byte_size=row[5],
        retrieved_at=row[6],
        vintage=row[7],
    )
    expected_rows = int(row[8]) if row[8] is not None else sum(
        len(chunk) for chunk in pd.read_csv(snapshot.path, dtype=str, chunksize=CHUNK_SIZE)
    )
    run_id = begin_run(conn, source_id, "rebuild standardized property/regulatory facts from existing Raw snapshot")
    try:
        raw_rows, inserted, deleted, valid_from, valid_to = _replace_table_from_csv(
            conn, spec, snapshot, expected_rows
        )
        unresolved = int(
            conn.execute(
                f"SELECT count(*) FROM {spec.table_name} WHERE source_id=? AND geography_id IS NULL",
                [source_id],
            ).fetchone()[0]
        )
        warnings = [*spec.warnings, "Standard layer rebuilt from an existing hash-registered Raw snapshot; no network request was made."]
        if unresolved:
            warnings.append(f"Records without a valid 10-digit BBL: {unresolved}.")
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=0,
            downloaded_bytes=0,
            inserted_records=inserted,
            deleted_records=deleted,
            warnings=warnings,
        )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "source_rows": raw_rows,
            "standardized_rows": inserted,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "unresolved_bbl": unresolved,
            "reused_raw_snapshot": True,
        }
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise


def refresh_rolling_sales(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    return refresh_property_source(conn, "nyc_rolling_sales")


def refresh_bis_permits(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    return refresh_property_source(conn, "dob_permit_issuance_recent")


def refresh_dob_now_permits(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    return refresh_property_source(conn, "dob_now_approved_permits_recent")


def refresh_active_dob_violations(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    return refresh_property_source(conn, "dob_violations_active")


def refresh_active_safety_violations(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    return refresh_property_source(conn, "dob_safety_violations_active")


def refresh_active_ecb_violations(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    return refresh_property_source(conn, "dob_ecb_violations_active")


def refresh_legacy_certificates_of_occupancy(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    return refresh_property_source(conn, "dob_certificate_occupancy_legacy")


def refresh_dob_now_certificates_of_occupancy(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    return refresh_property_source(conn, "dob_now_certificate_occupancy")
