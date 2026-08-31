from __future__ import annotations

import json
from typing import Any

import duckdb

from ..config import get_source
from ..db import begin_run, finish_run
from ..provenance import save_bytes_snapshot
from ..util import json_dumps
from .common import register_table_lineage


CASE_FILTER_SQL = """
    record_type IN ('rolling_calendar_sale', 'acris_deed')
    AND record_date IS NOT NULL
    AND amount > 0
    AND geography_type='tax_lot'
    AND regexp_full_match(geography_id, '[0-9]{10}')
"""


def _upstream_manifest(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    snapshots = conn.execute(
        f"""
        SELECT DISTINCT p.source_id, p.snapshot_id, s.vintage, s.retrieved_at,
               s.sha256, s.source_url, s.license, s.row_count
        FROM property_record p
        JOIN dataset_snapshot s USING(snapshot_id)
        WHERE {CASE_FILTER_SQL}
        ORDER BY p.source_id, p.snapshot_id
        """
    ).fetchdf().to_dict("records")
    candidate_count = conn.execute(
        f"SELECT count(*) FROM property_record WHERE {CASE_FILTER_SQL}"
    ).fetchone()[0]
    return {
        "derivation": "privacy_minimized_real_property_transaction_cases_v1",
        "method_version": "0.2.0",
        "candidate_records": candidate_count,
        "deduplication_key": ["tax_lot_bbl", "transaction_date", "rounded_amount"],
        "source_preference": ["nyc_rolling_sales", "acris_real_property_master_recent"],
        "included_record_types": ["rolling_calendar_sale", "acris_deed"],
        "excluded_fields": [
            "names",
            "parties",
            "mailing_addresses",
            "street_or_unit_address",
            "phone",
            "email",
            "contact_identifiers",
        ],
        "scope_note": "Public real-property records only; not a business acquisition, current valuation, legal conclusion, or calibrated comparable-sale model.",
        "upstream_snapshots": snapshots,
    }


def refresh_public_transaction_cases(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "derived_public_transaction_cases"
    source = get_source(source_id)
    run_id = begin_run(conn, source_id, "rebuild privacy-minimized public real-property transaction cases")
    old_count = conn.execute(
        "SELECT count(*) FROM transaction_case WHERE source_id=?", [source_id]
    ).fetchone()[0]
    try:
        manifest = _upstream_manifest(conn)
        if not manifest["upstream_snapshots"]:
            raise ValueError("No eligible NYC rolling-sale or ACRIS deed records are available")
        manifest_bytes = json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            default=str,
            sort_keys=True,
        ).encode("utf-8")
        snapshot = save_bytes_snapshot(
            conn,
            source_id=source_id,
            vintage="0.2.0",
            filename="derivation_manifest.json",
            content=manifest_bytes,
            source_url=source["url"],
            run_id=run_id,
            content_type="application/json",
            query={
                "filter": "positive-amount tax-lot rolling sales and ACRIS deeds",
                "deduplication": manifest["deduplication_key"],
                "upstream_snapshot_ids": [
                    row["snapshot_id"] for row in manifest["upstream_snapshots"]
                ],
            },
            warnings=[manifest["scope_note"], "No personal names, parties, street addresses, unit identifiers, contact details, or individual profiles are copied into the case table."],
        )
        conn.execute("DELETE FROM transaction_case WHERE source_id=?", [source_id])
        conn.execute(
            f"""
            INSERT INTO transaction_case
            WITH candidates AS (
                SELECT
                    property_record_id,
                    source_id AS upstream_source_id,
                    snapshot_id AS upstream_snapshot_id,
                    record_type,
                    record_date,
                    amount,
                    geography_type,
                    geography_id,
                    attributes_json,
                    confidence,
                    CASE WHEN source_id='nyc_rolling_sales' THEN 1 ELSE 2 END AS source_priority
                FROM property_record
                WHERE {CASE_FILTER_SQL}
            ), ranked AS (
                SELECT *, row_number() OVER (
                    PARTITION BY geography_id, record_date, round(amount, 2)
                    ORDER BY source_priority, property_record_id
                ) AS duplicate_rank
                FROM candidates
            )
            SELECT
                'case_' || substr(sha256(
                    'public_property|' || upstream_source_id || '|' || property_record_id
                ), 1, 24) AS transaction_case_id,
                CASE
                    WHEN record_type='rolling_calendar_sale' THEN 'rolling_tax_lot_sale'
                    ELSE 'recorded_deed'
                END AS case_type,
                NULL AS business_type,
                record_date AS transaction_date,
                amount,
                geography_type,
                geography_id,
                json_object(
                    'upstream_property_record_id', property_record_id,
                    'upstream_source_id', upstream_source_id,
                    'upstream_snapshot_id', upstream_snapshot_id,
                    'upstream_record_type', record_type,
                    'scope', 'real_property_not_business_transfer',
                    'neighborhood', json_extract_string(attributes_json, '$.neighborhood'),
                    'building_class_category', json_extract_string(attributes_json, '$.building_class_category'),
                    'building_class', coalesce(
                        json_extract_string(attributes_json, '$.building_class_at_time_of'),
                        json_extract_string(attributes_json, '$.property_types')
                    ),
                    'zip_code', json_extract_string(attributes_json, '$.zip_code')
                ) AS attributes_json,
                ? AS source_id,
                ? AS snapshot_id,
                ? AS retrieved_at,
                record_date AS valid_from,
                NULL AS valid_to,
                'derived_public_administrative_case' AS measure_type,
                least(coalesce(confidence, 0.8), 0.9) AS confidence,
                '0.2.0' AS method_version
            FROM ranked
            WHERE duplicate_rank=1
            """,
            [source_id, snapshot.snapshot_id, snapshot.retrieved_at],
        )
        inserted = conn.execute(
            "SELECT count(*) FROM transaction_case WHERE source_id=?", [source_id]
        ).fetchone()[0]
        if inserted < 100_000:
            raise ValueError(f"Derived transaction-case coverage is unexpectedly low: {inserted}")
        register_table_lineage(
            conn,
            table_name="transaction_case",
            entity_type="transaction_case",
            id_column="transaction_case_id",
            source_id=source_id,
            snapshot_id=snapshot.snapshot_id,
        )
        schema = {
            row[0]: row[1]
            for row in conn.execute("DESCRIBE transaction_case").fetchall()
        }
        conn.execute(
            "UPDATE dataset_snapshot SET row_count=?, schema_json=?::JSON WHERE snapshot_id=?",
            [inserted, json_dumps(schema), snapshot.snapshot_id],
        )
        warnings = [
            manifest["scope_note"],
            "Deduplication removes same-lot, same-date, same-amount overlaps and prefers the rolling-sales record.",
            "Cases intentionally omit parties, names, street or unit addresses, contact details, and business-acquisition claims.",
        ]
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            inserted_records=inserted,
            deleted_records=old_count,
            warnings=warnings,
        )
        return {
            "run_id": run_id,
            "status": "SUCCEEDED_WITH_WARNINGS",
            "snapshot_id": snapshot.snapshot_id,
            "raw_bytes": snapshot.byte_size,
            "candidate_records": manifest["candidate_records"],
            "transaction_cases": inserted,
            "deduplicated_records": manifest["candidate_records"] - inserted,
            "warnings": warnings,
        }
    except Exception as exc:
        finish_run(
            conn,
            run_id,
            "FAILED",
            deleted_records=old_count,
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        raise
