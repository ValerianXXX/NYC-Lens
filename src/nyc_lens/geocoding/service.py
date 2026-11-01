from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import duckdb
import httpx

from ..config import get_source, geographies_config
from ..db import begin_run, finish_run
from ..provenance import save_bytes_snapshot
from ..util import json_dumps, sha256_file
from ..ingest.common import register_table_lineage


COUNTY_TO_BOROUGH = {
    "36005": "Bronx",
    "36047": "Brooklyn",
    "36061": "Manhattan",
    "36081": "Queens",
    "36085": "Staten Island",
}

UNIQUE_MATCH_METHOD = "census-unique-match-v1"


def _unique_match(payload: Any, address: str) -> dict[str, Any]:
    result = payload.get("result") if isinstance(payload, dict) else None
    matches = result.get("addressMatches") if isinstance(result, dict) else None
    if not isinstance(matches, list) or any(not isinstance(match, dict) for match in matches):
        raise ValueError("ADDRESS_MATCH_RESPONSE_INVALID: Census addressMatches must be a list of matches")
    if not matches:
        raise ValueError(f"No official Census Geocoder match for: {address}")
    if len(matches) != 1:
        raise ValueError(
            "ADDRESS_MATCH_AMBIGUOUS: Multiple locations matched. Add the street number, city and ZIP code, "
            "then confirm the corrected address / 地址匹配到多个地点，请补全门牌、城市和邮编后重新确认。"
        )
    return matches[0]


def _legacy_cache_is_unique(conn: duckdb.DuckDBPyConnection, cached: dict[str, Any]) -> bool:
    """Old releases cached the first match. Recheck retained full response bytes."""
    row = conn.execute(
        "SELECT raw_path, sha256 FROM dataset_snapshot WHERE snapshot_id=? AND source_id='census_geocoder'",
        [cached.get("snapshot_id")],
    ).fetchone()
    if not row or not row[0] or not row[1]:
        return False
    try:
        path = Path(row[0])
        if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024 or sha256_file(path) != row[1]:
            return False
        payload = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return False
    # A known ambiguous old response must ask for correction, not silently
    # fetch until the service happens to return a different answer.
    match = _unique_match(payload, cached["original_input"])
    coordinates = match.get("coordinates") or {}
    return (coordinates.get("x") == cached["longitude"]
            and coordinates.get("y") == cached["latitude"]
            and match.get("matchedAddress") == cached["standardized_address"])


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
                   geocoder, match_status, confidence, snapshot_id, source_id, method_version
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
                "geocoder", "match_status", "confidence", "snapshot_id", "source_id", "method_version",
            ]
            cached = dict(zip(keys, row))
            inferred_county = cached.get("county_geoid") or str(cached.get("census_tract") or cached.get("census_block") or "")[:5]
            if inferred_county:
                inferred = _regional_fields(inferred_county)
                for key, value in inferred.items():
                    cached[key] = cached.get(key) or value
            if (cached.get("source_id") != "census_geocoder"
                    or cached.get("method_version") == UNIQUE_MATCH_METHOD
                    or _legacy_cache_is_unique(conn, cached)):
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
        match = _unique_match(payload, address)
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
            "method_version": UNIQUE_MATCH_METHOD,
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
