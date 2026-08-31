from __future__ import annotations

import json
from typing import Any

import duckdb
import pandas as pd
from shapely import from_wkt

from ..config import get_source
from ..db import begin_run, finish_run
from ..provenance import download_snapshot
from ..storage import replace_source_from_relation
from ..util import json_dumps, utc_now
from .common import (
    as_date,
    as_number,
    normalized_text,
    register_standardized_records,
    register_table_lineage,
    stable_id,
    standardized_path,
    update_snapshot_details,
)
from .socrata import download_socrata_csv, fetch_metadata_snapshot


NYC_LICENSE_COUNTIES = ("NEW YORK", "KINGS", "QUEENS", "BRONX", "RICHMOND")


def _point_coordinates(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    points = series.fillna("").map(
        lambda value: from_wkt(value) if isinstance(value, str) and value.upper().startswith("POINT") else None
    )
    longitude = points.map(lambda point: point.x if point is not None and not point.is_empty else None)
    latitude = points.map(lambda point: point.y if point is not None and not point.is_empty else None)
    valid = longitude.between(-74.25909, -73.700272) & latitude.between(40.477399, 40.917577)
    return longitude.where(valid), latitude.where(valid)


def _facility_category(row: pd.Series) -> str:
    domain = normalized_text(row.get("facdomain"))
    detail = " ".join(
        normalized_text(row.get(column)) for column in ("facgroup", "facsubgrp", "factype")
    )
    text = f"{domain} {detail}"
    if any(token in text for token in ("SCHOOL", "EDUCATION", "COLLEGE", "UNIVERSITY")):
        return "school"
    if any(token in text for token in ("HOSPITAL", "HEALTH", "CLINIC", "MEDICAL")):
        return "health_facility"
    if any(token in text for token in ("PARK", "GARDEN", "PLAYGROUND", "RECREATION")):
        return "park"
    if "LIBRAR" in detail:
        return "library"
    if any(token in detail for token in ("CULTUR", "MUSEUM", "THEATER", "THEATRE", "HISTORICAL")) or "CULTUR" in domain:
        return "cultural_facility"
    if any(token in text for token in ("TRANSIT", "TRANSPORTATION")):
        return "transport_facility"
    if any(token in text for token in ("POLICE", "FIRE", "PUBLIC SAFETY", "JUSTICE")):
        return "public_safety"
    return "public_facility"


def refresh_nysla_licenses(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "nysla_active_licenses"
    run_id = begin_run(conn, source_id, "refresh NYC active-license extract")
    try:
        metadata = fetch_metadata_snapshot(conn, source_id, "current", run_id)
        select = ",".join(
            [
                "licensepermitid",
                "premisescounty",
                "dba",
                "actualaddressofpremises",
                "city",
                "zipcode",
                "description",
                "originalissuedate",
                "effectivedate",
                "expirationdate",
                "georeference",
            ]
        )
        county_values = ",".join(f"'{county}'" for county in NYC_LICENSE_COUNTIES)
        snapshot = download_socrata_csv(
            conn,
            source_id,
            "current",
            run_id,
            select=select,
            where=f"upper(premisescounty) in({county_values})",
            order="licensepermitid",
            limit=100_000,
        )
        frame = pd.read_csv(snapshot.path, dtype=str, keep_default_na=True)
        duplicate_rows = int(frame.duplicated("licensepermitid").sum())
        update_snapshot_details(
            conn,
            snapshot.snapshot_id,
            frame,
            warnings=[
                "Only five-county, non-personal analysis fields were requested from the statewide source.",
                f"Exact duplicate license identifiers removed in standardization: {duplicate_rows}.",
            ],
        )
        frame = frame.drop_duplicates("licensepermitid", keep="first").copy()
        longitude, latitude = _point_coordinates(frame.get("georeference", pd.Series(index=frame.index, dtype=str)))
        issued = as_date(frame.get("originalissuedate", pd.Series(index=frame.index, dtype=str)))
        valid_from = as_date(frame.get("effectivedate", pd.Series(index=frame.index, dtype=str)))
        valid_to = as_date(frame.get("expirationdate", pd.Series(index=frame.index, dtype=str)))
        source_keys = frame["licensepermitid"].fillna("")
        business_ids = source_keys.map(lambda value: stable_id("biz", source_id, value))
        names = frame.get("dba", pd.Series(index=frame.index, dtype=str)).fillna("").str.strip()
        names = names.mask(names.eq(""), "Licensed premises")
        addresses = frame.get("actualaddressofpremises", pd.Series(index=frame.index, dtype=str)).fillna("").str.strip()
        borough_map = {
            "NEW YORK": "Manhattan",
            "KINGS": "Brooklyn",
            "QUEENS": "Queens",
            "BRONX": "Bronx",
            "RICHMOND": "Staten Island",
        }
        boroughs = frame["premisescounty"].fillna("").str.upper().map(borough_map)
        business = pd.DataFrame(
            {
                "business_location_id": business_ids,
                "source_business_id": source_keys,
                "name": names,
                "normalized_name": names.map(normalized_text),
                "address": addresses,
                "borough": boroughs,
                "zipcode": frame.get("zipcode"),
                "longitude": longitude,
                "latitude": latitude,
                "primary_category": "alcohol_licensed_premises",
                "secondary_categories_json": frame.get("description").map(
                    lambda value: json_dumps([value] if pd.notna(value) else [])
                ),
                "cuisine": None,
                "status": "active_license",
                "opened_at": issued,
                "closed_at": None,
                "dedupe_key": [stable_id("dedupe", source_id, value) for value in source_keys],
                "source_id": source_id,
                "snapshot_id": snapshot.snapshot_id,
                "retrieved_at": snapshot.retrieved_at,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "geography_type": "point",
                "geography_id": frame.get("zipcode"),
                "measure_type": "administrative_record",
                "confidence": longitude.notna().map({True: 0.95, False: 0.7}),
                "method_version": "0.1.0",
            }
        )
        licenses = pd.DataFrame(
            {
                "license_id": source_keys.map(lambda value: stable_id("license", source_id, value)),
                "source_license_id": source_keys,
                "business_location_id": business_ids,
                "license_type": frame.get("description"),
                "status": "active",
                "issued_at": issued,
                "expires_at": valid_to,
                "source_id": source_id,
                "snapshot_id": snapshot.snapshot_id,
                "retrieved_at": snapshot.retrieved_at,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "geography_type": "point",
                "geography_id": frame.get("zipcode"),
                "measure_type": "administrative_record",
                "confidence": 0.95,
                "method_version": "0.1.0",
            }
        )
        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM license WHERE source_id=?", [source_id])
            conn.execute("DELETE FROM business_location WHERE source_id=?", [source_id])
            conn.register("_license_business", business)
            conn.register("_licenses", licenses)
            conn.execute("INSERT INTO business_location BY NAME SELECT * FROM _license_business")
            conn.execute("INSERT INTO license BY NAME SELECT * FROM _licenses")
            conn.unregister("_license_business")
            conn.unregister("_licenses")
            register_standardized_records(
                conn,
                business,
                entity_type="business_location",
                entity_id_column="business_location_id",
                source_id=source_id,
                snapshot_id=snapshot.snapshot_id,
                source_record_id_column="source_business_id",
            )
            register_standardized_records(
                conn,
                licenses,
                entity_type="license",
                entity_id_column="license_id",
                source_id=source_id,
                snapshot_id=snapshot.snapshot_id,
                source_record_id_column="source_license_id",
            )
            conn.execute("COMMIT")
        except Exception:
            for name in ("_license_business", "_licenses"):
                try:
                    conn.unregister(name)
                except Exception:
                    pass
            conn.execute("ROLLBACK")
            raise
        business.to_parquet(standardized_path(source_id, "current", "business_locations.parquet"), index=False)
        licenses.to_parquet(standardized_path(source_id, "current", "licenses.parquet"), index=False)
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=2,
            downloaded_bytes=metadata.byte_size + snapshot.byte_size,
            inserted_records=len(business) + len(licenses),
            warnings=[
                "Legal-name and other unnecessary source fields were not requested.",
                f"Removed {duplicate_rows} exact duplicate source rows by license identifier.",
            ],
        )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "licenses": len(licenses),
            "licensed_locations": len(business),
            "locations_with_coordinates": int(longitude.notna().sum()),
            "duplicate_source_rows_removed": duplicate_rows,
            "raw_bytes": metadata.byte_size + snapshot.byte_size,
        }
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise


def refresh_facilities(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "nyc_facilities"
    run_id = begin_run(conn, source_id, "refresh citywide facilities")
    try:
        metadata = fetch_metadata_snapshot(conn, source_id, "current", run_id)
        select = ",".join(
            [
                "uid",
                "facname",
                "address",
                "city",
                "boro",
                "zipcode",
                "latitude",
                "longitude",
                "bin",
                "bbl",
                "nta2020",
                "facgroup",
                "facsubgrp",
                "factype",
                "capacity",
                "captype",
                "optype",
                "opname",
                "overagency",
                "datasource",
                "facdomain",
            ]
        )
        snapshot = download_socrata_csv(
            conn,
            source_id,
            "current",
            run_id,
            select=select,
            order="uid",
            limit=100_000,
        )
        frame = pd.read_csv(snapshot.path, dtype=str, keep_default_na=True, low_memory=False)
        update_snapshot_details(conn, snapshot.snapshot_id, frame)
        longitude = as_number(frame.get("longitude", pd.Series(index=frame.index, dtype=str)))
        latitude = as_number(frame.get("latitude", pd.Series(index=frame.index, dtype=str)))
        valid = longitude.between(-74.25909, -73.700272) & latitude.between(40.477399, 40.917577)
        longitude = longitude.where(valid)
        latitude = latitude.where(valid)
        categories = frame.apply(_facility_category, axis=1)
        taxonomy_json = frame.apply(
            lambda row: json_dumps(
                {
                    "domain": row.get("facdomain"),
                    "group": row.get("facgroup"),
                    "subgroup": row.get("facsubgrp"),
                    "type": row.get("factype"),
                    "operator_type": row.get("optype"),
                    "operator": row.get("opname"),
                    "oversight_agency": row.get("overagency"),
                    "capacity": row.get("capacity"),
                    "capacity_type": row.get("captype"),
                }
            ),
            axis=1,
        )
        source_item_json = frame.apply(
            lambda row: json_dumps(
                {
                    "uid": row.get("uid"),
                    "source_dataset": row.get("datasource"),
                    "bin": row.get("bin"),
                    "bbl": row.get("bbl"),
                    "facility_type": row.get("factype"),
                    "operator": row.get("opname"),
                    "oversight_agency": row.get("overagency"),
                }
            ),
            axis=1,
        )
        pois = pd.DataFrame(
            {
                "poi_id": frame["uid"].map(lambda value: stable_id("poi", source_id, value)),
                "source_poi_id": frame["uid"],
                "name": frame.get("facname"),
                "basic_category": categories,
                "taxonomy_json": taxonomy_json,
                "address": frame.get("address"),
                "longitude": longitude,
                "latitude": latitude,
                "source_items_json": source_item_json,
                "status": "published_facility",
                "source_id": source_id,
                "snapshot_id": snapshot.snapshot_id,
                "retrieved_at": snapshot.retrieved_at,
                "valid_from": None,
                "valid_to": None,
                "geography_type": "point",
                "geography_id": frame.get("nta2020"),
                "measure_type": "compiled_administrative_record",
                "confidence": longitude.notna().map({True: 0.9, False: 0.65}),
                "method_version": "0.1.0",
            }
        )
        location_keys = pd.Series(index=pois.index, dtype="string")
        bbl = pd.to_numeric(frame.get("bbl"), errors="coerce").round().astype("Int64").astype("string")
        bin_id = pd.to_numeric(frame.get("bin"), errors="coerce").round().astype("Int64").astype("string")
        with_bbl = bbl.notna()
        with_bin = ~with_bbl & bin_id.notna()
        with_points = (
            ~with_bbl
            & ~with_bin
            & pois["longitude"].notna()
            & pois["latitude"].notna()
        )
        location_keys.loc[with_bbl] = "BBL:" + bbl.loc[with_bbl]
        location_keys.loc[with_bin] = "BIN:" + bin_id.loc[with_bin]
        location_keys.loc[with_points] = (
            "POINT:"
            + pois.loc[with_points, "longitude"].round(5).map(lambda value: f"{value:.5f}").astype("string")
            + ","
            + pois.loc[with_points, "latitude"].round(5).map(lambda value: f"{value:.5f}").astype("string")
        )
        fallback = location_keys.isna()
        location_keys.loc[fallback] = (
            "ADDRESS:"
            + pois.loc[fallback, "address"].map(normalized_text)
            + "|"
            + frame.loc[fallback, "boro"].map(normalized_text)
            + "|"
            + frame.loc[fallback, "zipcode"].fillna("")
        )
        pois["_dedupe_key"] = [
            stable_id(
                "facility_point",
                normalized_text(name) or source_poi_id,
                category,
                location_key,
            )
            for name, source_poi_id, category, location_key in zip(
                pois["name"], pois["source_poi_id"], pois["basic_category"], location_keys
            )
        ]
        deduplicated: list[dict[str, Any]] = []
        for dedupe_key, group in pois.groupby("_dedupe_key", sort=True):
            record = group.iloc[0].drop(labels=["_dedupe_key"]).to_dict()
            source_ids = sorted(group["source_poi_id"].astype(str).unique())
            source_items = [json.loads(value) for value in group["source_items_json"]]
            record["poi_id"] = stable_id("poi", source_id, dedupe_key)
            record["source_poi_id"] = "|".join(source_ids)
            record["source_items_json"] = json_dumps(source_items)
            deduplicated.append(record)
        raw_facility_rows = len(pois)
        pois = pd.DataFrame(deduplicated)
        collapsed_program_rows = raw_facility_rows - len(pois)
        if pois["poi_id"].duplicated().any():
            raise ValueError("NYC facilities transformation produced duplicate POI identifiers")
        conn.register("_facility_pois", pois)
        try:
            previous, published = replace_source_from_relation(
                conn,
                table_name="poi",
                source_id=source_id,
                relation="_facility_pois",
                region="nyc",
            )
        finally:
            conn.unregister("_facility_pois")
        if published != len(pois):
            raise ValueError(f"NYC facilities published row mismatch: {published:,}/{len(pois):,}")
        conn.execute("BEGIN")
        try:
            conn.execute(
                "DELETE FROM standardized_record WHERE entity_type='poi' AND source_id=?",
                [source_id],
            )
            register_standardized_records(
                conn,
                pois,
                entity_type="poi",
                entity_id_column="poi_id",
                source_id=source_id,
                snapshot_id=snapshot.snapshot_id,
                source_record_id_column="source_poi_id",
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        pois.to_parquet(standardized_path(source_id, "current", "facilities.parquet"), index=False)
        finish_run(
            conn,
            run_id,
            "SUCCEEDED",
            request_count=2,
            downloaded_bytes=metadata.byte_size + snapshot.byte_size,
            inserted_records=len(pois),
            deleted_records=previous,
            warnings=[
                f"Collapsed {collapsed_program_rows} co-located same-name and same-category program rows into physical facility points; all source items remain in source_items_json."
            ],
        )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "source_facility_rows": raw_facility_rows,
            "facility_points": len(pois),
            "facility_points_with_coordinates": int(pois["longitude"].notna().sum()),
            "co_located_program_rows_collapsed": collapsed_program_rows,
            "categories": pois["basic_category"].value_counts().to_dict(),
            "raw_bytes": metadata.byte_size + snapshot.byte_size,
        }
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise


def _prepare_str_registrations(frame: pd.DataFrame) -> pd.DataFrame:
    renamed = frame.rename(
        columns={
            frame.columns[0]: "registration_number",
            frame.columns[1]: "status",
            frame.columns[2]: "expiration_date",
            frame.columns[3]: "street_address",
            frame.columns[4]: "unit_number",
            frame.columns[5]: "borough",
            frame.columns[6]: "zipcode",
            frame.columns[7]: "bin",
            frame.columns[8]: "booking_service",
            frame.columns[9]: "listing_identifier",
        }
    ).copy()
    identity_columns = [
        "registration_number",
        "status",
        "expiration_date",
        "street_address",
        "unit_number",
        "borough",
        "zipcode",
        "bin",
    ]
    renamed[identity_columns] = renamed[identity_columns].ffill()
    renamed = renamed.loc[renamed["registration_number"].notna()].copy()
    grouped = renamed.groupby("registration_number", as_index=False, sort=True).agg(
        status=("status", "first"),
        expiration_date=("expiration_date", "first"),
        street_address=("street_address", "first"),
        borough=("borough", "first"),
        zipcode=("zipcode", "first"),
        bin=("bin", "first"),
        listing_count=("listing_identifier", lambda values: int(values.notna().sum())),
        booking_service_count=("booking_service", lambda values: int(values.dropna().nunique())),
    )
    return grouped


def refresh_short_term_rentals(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "nyc_str_registration"
    source = get_source(source_id)
    vintage = "2026-01-07"
    run_id = begin_run(conn, source_id, "refresh public registration inventory")
    try:
        page = download_snapshot(
            conn,
            source_id=source_id,
            vintage=vintage,
            url=source["url"],
            filename="registration_and_listing_data.html",
            run_id=run_id,
        )
        snapshot = download_snapshot(
            conn,
            source_id=source_id,
            vintage=vintage,
            url=source["api_url"],
            filename="January_7_2026_STR_Registration_Dataset.xlsx",
            run_id=run_id,
            valid_to=pd.Timestamp("2026-01-07").date(),
        )
        raw = pd.read_excel(snapshot.path, header=1, dtype=str)
        prepared = _prepare_str_registrations(raw)
        update_snapshot_details(
            conn,
            snapshot.snapshot_id,
            raw,
            warnings=[
                "Standardized layer omits unit numbers, booking-service names, and listing identifiers.",
                "Latest official publication found during refresh is dated 2026-01-07.",
            ],
        )
        building_points = conn.execute(
            """
            SELECT bin,
                   avg((min_longitude + max_longitude) / 2.0) AS longitude,
                   avg((min_latitude + max_latitude) / 2.0) AS latitude
            FROM building
            WHERE source_id='nyc_building_footprints' AND bin IS NOT NULL
              AND min_longitude IS NOT NULL AND max_longitude IS NOT NULL
              AND min_latitude IS NOT NULL AND max_latitude IS NOT NULL
            GROUP BY bin
            """
        ).fetchdf()
        prepared["bin"] = prepared["bin"].astype(str).str.replace(r"\.0$", "", regex=True)
        prepared = prepared.merge(building_points, on="bin", how="left")
        expiration = as_date(prepared["expiration_date"])
        records = pd.DataFrame(
            {
                "short_term_rental_id": prepared["registration_number"].map(
                    lambda value: stable_id("str", source_id, value)
                ),
                "source_listing_id": prepared["registration_number"],
                "status": prepared["status"].str.lower(),
                "longitude": prepared["longitude"],
                "latitude": prepared["latitude"],
                "attributes_json": prepared.apply(
                    lambda row: json_dumps(
                        {
                            "street_address": row.get("street_address"),
                            "borough": row.get("borough"),
                            "zipcode": row.get("zipcode"),
                            "bin": row.get("bin"),
                            "registration_expiration_date": row.get("expiration_date"),
                            "listing_count": row.get("listing_count"),
                            "booking_service_count": row.get("booking_service_count"),
                            "privacy_note": "unit and listing identifiers omitted from standardized layer",
                        }
                    ),
                    axis=1,
                ),
                "source_id": source_id,
                "snapshot_id": snapshot.snapshot_id,
                "retrieved_at": snapshot.retrieved_at,
                "valid_from": None,
                "valid_to": expiration,
                "geography_type": "building",
                "geography_id": prepared["bin"],
                "measure_type": "administrative_record",
                "confidence": prepared["longitude"].notna().map({True: 0.9, False: 0.7}),
                "method_version": "0.1.0",
            }
        )
        conn.execute("DELETE FROM short_term_rental WHERE source_id=?", [source_id])
        conn.register("_short_term_rentals", records)
        conn.execute("INSERT INTO short_term_rental BY NAME SELECT * FROM _short_term_rentals")
        conn.unregister("_short_term_rentals")
        register_standardized_records(
            conn,
            records,
            entity_type="short_term_rental",
            entity_id_column="short_term_rental_id",
            source_id=source_id,
            snapshot_id=snapshot.snapshot_id,
            source_record_id_column="source_listing_id",
        )
        records.to_parquet(standardized_path(source_id, vintage, "registrations_building_level.parquet"), index=False)
        registered = int(records["status"].eq("registered").sum())
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=2,
            downloaded_bytes=page.byte_size + snapshot.byte_size,
            inserted_records=len(records),
            warnings=[
                "Latest official publication found is dated 2026-01-07.",
                "Unit numbers, booking-service names, and listing identifiers were excluded from the standardized layer.",
                "A registration does not prove every associated stay complies with occupancy rules.",
            ],
        )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "registrations": len(records),
            "registered_status": registered,
            "registrations_with_building_point": int(records["longitude"].notna().sum()),
            "source_as_of": vintage,
            "raw_bytes": page.byte_size + snapshot.byte_size,
        }
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise
