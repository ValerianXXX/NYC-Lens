from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from ..config import get_source
from ..db import begin_run, finish_run
from ..provenance import Snapshot, download_snapshot
from ..util import json_dumps
from ..storage import replace_source_from_relation
from .common import register_table_lineage, standardized_path
from .socrata import download_socrata_csv, fetch_metadata_snapshot


MASTER_SOURCE_ID = "acris_real_property_master_recent"
LEGALS_SOURCE_ID = "acris_real_property_legals_recent"
START_DATE = date(2021, 1, 1)
MASTER_TYPES = ("DEED", "RPTT&RET", "MTGE", "SAT", "ASST")
MASTER_SELECT = (
    "document_id,record_type,crfn,recorded_borough,doc_type,document_date,"
    "document_amt,recorded_datetime,modified_date,percent_trans,good_through_date"
)
LEGALS_SELECT = (
    "document_id,record_type,borough,block,lot,easement,partial_lot,"
    "air_rights,subterranean_rights,property_type,good_through_date"
)
MAX_EXPECTED_RAW_BYTES = 5_000_000_000


def _master_where() -> str:
    types = ",".join(f"'{value}'" for value in MASTER_TYPES)
    return f"recorded_datetime >= '{START_DATE.isoformat()}T00:00:00.000' AND doc_type IN ({types})"


def _legals_where() -> str:
    return "document_id >= '2021010100000000'"


def _row_count(snapshot: Snapshot) -> int:
    frame = pd.read_csv(snapshot.path, dtype=str)
    if frame.empty:
        return 0
    return int(frame.iloc[0, 0])


def _download_large_csv(
    conn: duckdb.DuckDBPyConnection,
    *,
    source_id: str,
    run_id: str,
    select: str,
    where: str,
    expected_rows: int,
) -> Snapshot:
    source = get_source(source_id)
    return download_snapshot(
        conn,
        source_id=source_id,
        vintage="2021-present",
        url=source["api_url"],
        filename=f"{source['dataset_id']}.csv",
        run_id=run_id,
        params={
            "$select": select,
            "$where": where,
            "$order": "document_id",
            "$limit": expected_rows + 1,
        },
        valid_from=START_DATE,
        timeout_seconds=3600,
    )


def _data_snapshot(conn: duckdb.DuckDBPyConnection, source_id: str) -> Snapshot:
    row = conn.execute(
        """
        SELECT snapshot_id, source_id, raw_path, metadata_path, sha256,
               byte_size, retrieved_at, vintage
        FROM dataset_snapshot
        WHERE source_id=? AND status='SUCCEEDED'
          AND json_extract_string(query_json, '$.\"$select\"') IS NOT NULL
          AND json_extract_string(query_json, '$.\"$select\"') NOT ILIKE 'count(%'
        ORDER BY retrieved_at DESC LIMIT 1
        """,
        [source_id],
    ).fetchone()
    if not row:
        raise ValueError(f"No reusable ACRIS record-data snapshot for {source_id}")
    return Snapshot(
        snapshot_id=row[0],
        source_id=row[1],
        path=Path(row[2]),
        metadata_path=Path(row[3]),
        sha256=row[4],
        byte_size=row[5],
        retrieved_at=row[6],
        vintage=row[7],
    )


def _replace_acris_property_records(
    conn: duckdb.DuckDBPyConnection,
    master: Snapshot,
    legals: Snapshot,
    *,
    expected_master_rows: int | None = None,
    expected_legal_rows: int | None = None,
) -> dict[str, Any]:
    master_path = str(master.path).replace("'", "''")
    legal_path = str(legals.path).replace("'", "''")
    raw_master_rows = int(
        conn.execute(f"SELECT count(*) FROM read_csv_auto('{master_path}', all_varchar=true)").fetchone()[0]
    )
    raw_legal_rows = int(
        conn.execute(f"SELECT count(*) FROM read_csv_auto('{legal_path}', all_varchar=true)").fetchone()[0]
    )
    if expected_master_rows is not None and raw_master_rows != expected_master_rows:
        raise ValueError(
            f"ACRIS Master row-count mismatch: expected {expected_master_rows:,}, downloaded {raw_master_rows:,}"
        )
    if expected_legal_rows is not None and raw_legal_rows != expected_legal_rows:
        raise ValueError(
            f"ACRIS Legals row-count mismatch: expected {expected_legal_rows:,}, downloaded {raw_legal_rows:,}"
        )

    retrieved_at = master.retrieved_at.isoformat(sep=" ")
    source_id = MASTER_SOURCE_ID
    transaction_open = False
    try:
        conn.execute("DROP TABLE IF EXISTS _acris_property_records")
        conn.execute(
            f"""
            CREATE TEMP TABLE _acris_property_records AS
            WITH master AS (
                SELECT
                    document_id,
                    record_type,
                    crfn,
                    recorded_borough,
                    doc_type,
                    document_date,
                    document_amt,
                    recorded_datetime,
                    modified_date,
                    percent_trans,
                    good_through_date
                FROM read_csv_auto('{master_path}', all_varchar=true)
            ), legal_normalized AS (
                SELECT
                    document_id,
                    record_type,
                    borough,
                    block,
                    lot,
                    easement,
                    partial_lot,
                    air_rights,
                    subterranean_rights,
                    property_type,
                    good_through_date,
                    printf(
                        '%d%05d%04d',
                        try_cast(borough AS INTEGER),
                        try_cast(block AS INTEGER),
                        try_cast(lot AS INTEGER)
                    ) AS bbl
                FROM read_csv_auto('{legal_path}', all_varchar=true)
                WHERE try_cast(borough AS INTEGER) BETWEEN 1 AND 5
                  AND try_cast(block AS INTEGER) BETWEEN 1 AND 99999
                  AND try_cast(lot AS INTEGER) BETWEEN 1 AND 9999
            ), joined AS (
                SELECT
                    m.document_id,
                    l.bbl,
                    any_value(m.record_type) AS master_record_type,
                    any_value(m.crfn) AS crfn,
                    any_value(m.recorded_borough) AS recorded_borough,
                    any_value(m.doc_type) AS doc_type,
                    any_value(m.document_date) AS document_date_raw,
                    any_value(m.document_amt) AS document_amount_raw,
                    any_value(m.recorded_datetime) AS recorded_datetime_raw,
                    any_value(m.modified_date) AS modified_date_raw,
                    any_value(m.percent_trans) AS percent_transferred_raw,
                    any_value(m.good_through_date) AS master_good_through_date,
                    string_agg(DISTINCT nullif(trim(l.property_type), ''), '|') AS property_types,
                    bool_or(upper(coalesce(l.easement, 'N'))='Y') AS has_easement,
                    bool_or(upper(coalesce(l.partial_lot, 'N')) IN ('Y','E')) AS has_partial_lot,
                    bool_or(upper(coalesce(l.air_rights, 'N'))='Y') AS has_air_rights,
                    bool_or(upper(coalesce(l.subterranean_rights, 'N'))='Y') AS has_subterranean_rights,
                    max(l.good_through_date) AS legal_good_through_date
                FROM master m
                JOIN legal_normalized l USING(document_id)
                GROUP BY m.document_id, l.bbl
            ), typed AS (
                SELECT
                    *,
                    try_cast(recorded_datetime_raw AS TIMESTAMP)::DATE AS record_date,
                    try_cast(replace(document_amount_raw, ',', '') AS DOUBLE) AS amount
                FROM joined
            )
            SELECT
                'property_' || substr(sha256('{source_id}|' || document_id || '|' || bbl), 1, 24)
                    AS property_record_id,
                'parcel_' || bbl AS parcel_id,
                'acris_' || lower(replace(doc_type, '&', '_and_')) AS record_type,
                record_date,
                amount,
                json_object(
                    'document_id', document_id,
                    'master_record_type', master_record_type,
                    'crfn', crfn,
                    'recorded_borough', recorded_borough,
                    'doc_type', doc_type,
                    'document_date_raw', document_date_raw,
                    'document_amount_raw', document_amount_raw,
                    'recorded_datetime_raw', recorded_datetime_raw,
                    'modified_date_raw', modified_date_raw,
                    'percent_transferred_raw', percent_transferred_raw,
                    'property_types', property_types,
                    'has_easement', has_easement,
                    'has_partial_lot', has_partial_lot,
                    'has_air_rights', has_air_rights,
                    'has_subterranean_rights', has_subterranean_rights,
                    'master_good_through_date', master_good_through_date,
                    'legal_good_through_date', legal_good_through_date,
                    'legal_snapshot_id', '{legals.snapshot_id}'
                ) AS attributes_json,
                '{source_id}' AS source_id,
                '{master.snapshot_id}' AS snapshot_id,
                TIMESTAMP '{retrieved_at}' AS retrieved_at,
                record_date AS valid_from,
                NULL::DATE AS valid_to,
                'tax_lot' AS geography_type,
                bbl AS geography_id,
                'administrative_property_document' AS measure_type,
                0.95::DOUBLE AS confidence,
                '0.1.0' AS method_version
            FROM typed
            WHERE record_date BETWEEN DATE '{START_DATE.isoformat()}' AND DATE '{master.retrieved_at.date().isoformat()}'
            """
        )
        inserted = int(conn.execute("SELECT count(*) FROM _acris_property_records").fetchone()[0])
        duplicates = int(
            conn.execute(
                "SELECT count(*) - count(DISTINCT property_record_id) FROM _acris_property_records"
            ).fetchone()[0]
        )
        if inserted == 0 or duplicates:
            raise ValueError(f"ACRIS local join produced inserted={inserted:,}, duplicate_ids={duplicates:,}")
        deleted, published = replace_source_from_relation(
            conn,
            table_name="property_record",
            source_id=source_id,
            relation="_acris_property_records",
        )
        if published != inserted:
            raise ValueError(f"ACRIS published row mismatch: {published:,}/{inserted:,}")
        conn.execute("BEGIN TRANSACTION")
        transaction_open = True
        conn.execute("DELETE FROM standardized_record WHERE entity_type='property_record' AND source_id IN (?, ?)", [MASTER_SOURCE_ID, LEGALS_SOURCE_ID])
        register_table_lineage(
            conn,
            table_name="property_record",
            entity_type="property_record",
            id_column="property_record_id",
            source_id=source_id,
            snapshot_id=master.snapshot_id,
        )
        conn.execute(
            """
            INSERT INTO standardized_record
            WITH incoming AS (
                SELECT
                    'std_' || substr(sha256('property_record|' || property_record_id || '|' || ?), 1, 24)
                        AS standardized_record_id,
                    'property_record' AS entity_type,
                    property_record_id AS entity_id,
                    ? AS source_id,
                    ? AS snapshot_id,
                    property_record_id AS source_record_id,
                    method_version,
                    retrieved_at AS standardized_at
                FROM property_record
                WHERE source_id=? AND snapshot_id=?
            )
            SELECT incoming.*
            FROM incoming
            WHERE NOT EXISTS (
                SELECT 1 FROM standardized_record existing
                WHERE existing.standardized_record_id=incoming.standardized_record_id
            )
            """,
            [legals.snapshot_id, LEGALS_SOURCE_ID, legals.snapshot_id, source_id, master.snapshot_id],
        )
        output = standardized_path(source_id, "2021-present", "acris_property_documents.parquet")
        escaped_output = str(output).replace("'", "''")
        conn.execute(
            f"COPY (SELECT * FROM property_record WHERE source_id='{source_id}') "
            f"TO '{escaped_output}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        valid_from, valid_to = conn.execute(
            "SELECT min(record_date), max(record_date) FROM _acris_property_records"
        ).fetchone()
        joined_documents, linked_lots = conn.execute(
            "SELECT count(DISTINCT json_extract_string(attributes_json, '$.document_id')), count(DISTINCT geography_id) FROM _acris_property_records"
        ).fetchone()
        conn.execute(
            """
            UPDATE dataset_snapshot
            SET row_count=?, schema_json=?::JSON, valid_from=?, valid_to=?, warnings_json=?::JSON
            WHERE snapshot_id=?
            """,
            [
                raw_master_rows,
                json_dumps({field: "VARCHAR" for field in MASTER_SELECT.split(",")}),
                valid_from,
                valid_to,
                json_dumps([
                    "Only selected non-personal Master fields were requested.",
                    f"Standardized rows are a local join to Legals snapshot {legals.snapshot_id}.",
                ]),
                master.snapshot_id,
            ],
        )
        conn.execute(
            """
            UPDATE dataset_snapshot
            SET row_count=?, schema_json=?::JSON, valid_from=?, valid_to=?, warnings_json=?::JSON
            WHERE snapshot_id=?
            """,
            [
                raw_legal_rows,
                json_dumps({field: "VARCHAR" for field in LEGALS_SELECT.split(",")}),
                START_DATE,
                valid_to,
                json_dumps([
                    "Street number, street name, unit and all ACRIS party datasets were excluded.",
                    f"Only rows matching selected Master snapshot {master.snapshot_id} enter the standard property layer.",
                ]),
                legals.snapshot_id,
            ],
        )
        conn.execute("COMMIT")
        transaction_open = False
        conn.execute("DROP TABLE _acris_property_records")
        return {
            "raw_master_rows": raw_master_rows,
            "raw_legal_rows": raw_legal_rows,
            "standardized_property_records": inserted,
            "joined_documents": int(joined_documents),
            "linked_tax_lots": int(linked_lots),
            "valid_from": valid_from,
            "valid_to": valid_to,
            "deleted_records": deleted,
            "output": str(output),
        }
    except Exception:
        try:
            conn.execute("DROP TABLE IF EXISTS _acris_property_records")
        except Exception:
            pass
        if transaction_open:
            conn.execute("ROLLBACK")
        raise


def refresh_acris_transactions(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Refresh a bounded, privacy-minimized ACRIS Master-to-Legals property-document layer."""
    master_run = begin_run(conn, MASTER_SOURCE_ID, "refresh selected non-personal 2021-present ACRIS documents")
    legal_run = begin_run(conn, LEGALS_SOURCE_ID, "refresh selected non-personal 2021-present ACRIS lot links")
    master_bytes = 0
    legal_bytes = 0
    try:
        master_metadata = fetch_metadata_snapshot(conn, MASTER_SOURCE_ID, "current", master_run)
        master_bytes += master_metadata.byte_size
        legal_metadata = fetch_metadata_snapshot(conn, LEGALS_SOURCE_ID, "current", legal_run)
        legal_bytes += legal_metadata.byte_size
        master_count_snapshot = download_socrata_csv(
            conn,
            MASTER_SOURCE_ID,
            "2021-present",
            master_run,
            select="count(*) AS expected_rows",
            where=_master_where(),
            limit=1,
        )
        legal_count_snapshot = download_socrata_csv(
            conn,
            LEGALS_SOURCE_ID,
            "2021-present",
            legal_run,
            select="count(*) AS expected_rows",
            where=_legals_where(),
            limit=1,
        )
        master_bytes += master_count_snapshot.byte_size
        legal_bytes += legal_count_snapshot.byte_size
        master_rows = _row_count(master_count_snapshot)
        legal_rows = _row_count(legal_count_snapshot)
        # Conservative selected-column estimates are intentionally well above the measured 2026 samples.
        master_estimated_bytes = master_rows * 240
        legal_estimated_bytes = legal_rows * 180
        if master_estimated_bytes > MAX_EXPECTED_RAW_BYTES or legal_estimated_bytes > MAX_EXPECTED_RAW_BYTES:
            raise ValueError(
                "ACRIS selected-field extract is expected to exceed the 5 GB single-file pause threshold; user approval is required."
            )
        master = _download_large_csv(
            conn,
            source_id=MASTER_SOURCE_ID,
            run_id=master_run,
            select=MASTER_SELECT,
            where=_master_where(),
            expected_rows=master_rows,
        )
        master_bytes += master.byte_size
        legals = _download_large_csv(
            conn,
            source_id=LEGALS_SOURCE_ID,
            run_id=legal_run,
            select=LEGALS_SELECT,
            where=_legals_where(),
            expected_rows=legal_rows,
        )
        legal_bytes += legals.byte_size
        if master.byte_size > MAX_EXPECTED_RAW_BYTES or legals.byte_size > MAX_EXPECTED_RAW_BYTES:
            raise ValueError("An ACRIS Raw file crossed the 5 GB single-file threshold")
        result = _replace_acris_property_records(
            conn,
            master,
            legals,
            expected_master_rows=master_rows,
            expected_legal_rows=legal_rows,
        )
        warnings = [
            "Only DEED, RPTT&RET, MTGE, SAT and ASST Master rows from 2021 onward are retained.",
            "No ACRIS party dataset, party name, address, street or unit field was requested.",
            "Document records and amounts are administrative filing facts, not appraisals, title opinions or legal conclusions.",
        ]
        finish_run(
            conn,
            legal_run,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=3,
            downloaded_bytes=legal_bytes,
            inserted_records=result["standardized_property_records"],
            warnings=warnings,
        )
        finish_run(
            conn,
            master_run,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=3,
            downloaded_bytes=master_bytes,
            inserted_records=result["standardized_property_records"],
            deleted_records=result["deleted_records"],
            warnings=warnings,
        )
        return {
            "master_run_id": master_run,
            "legals_run_id": legal_run,
            "master_snapshot_id": master.snapshot_id,
            "legals_snapshot_id": legals.snapshot_id,
            "master_downloaded_bytes": master_bytes,
            "legals_downloaded_bytes": legal_bytes,
            "privacy_minimized": True,
            **result,
        }
    except Exception as exc:
        for run_id in (legal_run, master_run):
            current = conn.execute("SELECT status FROM ingestion_run WHERE run_id=?", [run_id]).fetchone()
            if current and current[0] == "INDETERMINATE":
                finish_run(
                    conn,
                    run_id,
                    "FAILED",
                    downloaded_bytes=legal_bytes if run_id == legal_run else master_bytes,
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
        raise


def rebuild_acris_transactions(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Rebuild the local ACRIS join from the latest registered Raw snapshots without network access."""
    master = _data_snapshot(conn, MASTER_SOURCE_ID)
    legals = _data_snapshot(conn, LEGALS_SOURCE_ID)
    master_run = begin_run(conn, MASTER_SOURCE_ID, "rebuild ACRIS property documents from existing Raw snapshots")
    legal_run = begin_run(conn, LEGALS_SOURCE_ID, "reuse ACRIS Legals Raw snapshot in local rebuild")
    try:
        result = _replace_acris_property_records(conn, master, legals)
        warnings = [
            "Standard layer rebuilt from existing hash-registered Raw snapshots; no network request was made.",
            "All ACRIS personal-party datasets and address/unit fields remain excluded.",
        ]
        finish_run(
            conn,
            legal_run,
            "SUCCEEDED_WITH_WARNINGS",
            inserted_records=result["standardized_property_records"],
            warnings=warnings,
        )
        finish_run(
            conn,
            master_run,
            "SUCCEEDED_WITH_WARNINGS",
            inserted_records=result["standardized_property_records"],
            deleted_records=result["deleted_records"],
            warnings=warnings,
        )
        return {
            "master_run_id": master_run,
            "legals_run_id": legal_run,
            "master_snapshot_id": master.snapshot_id,
            "legals_snapshot_id": legals.snapshot_id,
            "reused_raw_snapshots": True,
            **result,
        }
    except Exception as exc:
        for run_id in (legal_run, master_run):
            finish_run(
                conn,
                run_id,
                "FAILED",
                error={"type": type(exc).__name__, "message": str(exc)},
            )
        raise
