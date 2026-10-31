from __future__ import annotations

import json
from datetime import date
from typing import Any
from urllib.parse import urlparse

import duckdb
import httpx
import pandas as pd

from ..config import get_source
from ..db import begin_run, finish_run
from ..provenance import Snapshot, download_snapshot, save_bytes_snapshot
from ..publication import publication_transaction
from ..util import json_dumps, utc_now
from .common import (
    as_date,
    as_number,
    normalized_text,
    register_standardized_records,
    register_table_lineage,
    registered_frame,
    stable_id,
    standardized_path,
    update_snapshot_details,
)


def _domain(source_id: str) -> str:
    configured = urlparse(get_source(source_id).get("api_url") or "").netloc
    return configured or "data.cityofnewyork.us"


def fetch_metadata_snapshot(
    conn: duckdb.DuckDBPyConnection,
    source_id: str,
    vintage: str,
    run_id: str,
) -> Snapshot:
    dataset_id = get_source(source_id)["dataset_id"]
    url = f"https://{_domain(source_id)}/api/views/{dataset_id}"
    response = httpx.get(url, follow_redirects=True, timeout=60, headers={"User-Agent": "NYC-Lens/0.1"})
    response.raise_for_status()
    return save_bytes_snapshot(
        conn,
        source_id=source_id,
        vintage=vintage,
        filename=f"{dataset_id}_metadata.json",
        content=response.content,
        source_url=url,
        run_id=run_id,
        content_type="application/json",
        query={"kind": "Socrata dataset metadata"},
        http_status=response.status_code,
    )


def download_socrata_csv(
    conn: duckdb.DuckDBPyConnection,
    source_id: str,
    vintage: str,
    run_id: str,
    *,
    select: str | None = None,
    where: str | None = None,
    group: str | None = None,
    order: str | None = None,
    limit: int = 2_000_000,
    valid_from: date | None = None,
    valid_to: date | None = None,
) -> Snapshot:
    source = get_source(source_id)
    params: dict[str, Any] = {"$limit": limit}
    if select:
        params["$select"] = select
    if where:
        params["$where"] = where
    if group:
        params["$group"] = group
    if order:
        params["$order"] = order
    return download_snapshot(
        conn,
        source_id=source_id,
        vintage=vintage,
        url=source["api_url"],
        filename=f"{source['dataset_id']}.csv",
        run_id=run_id,
        params=params,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def refresh_dohmh(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "dohmh_restaurants"
    run_id = begin_run(conn, source_id, "refresh")
    try:
        fetch_metadata_snapshot(conn, source_id, "current", run_id)
        select = ",".join(
            [
                "camis",
                "dba",
                "boro",
                "building",
                "street",
                "zipcode",
                "cuisine_description",
                "inspection_date",
                "action",
                "violation_code",
                "violation_description",
                "critical_flag",
                "score",
                "grade",
                "grade_date",
                "record_date",
                "latitude",
                "longitude",
                "bbl",
                "bin",
                "nta",
                "community_board",
                "council_district",
                "census_tract",
            ]
        )
        snapshot = download_socrata_csv(
            conn, source_id, "current", run_id, select=select, limit=1_000_000
        )
        frame = pd.read_csv(snapshot.path, dtype=str, keep_default_na=True, low_memory=False)
        update_snapshot_details(conn, snapshot.snapshot_id, frame)
        frame["inspection_date_parsed"] = pd.to_datetime(frame.get("inspection_date"), errors="coerce")
        frame["latitude_num"] = as_number(frame.get("latitude", pd.Series(dtype=str)))
        frame["longitude_num"] = as_number(frame.get("longitude", pd.Series(dtype=str)))
        valid_coordinates = (
            frame["longitude_num"].between(-74.25909, -73.700272)
            & frame["latitude_num"].between(40.477399, 40.917577)
        )
        frame.loc[~valid_coordinates, ["longitude_num", "latitude_num"]] = pd.NA
        frame["score_num"] = as_number(frame.get("score", pd.Series(dtype=str)))
        latest = frame.sort_values("inspection_date_parsed", na_position="first").drop_duplicates("camis", keep="last").copy()
        latest["business_location_id"] = latest["camis"].map(lambda value: stable_id("biz", source_id, value))
        latest["address_full"] = (
            latest.get("building", "").fillna("").str.strip()
            + " "
            + latest.get("street", "").fillna("").str.strip()
        ).str.strip()
        latest["normalized_name"] = latest.get("dba", "").map(normalized_text)
        latest["dedupe_key"] = latest.apply(
            lambda row: stable_id(
                "dedupe",
                normalized_text(row.get("dba")),
                normalized_text(row.get("address_full")),
                row.get("zipcode"),
            ),
            axis=1,
        )
        latest["status"] = latest.get("action", "").fillna("").map(
            lambda value: "closed_by_dohmh" if "closed" in value.lower() else "active_in_source"
        )
        now = utc_now()
        business = pd.DataFrame(
            {
                "business_location_id": latest["business_location_id"],
                "source_business_id": latest["camis"],
                "name": latest["dba"].fillna("Unnamed establishment"),
                "normalized_name": latest["normalized_name"],
                "address": latest["address_full"],
                "borough": latest.get("boro"),
                "zipcode": latest.get("zipcode"),
                "longitude": latest["longitude_num"],
                "latitude": latest["latitude_num"],
                "primary_category": "restaurant",
                "secondary_categories_json": latest.get("cuisine_description").map(lambda x: json_dumps([x] if pd.notna(x) else [])),
                "cuisine": latest.get("cuisine_description"),
                "status": latest["status"],
                "opened_at": None,
                "closed_at": None,
                "dedupe_key": latest["dedupe_key"],
                "source_id": source_id,
                "snapshot_id": snapshot.snapshot_id,
                "retrieved_at": snapshot.retrieved_at,
                "valid_from": None,
                "valid_to": None,
                "geography_type": "point",
                "geography_id": latest.get("nta"),
                "measure_type": "administrative_record",
                "confidence": latest["latitude_num"].notna().map({True: 0.95, False: 0.6}),
                "method_version": "0.1.0",
            }
        )
        inspection = pd.DataFrame(
            {
                "inspection_id": frame.apply(
                    lambda row: stable_id(
                        "insp",
                        row.get("camis"),
                        row.get("inspection_date"),
                        row.get("violation_code"),
                        row.get("grade"),
                        row.name,
                    ),
                    axis=1,
                ),
                "business_location_id": frame["camis"].map(lambda value: stable_id("biz", source_id, value)),
                "source_inspection_id": frame.apply(
                    lambda row: f"{row.get('camis','')}:{row.get('inspection_date','')}:{row.get('violation_code','')}", axis=1
                ),
                "inspection_date": frame["inspection_date_parsed"].dt.date,
                "score": frame["score_num"],
                "grade": frame.get("grade"),
                "action": frame.get("action"),
                "violation_code": frame.get("violation_code"),
                "violation_description": frame.get("violation_description"),
                "source_id": source_id,
                "snapshot_id": snapshot.snapshot_id,
                "retrieved_at": snapshot.retrieved_at,
                "valid_from": frame["inspection_date_parsed"].dt.date,
                "valid_to": None,
                "geography_type": "point",
                "geography_id": frame.get("nta"),
                "measure_type": "administrative_record",
                "confidence": 0.95,
                "method_version": "0.1.0",
            }
        )
        # Prepare an immutable derived export before changing published facts.
        output = standardized_path(source_id, snapshot.snapshot_id, "business_locations.parquet")
        business.to_parquet(output, index=False)
        with publication_transaction(conn):
            conn.execute("DELETE FROM inspection WHERE source_id=?", [source_id])
            conn.execute("DELETE FROM business_location WHERE source_id=?", [source_id])
            with registered_frame(conn, "_business", business):
                conn.execute("INSERT INTO business_location BY NAME SELECT * FROM _business")
            with registered_frame(conn, "_inspection", inspection):
                conn.execute("INSERT INTO inspection BY NAME SELECT * FROM _inspection")
            register_table_lineage(
                conn,
                table_name="inspection",
                entity_type="inspection",
                id_column="inspection_id",
                source_id=source_id,
                snapshot_id=snapshot.snapshot_id,
            )
            register_standardized_records(
                conn,
                business,
                entity_type="business_location",
                entity_id_column="business_location_id",
                source_id=source_id,
                snapshot_id=snapshot.snapshot_id,
                source_record_id_column="source_business_id",
            )
            finish_run(
                conn,
                run_id,
                "SUCCEEDED",
                request_count=2,
                downloaded_bytes=snapshot.byte_size,
                inserted_records=len(business) + len(inspection),
            )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "businesses": len(business),
            "inspection_rows": len(inspection),
            "raw_bytes": snapshot.byte_size,
            "standardized_parquet": str(output),
        }
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise


def refresh_hotels(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "nyc_hotels"
    run_id = begin_run(conn, source_id, "refresh")
    try:
        fetch_metadata_snapshot(conn, source_id, "current", run_id)
        select = "parid,borocode,block,lot,taxyear,street_num,street_name,postcode,bldg_class,borough,latitude,longitude,community_board,census_tract,bin,bbl,nta,nta_code2"
        snapshot = download_socrata_csv(conn, source_id, "current", run_id, select=select, limit=100_000)
        frame = pd.read_csv(snapshot.path, dtype=str)
        update_snapshot_details(conn, snapshot.snapshot_id, frame)
        frame["_taxyear"] = pd.to_numeric(frame.get("taxyear"), errors="coerce")
        frame["_hotel_key"] = frame.get("parid").fillna("") + "|" + frame.get("bbl").fillna("")
        frame.loc[frame["_hotel_key"].eq("|"), "_hotel_key"] = frame.index.astype(str)
        current = frame.sort_values("_taxyear", na_position="first").drop_duplicates("_hotel_key", keep="last").copy()
        lon = as_number(current.get("longitude", pd.Series(dtype=str)))
        lat = as_number(current.get("latitude", pd.Series(dtype=str)))
        addresses = (current.get("street_num", "").fillna("").str.strip() + " " + current.get("street_name", "").fillna("").str.strip()).str.strip()
        hotels = pd.DataFrame(
            {
                "hotel_id": current["_hotel_key"].map(lambda value: stable_id("hotel", source_id, value)),
                "source_hotel_id": current.get("parid").fillna(current.get("bbl")),
                "name": addresses.map(lambda value: f"Hotel property at {value}" if value else "Hotel property"),
                "address": addresses,
                "borough": current.get("borough"),
                "rooms": None,
                "status": "listed_hotel_tax_lot",
                "longitude": lon,
                "latitude": lat,
                "source_id": source_id,
                "snapshot_id": snapshot.snapshot_id,
                "retrieved_at": snapshot.retrieved_at,
                "valid_from": None,
                "valid_to": None,
                "geography_type": "point",
                "geography_id": current.get("nta_code2", current.get("nta")),
                "measure_type": "administrative_record",
                "confidence": lon.notna().map({True: 0.9, False: 0.55}),
                "method_version": "0.1.0",
            }
        )
        output = standardized_path(source_id, snapshot.snapshot_id, "hotels.parquet")
        hotels.to_parquet(output, index=False)
        with publication_transaction(conn):
            conn.execute("DELETE FROM hotel WHERE source_id=?", [source_id])
            with registered_frame(conn, "_hotels", hotels):
                conn.execute("INSERT INTO hotel BY NAME SELECT * FROM _hotels")
            register_standardized_records(
                conn,
                hotels,
                entity_type="hotel",
                entity_id_column="hotel_id",
                source_id=source_id,
                snapshot_id=snapshot.snapshot_id,
                source_record_id_column="source_hotel_id",
            )
            finish_run(conn, run_id, "SUCCEEDED", request_count=2, downloaded_bytes=snapshot.byte_size, inserted_records=len(hotels))
        return {"run_id": run_id, "snapshot_id": snapshot.snapshot_id, "hotels": len(hotels), "raw_bytes": snapshot.byte_size,
                "standardized_parquet": str(output)}
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise


def refresh_air_quality(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "air_quality_health"
    run_id = begin_run(conn, source_id, "refresh")
    try:
        fetch_metadata_snapshot(conn, source_id, "current", run_id)
        snapshot = download_socrata_csv(conn, source_id, "current", run_id, limit=100_000)
        frame = pd.read_csv(snapshot.path, dtype=str)
        update_snapshot_details(conn, snapshot.snapshot_id, frame)
        values = as_number(frame.get("data_value", pd.Series(dtype=str)))
        start_dates = pd.to_datetime(frame.get("start_date"), errors="coerce").dt.date
        health_mask = frame.get("name", "").fillna("").str.contains(
            "asthma|hospital|death|cardiovascular|respiratory", case=False, regex=True
        )
        base = pd.DataFrame(
            {
                "indicator_id": frame.get("indicator_id"),
                "name": frame.get("name"),
                "measure": frame.get("measure"),
                "value": values,
                "unit": frame.get("measure_info"),
                "message": frame.get("message"),
                "period": frame.get("time_period"),
                "source_id": source_id,
                "snapshot_id": snapshot.snapshot_id,
                "retrieved_at": snapshot.retrieved_at,
                "valid_from": start_dates,
                "valid_to": None,
                "geography_type": frame.get("geo_type_name"),
                "geography_id": frame.get("geo_join_id"),
                "confidence": 0.7,
                "method_version": "0.1.0",
            }
        )
        health = base.loc[health_mask].copy()
        health.insert(0, "health_indicator_id", [stable_id("health", source_id, value) for value in frame.loc[health_mask, "unique_id"]])
        health.insert(6, "margin_of_error", None)
        health.insert(len(health.columns) - 2, "measure_type", "area_health_estimate")
        environment = base.loc[~health_mask].copy()
        environment.insert(0, "environment_indicator_id", [stable_id("env", source_id, value) for value in frame.loc[~health_mask, "unique_id"]])
        environment.insert(len(environment.columns) - 2, "measure_type", "model_or_observation_as_published")
        output = standardized_path(source_id, snapshot.snapshot_id, "air_quality_health.parquet")
        frame.to_parquet(output, index=False)
        with publication_transaction(conn):
            conn.execute("DELETE FROM health_indicator WHERE source_id=?", [source_id])
            conn.execute("DELETE FROM environment_indicator WHERE source_id=?", [source_id])
            if not health.empty:
                with registered_frame(conn, "_health", health):
                    conn.execute("INSERT INTO health_indicator BY NAME SELECT * FROM _health")
                register_table_lineage(
                    conn,
                    table_name="health_indicator",
                    entity_type="health_indicator",
                    id_column="health_indicator_id",
                    source_id=source_id,
                    snapshot_id=snapshot.snapshot_id,
                )
            if not environment.empty:
                with registered_frame(conn, "_environment", environment):
                    conn.execute("INSERT INTO environment_indicator BY NAME SELECT * FROM _environment")
                register_table_lineage(
                    conn,
                    table_name="environment_indicator",
                    entity_type="environment_indicator",
                    id_column="environment_indicator_id",
                    source_id=source_id,
                    snapshot_id=snapshot.snapshot_id,
                )
            finish_run(conn, run_id, "SUCCEEDED", request_count=2, downloaded_bytes=snapshot.byte_size, inserted_records=len(frame))
        return {"run_id": run_id, "snapshot_id": snapshot.snapshot_id, "health_rows": len(health), "environment_rows": len(environment),
                "raw_bytes": snapshot.byte_size, "standardized_parquet": str(output)}
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise
