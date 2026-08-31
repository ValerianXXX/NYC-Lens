from __future__ import annotations

import json
import os
import time
from typing import Any

import duckdb
import httpx

from ..config import get_source, geographies_config
from ..db import begin_run, finish_run
from ..provenance import save_bytes_snapshot
from ..util import json_dumps
from ..ingest.common import register_table_lineage


COUNTY_TO_BOROUGH = {
    "36005": "Bronx",
    "36047": "Brooklyn",
    "36061": "Manhattan",
    "36081": "Queens",
    "36085": "Staten Island",
}


def _regional_fields(county_geoid: str | None) -> dict[str, str | None]:
    regional = geographies_config()["greater_nyc"]
    county_definition = regional["counties"].get(str(county_geoid)) if county_geoid else None
    if not county_definition:
        return {
            "state_fips": str(county_geoid)[:2] if county_geoid else None,
            "state_abbr": None,
            "county_geoid": county_geoid,
            "county_name": None,
            "region_code": None,
            "region_name": None,
            "subregion": None,
        }
    return {
        "state_fips": str(county_geoid)[:2],
        "state_abbr": county_definition["state"],
        "county_geoid": str(county_geoid),
        "county_name": county_definition["name"],
        "region_code": f"CSA{regional['csa_code']}",
        "region_name": regional["name"],
        "subregion": county_definition["subregion"],
    }


def _stable_address_id(address: str, longitude: float, latitude: float) -> str:
    import hashlib

    payload = f"{address}|{longitude:.8f}|{latitude:.8f}".encode("utf-8")
    return f"addr_{hashlib.sha256(payload).hexdigest()[:24]}"


def record_geoservice_status(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "nyc_geoservice"
    key = os.getenv("NYC_LENS_GEOSERVICE_KEY")
    run_id = begin_run(conn, source_id, "geocoder availability check")
    if not key:
        warning = "NYC Geoservice requires a subscription key; NYC_LENS_GEOSERVICE_KEY is not set."
        finish_run(conn, run_id, "BLOCKED_CREDENTIALS", warnings=[warning])
        return {"run_id": run_id, "status": "BLOCKED_CREDENTIALS", "warning": warning}
    finish_run(
        conn,
        run_id,
        "SUCCEEDED_WITH_WARNINGS",
        warnings=["Credential present; the current MVP still uses Census Geocoder for its reproducible public samples."],
    )
    return {"run_id": run_id, "status": "SUCCEEDED_WITH_WARNINGS"}


def geocode_address(
    conn: duckdb.DuckDBPyConnection,
    address: str,
    *,
    use_cache: bool = True,
    client: httpx.Client | None = None,
    max_attempts: int = 5,
) -> dict[str, Any]:
    if use_cache:
        row = conn.execute(
            """
            SELECT address_id, original_input, standardized_address, borough,
                   state_fips, state_abbr, county_geoid, county_name,
                   region_code, region_name, subregion, zipcode,
                   longitude, latitude, bbl, bin, census_tract, census_block,
                   geocoder, match_status, confidence, snapshot_id
            FROM address WHERE lower(original_input)=lower(?) AND match_status='MATCH'
            ORDER BY retrieved_at DESC LIMIT 1
            """,
            [address],
        ).fetchone()
        if row:
            keys = [
                "address_id", "original_input", "standardized_address", "borough",
                "state_fips", "state_abbr", "county_geoid", "county_name",
                "region_code", "region_name", "subregion", "zipcode",
                "longitude", "latitude", "bbl", "bin", "census_tract", "census_block",
                "geocoder", "match_status", "confidence", "snapshot_id",
            ]
            cached = dict(zip(keys, row))
            inferred_county = cached.get("county_geoid") or str(cached.get("census_tract") or cached.get("census_block") or "")[:5]
            if inferred_county:
                inferred = _regional_fields(inferred_county)
                for key, value in inferred.items():
                    cached[key] = cached.get(key) or value
            return cached
    source_id = "census_geocoder"
    run_id = begin_run(conn, source_id, "geocode")
    source = get_source(source_id)
    params = {
        "address": address,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "format": "json",
    }
    try:
        owned_client = client is None
        request_client = client or httpx.Client(
            follow_redirects=True,
            timeout=60,
            headers={"User-Agent": "NYC-Lens/0.1 local-research"},
            limits=httpx.Limits(max_keepalive_connections=2, max_connections=4),
        )
        try:
            response = None
            for attempt in range(max_attempts):
                try:
                    candidate = request_client.get(source["api_url"], params=params)
                    if candidate.status_code not in {429, 500, 502, 503, 504}:
                        response = candidate
                        break
                    if attempt == max_attempts - 1:
                        response = candidate
                        break
                except httpx.TransportError:
                    if attempt == max_attempts - 1:
                        raise
                time.sleep(min(0.5 * (2**attempt), 8.0))
            if response is None:  # defensive: max_attempts is expected to be positive
                raise RuntimeError("Census Geocoder request did not produce a response")
        finally:
            if owned_client:
                request_client.close()
        response.raise_for_status()
        snapshot = save_bytes_snapshot(
            conn,
            source_id=source_id,
            vintage="current",
            filename=f"geocode_{abs(hash(address))}.json",
            content=response.content,
            source_url=str(response.url),
            run_id=run_id,
            content_type="application/json",
            query=params,
            http_status=response.status_code,
        )
        payload = response.json()
        matches = payload.get("result", {}).get("addressMatches", [])
        if not matches:
            finish_run(conn, run_id, "SUCCEEDED_WITH_WARNINGS", request_count=1, downloaded_bytes=snapshot.byte_size, warnings=["No address match returned."])
            raise ValueError(f"No official Census Geocoder match for: {address}")
        match = matches[0]
        coordinates = match["coordinates"]
        longitude = float(coordinates["x"])
        latitude = float(coordinates["y"])
        regional = geographies_config()["greater_nyc"]
        bbox = regional["bbox"]
        if not (bbox["west"] <= longitude <= bbox["east"] and bbox["south"] <= latitude <= bbox["north"]):
            raise ValueError(f"Matched coordinate is outside the configured Greater NYC bounds: {longitude}, {latitude}")
        geographies = match.get("geographies", {})
        county = (geographies.get("Counties") or [{}])[0]
        block = (geographies.get("2020 Census Blocks") or [{}])[0]
        tract = (geographies.get("Census Tracts") or [{}])[0]
        county_geoid = county.get("GEOID")
        regional_fields = _regional_fields(county_geoid)
        if regional_fields["region_code"] is None:
            raise ValueError(f"Matched address county {county_geoid or 'unknown'} is outside official CSA 408 coverage.")
        borough = COUNTY_TO_BOROUGH.get(county_geoid)
        standardized = match.get("matchedAddress")
        zipcode = standardized.rsplit(",", 1)[-1].strip() if standardized and "," in standardized else None
        address_id = _stable_address_id(address, longitude, latitude)
        record = {
            "address_id": address_id,
            "original_input": address,
            "standardized_address": standardized,
            "borough": borough,
            **regional_fields,
            "zipcode": zipcode,
            "longitude": longitude,
            "latitude": latitude,
            "bbl": None,
            "bin": None,
            "census_tract": tract.get("GEOID"),
            "census_block": block.get("GEOID"),
            "geocoder": "U.S. Census Geocoder",
            "match_status": "MATCH",
            "confidence": 0.95,
            "raw_response_json": json_dumps(match),
            "source_id": source_id,
            "snapshot_id": snapshot.snapshot_id,
            "retrieved_at": snapshot.retrieved_at,
            "valid_from": None,
            "valid_to": None,
            "geography_type": "address",
            "geography_id": block.get("GEOID"),
            "measure_type": "administrative_record",
            "method_version": "0.2.0",
        }
        conn.execute("DELETE FROM address WHERE address_id=?", [address_id])
        conn.execute(
            "INSERT INTO address BY NAME SELECT "
            + ",".join(f"? AS {column}" for column in record),
            list(record.values()),
        )
        register_table_lineage(
            conn,
            table_name="address",
            entity_type="address",
            id_column="address_id",
            source_id=source_id,
            snapshot_id=snapshot.snapshot_id,
        )
        finish_run(conn, run_id, "SUCCEEDED", request_count=1, downloaded_bytes=snapshot.byte_size, inserted_records=1)
        return record
    except Exception as exc:
        existing = conn.execute("SELECT status FROM ingestion_run WHERE run_id=?", [run_id]).fetchone()
        if existing and existing[0] == "INDETERMINATE":
            finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise
