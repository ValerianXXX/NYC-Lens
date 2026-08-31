from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from ..db import begin_run, finish_run
from ..provenance import Snapshot, save_bytes_snapshot
from ..util import json_dumps
from ..storage import replace_source_from_relation
from .common import register_table_lineage, stable_id, standardized_path, update_snapshot_details
from .property import _normalize_bbl
from .socrata import download_socrata_csv, fetch_metadata_snapshot


SOURCE_ID = "nyc_311_recent_aggregates"
MAX_PARTITION_ROWS = 500_000
SELECT_FIELDS = (
    "bbl",
    "complaint_type",
    "agency",
    "count(*) as request_count",
    "min(created_date) as first_created_date",
    "max(created_date) as last_created_date",
)
GROUP_FIELDS = "bbl,complaint_type,agency"
PROHIBITED_311_FIELDS = {
    "unique_key",
    "descriptor",
    "descriptor_2",
    "incident_address",
    "street_name",
    "cross_street_1",
    "cross_street_2",
    "intersection_street_1",
    "intersection_street_2",
    "resolution_description",
    "taxi_pick_up_location",
    "latitude",
    "longitude",
    "location",
}


def selected_311_fields() -> set[str]:
    selected = {
        field.split(" as ", 1)[0].split("(", 1)[-1].rstrip(")*").strip().lower()
        for field in SELECT_FIELDS
    }
    return {field for field in selected if field}


def _window() -> tuple[date, date]:
    end = date.today()
    start = (pd.Timestamp(end) - pd.DateOffset(months=24)).date()
    return start, end


def _month_windows(start: date, end: date) -> list[tuple[date, date]]:
    cursor = start.replace(day=1)
    final_exclusive = end + timedelta(days=1)
    windows: list[tuple[date, date]] = []
    while cursor < final_exclusive:
        next_month = (pd.Timestamp(cursor) + pd.offsets.MonthBegin(1)).date()
        partition_start = max(cursor, start)
        partition_end = min(next_month, final_exclusive)
        windows.append((partition_start, partition_end))
        cursor = next_month
    return windows


def _partition_vintage(start: date, end_exclusive: date) -> str:
    return f"aggregate-{start.isoformat()}_{(end_exclusive - timedelta(days=1)).isoformat()}"


def _reusable_partition(
    conn: duckdb.DuckDBPyConnection,
    start: date,
    end_exclusive: date,
    current_month_start: date,
) -> Snapshot | None:
    # Completed calendar months are immutable enough to reuse; the current partial month is always refreshed.
    if end_exclusive > current_month_start:
        return None
    row = conn.execute(
        """
        SELECT snapshot_id, source_id, raw_path, metadata_path, sha256,
               byte_size, retrieved_at, vintage
        FROM dataset_snapshot
        WHERE source_id=? AND vintage=? AND status='SUCCEEDED'
          AND valid_from=? AND valid_to=?
        ORDER BY retrieved_at DESC LIMIT 1
        """,
        [SOURCE_ID, _partition_vintage(start, end_exclusive), start, end_exclusive - timedelta(days=1)],
    ).fetchone()
    if not row:
        return None
    path = Path(row[2])
    metadata_path = Path(row[3])
    if not path.is_file() or not metadata_path.is_file() or path.stat().st_size != row[5]:
        return None
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


def _aggregate_partitions(paths: list[Path]) -> pd.DataFrame:
    aggregate: pd.DataFrame | None = None
    keys = ["bbl", "complaint_type", "agency"]
    for path in paths:
        frame = pd.read_csv(path, dtype=str, keep_default_na=True, low_memory=False)
        if len(frame) >= MAX_PARTITION_ROWS:
            raise ValueError(f"311 monthly aggregate may be truncated at the row limit: {path}")
        if frame.empty:
            continue
        frame["request_count"] = pd.to_numeric(frame["request_count"], errors="coerce").fillna(0)
        frame["first_created_date"] = pd.to_datetime(frame["first_created_date"], errors="coerce")
        frame["last_created_date"] = pd.to_datetime(frame["last_created_date"], errors="coerce")
        combined = frame if aggregate is None else pd.concat([aggregate, frame], ignore_index=True)
        aggregate = (
            combined.groupby(keys, as_index=False, dropna=False)
            .agg(
                request_count=("request_count", "sum"),
                first_created_date=("first_created_date", "min"),
                last_created_date=("last_created_date", "max"),
            )
        )
    if aggregate is None or aggregate.empty:
        raise ValueError("No nonempty 311 monthly aggregate partitions were returned")
    aggregate["request_count"] = aggregate["request_count"].round().astype("int64")
    aggregate["first_created_date"] = aggregate["first_created_date"].dt.strftime("%Y-%m-%dT%H:%M:%S.%f").str[:-3]
    aggregate["last_created_date"] = aggregate["last_created_date"].dt.strftime("%Y-%m-%dT%H:%M:%S.%f").str[:-3]
    return aggregate.sort_values(keys).reset_index(drop=True)


def _build_aggregates(frame: pd.DataFrame, *, snapshot_id: str, retrieved_at: Any) -> pd.DataFrame:
    start, end = _window()
    bbl = frame.get("bbl", pd.Series(index=frame.index, dtype=str)).map(
        lambda value: _normalize_bbl(None, None, None, value)
    )
    request_count = pd.to_numeric(frame.get("request_count"), errors="coerce").fillna(0).astype("int64")
    first_created = pd.to_datetime(frame.get("first_created_date"), errors="coerce").dt.date
    last_created = pd.to_datetime(frame.get("last_created_date"), errors="coerce").dt.date
    complaint = frame.get("complaint_type", pd.Series(index=frame.index, dtype=str)).astype("string").str.strip()
    agency = frame.get("agency", pd.Series(index=frame.index, dtype=str)).astype("string").str.strip()
    attributes = pd.DataFrame(
        {
            "complaint_type": complaint,
            "agency": agency,
            "request_count": request_count,
            "first_created_date": first_created,
            "last_created_date": last_created,
            "window_start": start,
            "window_end": end,
        }
    )
    attributes_json = attributes.astype(object).where(pd.notna(attributes), None).to_dict("records")
    records = pd.DataFrame(
        {
            "regulatory_record_id": [
                stable_id("reg", SOURCE_ID, bbl_value, problem, agency_value, start, end)
                for bbl_value, problem, agency_value in zip(bbl, complaint, agency)
            ],
            "parcel_id": bbl.map(lambda value: f"parcel_{value}" if value else None),
            "record_type": "311_request_aggregate_24m",
            "status": agency,
            "issued_at": last_created,
            "closed_at": None,
            "attributes_json": [json_dumps(value) for value in attributes_json],
            "source_id": SOURCE_ID,
            "snapshot_id": snapshot_id,
            "retrieved_at": retrieved_at,
            "valid_from": first_created,
            "valid_to": last_created,
            "geography_type": bbl.notna().map({True: "tax_lot", False: "unresolved_property"}),
            "geography_id": bbl,
            "measure_type": "server_aggregated_administrative_request_count",
            "confidence": bbl.notna().map({True: 0.86, False: 0.55}),
            "method_version": "0.1.0",
        }
    )
    records["_request_count"] = request_count.to_numpy()
    return records


def refresh_311_aggregates(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Load only server-side 24-month BBL/problem aggregates, never individual 311 rows."""
    prohibited = selected_311_fields() & PROHIBITED_311_FIELDS
    if prohibited:
        raise ValueError(f"Prohibited individual-level 311 fields selected: {sorted(prohibited)}")
    start, end = _window()
    run_id = begin_run(conn, SOURCE_ID, "refresh privacy-minimized rolling 24-month 311 BBL aggregates")
    request_count = 0
    downloaded_bytes = 0
    partitions: list[Snapshot] = []
    reused_partitions = 0
    try:
        metadata = fetch_metadata_snapshot(conn, SOURCE_ID, "current", run_id)
        request_count += 1
        downloaded_bytes += metadata.byte_size
        current_month_start = end.replace(day=1)
        for partition_start, partition_end in _month_windows(start, end):
            snapshot = _reusable_partition(
                conn,
                partition_start,
                partition_end,
                current_month_start,
            )
            if snapshot is None:
                where = (
                    f"created_date >= '{partition_start.isoformat()}T00:00:00.000' "
                    f"AND created_date < '{partition_end.isoformat()}T00:00:00.000' "
                    "AND bbl IS NOT NULL AND bbl != '0'"
                )
                snapshot = download_socrata_csv(
                    conn,
                    SOURCE_ID,
                    _partition_vintage(partition_start, partition_end),
                    run_id,
                    select=",".join(SELECT_FIELDS),
                    where=where,
                    group=GROUP_FIELDS,
                    order="bbl,complaint_type,agency",
                    limit=MAX_PARTITION_ROWS,
                    valid_from=partition_start,
                    valid_to=partition_end - timedelta(days=1),
                )
                request_count += 1
                downloaded_bytes += snapshot.byte_size
                partition_frame = pd.read_csv(
                    snapshot.path,
                    dtype=str,
                    keep_default_na=True,
                    low_memory=False,
                )
                if len(partition_frame) >= MAX_PARTITION_ROWS:
                    raise ValueError(
                        f"311 partition reached the row safety limit for {partition_start}"
                    )
                update_snapshot_details(conn, snapshot.snapshot_id, partition_frame)
            else:
                reused_partitions += 1
            partitions.append(snapshot)
        frame = _aggregate_partitions([snapshot.path for snapshot in partitions])
        snapshot = save_bytes_snapshot(
            conn,
            source_id=SOURCE_ID,
            vintage=f"rolling-24m-{end.isoformat()}",
            filename="bbl_problem_agency_24m.csv",
            content=frame.to_csv(index=False).encode("utf-8"),
            source_url="https://data.cityofnewyork.us/resource/erm2-nwe9.csv",
            run_id=run_id,
            content_type="text/csv",
            query={
                "method": "local sum of bounded monthly server-side BBL x problem x agency aggregates",
                "partition_snapshot_ids": [item.snapshot_id for item in partitions],
                "$select": ",".join(SELECT_FIELDS),
                "$group": GROUP_FIELDS,
                "window_start": start,
                "window_end": end,
            },
            valid_from=start,
            valid_to=end,
            warnings=[
                "This combined Raw snapshot contains aggregates only; no individual 311 rows were downloaded.",
            ],
        )
        records = _build_aggregates(
            frame,
            snapshot_id=snapshot.snapshot_id,
            retrieved_at=snapshot.retrieved_at,
        )
        if records["regulatory_record_id"].duplicated().any():
            raise ValueError("311 aggregate produced duplicate BBL/problem/agency identifiers")
        unresolved = int(records["geography_id"].isna().sum())
        nonpositive = int((records["_request_count"] <= 0).sum())
        if nonpositive:
            raise ValueError(f"311 aggregate has {nonpositive} nonpositive groups")
        previous = int(
            conn.execute("SELECT count(*) FROM regulatory_record WHERE source_id=?", [SOURCE_ID]).fetchone()[0]
        )
        insert_records = records.drop(columns="_request_count")
        conn.register("_311_aggregates", insert_records)
        transaction_open = False
        try:
            previous, published = replace_source_from_relation(
                conn,
                table_name="regulatory_record",
                source_id=SOURCE_ID,
                relation="_311_aggregates",
            )
            if published != len(insert_records):
                raise ValueError(f"311 published row mismatch: {published}/{len(insert_records)}")
            conn.execute("BEGIN TRANSACTION")
            transaction_open = True
            conn.execute(
                "DELETE FROM standardized_record WHERE entity_type='regulatory_record' AND source_id=?",
                [SOURCE_ID],
            )
            register_table_lineage(
                conn,
                table_name="regulatory_record",
                entity_type="regulatory_record",
                id_column="regulatory_record_id",
                source_id=SOURCE_ID,
                snapshot_id=snapshot.snapshot_id,
            )
            insert_records.to_parquet(
                standardized_path(SOURCE_ID, "current", "bbl_problem_agency_24m.parquet"),
                index=False,
            )
            conn.execute("COMMIT")
            transaction_open = False
        except Exception:
            if transaction_open:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.unregister("_311_aggregates")
        total_requests = int(records["_request_count"].sum())
        update_snapshot_details(
            conn,
            snapshot.snapshot_id,
            frame,
            warnings=[
                "Raw is already a server-side BBL x problem x agency aggregate; no individual service-request rows were downloaded.",
                "Incident address, coordinates, descriptors, resolution text and unique request ids were not requested.",
            ],
        )
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS" if unresolved else "SUCCEEDED",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            inserted_records=len(insert_records),
            deleted_records=previous,
            warnings=[
                "311 counts are requests, not verified unique incidents, legal findings or proof of an ongoing condition.",
                f"Invalid BBL groups excluded from site linkage: {unresolved}.",
                f"Used {len(partitions)} monthly aggregate partitions; reused {reused_partitions} completed partitions.",
            ],
        )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "partition_snapshot_ids": [item.snapshot_id for item in partitions],
            "partitions": len(partitions),
            "reused_partitions": reused_partitions,
            "window_start": start,
            "window_end": end,
            "aggregate_groups": len(insert_records),
            "total_requests": total_requests,
            "tax_lots": int(insert_records["geography_id"].nunique()),
            "problem_types": int(frame["complaint_type"].nunique()),
            "unresolved_bbl_groups": unresolved,
            "individual_rows_downloaded": False,
            "excluded_sensitive_location_fields": True,
            "downloaded_bytes_this_run": downloaded_bytes,
            "raw_partition_bytes": sum(item.byte_size for item in partitions),
        }
    except Exception as exc:
        finish_run(
            conn,
            run_id,
            "FAILED",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            error={"type": type(exc).__name__, "message": str(exc)},
            warnings=[f"Completed monthly aggregate partitions remain reusable: {len(partitions)}"],
        )
        raise
