from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import duckdb
import httpx
import pandas as pd
from pyproj import Geod

from ..db import begin_run, finish_run
from ..geocoding.service import geocode_address
from ..paths import project_home
from ..util import new_id, sha256_file, utc_now


BOROUGHS = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]
POSTAL_CITY = {
    "Manhattan": "New York",
    "Brooklyn": "Brooklyn",
    "Queens": "Queens",
    "Bronx": "Bronx",
    "Staten Island": "Staten Island",
}
GEOD = Geod(ellps="WGS84")
METHOD_VERSION = "0.1.0"


def _latest_dohmh_csv_snapshot(conn: duckdb.DuckDBPyConnection) -> tuple[str, Path]:
    rows = conn.execute(
        """
        SELECT snapshot_id, raw_path
        FROM dataset_snapshot
        WHERE source_id='dohmh_restaurants' AND status='SUCCEEDED'
          AND lower(raw_path) LIKE '%.csv'
        ORDER BY retrieved_at DESC
        """
    ).fetchall()
    for snapshot_id, raw_path in rows:
        path = Path(raw_path)
        if path.is_file():
            return snapshot_id, path
    raise FileNotFoundError("No local DOHMH CSV snapshot is available for address validation")


def _deterministic_public_sample(path: Path, sample_size: int) -> pd.DataFrame:
    if sample_size < len(BOROUGHS):
        raise ValueError(f"sample_size must be at least {len(BOROUGHS)}")
    wanted = {
        "camis", "building", "street", "zipcode", "boro", "bbl", "longitude", "latitude"
    }
    frame = pd.read_csv(
        path,
        dtype=str,
        keep_default_na=True,
        low_memory=False,
        usecols=lambda column: column in wanted,
    )
    missing = wanted - set(frame.columns)
    if missing:
        raise ValueError(f"DOHMH validation source is missing columns: {', '.join(sorted(missing))}")
    for column in ("camis", "building", "street", "zipcode", "boro", "bbl"):
        frame[column] = frame[column].fillna("").str.strip()
    frame["boro"] = frame["boro"].str.title()
    frame["bbl"] = frame["bbl"].str.replace(r"\.0$", "", regex=True)
    frame["longitude_num"] = pd.to_numeric(frame["longitude"], errors="coerce")
    frame["latitude_num"] = pd.to_numeric(frame["latitude"], errors="coerce")
    frame = frame.loc[
        frame["boro"].isin(BOROUGHS)
        & frame["building"].ne("")
        & frame["street"].ne("")
        & frame["zipcode"].str.fullmatch(r"\d{5}")
        & frame["bbl"].str.fullmatch(r"[1-5]\d{9}")
        & frame["longitude_num"].between(-74.25909, -73.700272)
        & frame["latitude_num"].between(40.477399, 40.917577)
    ].copy()
    frame["input_address"] = frame.apply(
        lambda row: (
            f"{row['building']} {row['street']}, {POSTAL_CITY[row['boro']]}, "
            f"NY {row['zipcode']}"
        ),
        axis=1,
    )
    frame = frame.drop_duplicates(["camis"]).drop_duplicates(["input_address"])
    frame["sample_key"] = frame.apply(
        lambda row: hashlib.sha256(
            f"{row['boro']}|{row['camis']}|{row['input_address']}|{row['bbl']}".encode("utf-8")
        ).hexdigest(),
        axis=1,
    )
    base, remainder = divmod(sample_size, len(BOROUGHS))
    selected: list[pd.DataFrame] = []
    for index, borough in enumerate(BOROUGHS):
        requested = base + (1 if index < remainder else 0)
        candidates = frame.loc[frame["boro"].eq(borough)].sort_values("sample_key")
        if len(candidates) < requested:
            raise ValueError(f"Not enough valid public DOHMH addresses for {borough}: {len(candidates)}")
        selected.append(candidates.head(requested))
    return pd.concat(selected, ignore_index=True)


def _coordinate_error_m(
    expected_longitude: float,
    expected_latitude: float,
    observed_longitude: float,
    observed_latitude: float,
) -> float:
    return float(
        GEOD.inv(
            expected_longitude,
            expected_latitude,
            observed_longitude,
            observed_latitude,
        )[2]
    )


def validate_public_addresses(
    conn: duckdb.DuckDBPyConnection,
    *,
    sample_size: int = 100,
    maximum_coordinate_error_m: float = 250.0,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    source_snapshot_id, source_path = _latest_dohmh_csv_snapshot(conn)
    sample = _deterministic_public_sample(source_path, sample_size)
    batch_id = begin_run(conn, "census_geocoder", "public address validation")
    before_count, before_bytes = conn.execute(
        """
        SELECT count(*), coalesce(sum(byte_size), 0)
        FROM dataset_snapshot WHERE source_id='census_geocoder'
        """
    ).fetchone()
    checked_at = utc_now()
    rows: list[dict[str, Any]] = []
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=60,
            headers={"User-Agent": "NYC-Lens/0.1 local-research"},
            limits=httpx.Limits(max_keepalive_connections=2, max_connections=4),
        ) as client:
            for index, source_row in sample.iterrows():
                input_address = str(source_row["input_address"])
                record: dict[str, Any] | None = None
                error: Exception | None = None
                try:
                    record = geocode_address(conn, input_address, use_cache=True, client=client)
                except Exception as exc:  # validation must preserve failed cases
                    error = exc
                matched = bool(record and record.get("match_status") == "MATCH")
                observed_longitude = float(record["longitude"]) if matched else None
                observed_latitude = float(record["latitude"]) if matched else None
                coordinate_error = (
                    _coordinate_error_m(
                        float(source_row["longitude_num"]),
                        float(source_row["latitude_num"]),
                        observed_longitude,
                        observed_latitude,
                    )
                    if matched and observed_longitude is not None and observed_latitude is not None
                    else None
                )
                borough_match = bool(matched and record.get("borough") == source_row["boro"])
                success = bool(
                    matched
                    and borough_match
                    and coordinate_error is not None
                    and coordinate_error <= maximum_coordinate_error_m
                )
                validation_case_id = new_id("addrval")
                row = {
                    "validation_case_id": validation_case_id,
                    "validation_batch_id": batch_id,
                    "source_business_id": source_row["camis"],
                    "input_address": input_address,
                    "expected_borough": source_row["boro"],
                    "expected_bbl": source_row["bbl"],
                    "expected_longitude": float(source_row["longitude_num"]),
                    "expected_latitude": float(source_row["latitude_num"]),
                    "matched": matched,
                    "standardized_address": record.get("standardized_address") if record else None,
                    "observed_borough": record.get("borough") if record else None,
                    "observed_longitude": observed_longitude,
                    "observed_latitude": observed_latitude,
                    "coordinate_error_m": coordinate_error,
                    "borough_match": borough_match,
                    "success": success,
                    "error_type": type(error).__name__ if error else None,
                    "error_message": str(error) if error else None,
                    "source_id": "dohmh_restaurants",
                    "source_snapshot_id": source_snapshot_id,
                    "geocoder_snapshot_id": record.get("snapshot_id") if record else None,
                    "checked_at": checked_at,
                    "method_version": METHOD_VERSION,
                }
                conn.execute(
                    "INSERT INTO address_validation_case BY NAME SELECT "
                    + ",".join(f"? AS {column}" for column in row),
                    list(row.values()),
                )
                rows.append(row)
                if progress:
                    progress(index + 1, len(sample), input_address)

        successful = sum(bool(row["success"]) for row in rows)
        matched_count = sum(bool(row["matched"]) for row in rows)
        borough_matches = sum(bool(row["borough_match"]) for row in rows)
        within_threshold = sum(
            row["coordinate_error_m"] is not None
            and row["coordinate_error_m"] <= maximum_coordinate_error_m
            for row in rows
        )
        success_rate = successful / len(rows) if rows else 0.0
        report = {
            "validation_batch_id": batch_id,
            "checked_at": checked_at.isoformat(),
            "source_id": "dohmh_restaurants",
            "source_snapshot_id": source_snapshot_id,
            "source_path": str(source_path),
            "sampling": "deterministic stratified sample of public DOHMH establishments",
            "boroughs": BOROUGHS,
            "sample_size": len(rows),
            "maximum_coordinate_error_m": maximum_coordinate_error_m,
            "success_definition": "official match, correct borough, and coordinate error within threshold",
            "successful": successful,
            "success_rate": success_rate,
            "matched": matched_count,
            "match_rate": matched_count / len(rows),
            "borough_matches": borough_matches,
            "borough_match_rate": borough_matches / len(rows),
            "within_coordinate_threshold": within_threshold,
            "within_coordinate_threshold_rate": within_threshold / len(rows),
            "cases": rows,
        }
        report_dir = project_home() / "reports" / "qa"
        report_dir.mkdir(parents=True, exist_ok=True)
        json_path = report_dir / f"address_validation_{batch_id}.json"
        csv_path = report_dir / f"address_validation_{batch_id}.csv"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
        latest_path = report_dir / "address_validation_latest.json"
        latest_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        after_count, after_bytes = conn.execute(
            """
            SELECT count(*), coalesce(sum(byte_size), 0)
            FROM dataset_snapshot WHERE source_id='census_geocoder'
            """
        ).fetchone()
        status = "SUCCEEDED" if len(rows) >= 100 and success_rate >= 0.98 else "SUCCEEDED_WITH_WARNINGS"
        finish_run(
            conn,
            batch_id,
            status,
            request_count=after_count - before_count,
            downloaded_bytes=after_bytes - before_bytes,
            inserted_records=len(rows),
            warnings=[] if status == "SUCCEEDED" else [f"Observed address validation rate was {success_rate:.2%}."],
        )
        return {
            **{key: value for key, value in report.items() if key != "cases"},
            "status": status,
            "json_report": str(json_path),
            "json_sha256": sha256_file(json_path),
            "csv_report": str(csv_path),
            "csv_sha256": sha256_file(csv_path),
            "latest_report": str(latest_path),
            "new_geocoder_requests": after_count - before_count,
            "new_geocoder_bytes": after_bytes - before_bytes,
        }
    except Exception as exc:
        finish_run(
            conn,
            batch_id,
            "FAILED",
            inserted_records=len(rows),
            error={"type": type(exc).__name__, "message": str(exc)},
            warnings=[f"Partial validation cases retained: {len(rows)}"],
        )
        raise
