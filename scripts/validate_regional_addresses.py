from __future__ import annotations

import json

from shapely import from_wkt
from shapely.geometry import Point

from nyc_lens.config import test_sites_config
from nyc_lens.db import db_session, initialize_database
from nyc_lens.geocoding.service import geocode_address


def main() -> None:
    initialize_database()
    results = []
    failures = []
    with db_session() as conn:
        for site in test_sites_config().get("regional_sites", []):
            try:
                record = geocode_address(conn, site["address"])
                boundary_row = conn.execute(
                    """
                    SELECT geom_wkt FROM geo_boundary
                    WHERE source_id='tiger_county_2024'
                      AND geography_type='county_equivalent_2024'
                      AND geography_id=?
                    LIMIT 1
                    """,
                    [site["county_geoid"]],
                ).fetchone()
                inside = bool(
                    boundary_row
                    and from_wkt(boundary_row[0]).covers(
                        Point(float(record["longitude"]), float(record["latitude"]))
                    )
                )
                passed = (
                    record.get("county_geoid") == site["county_geoid"]
                    and record.get("state_abbr") == site["state_abbr"]
                    and record.get("region_code") == "CSA408"
                    and inside
                )
                result = {
                    "id": site["id"],
                    "input": site["address"],
                    "standardized_address": record.get("standardized_address"),
                    "state_abbr": record.get("state_abbr"),
                    "county_geoid": record.get("county_geoid"),
                    "region_code": record.get("region_code"),
                    "inside_official_county_boundary": inside,
                    "passed": passed,
                }
                results.append(result)
                if not passed:
                    failures.append(result)
            except Exception as exc:
                failure = {
                    "id": site["id"],
                    "input": site["address"],
                    "passed": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                results.append(failure)
                failures.append(failure)
    print(json.dumps({"total": len(results), "passed": len(results) - len(failures), "results": results}, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
