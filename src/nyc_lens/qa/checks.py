from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Callable

import duckdb
from shapely import from_wkt

from ..adapters.commercial_mobility import inspect_commercial_mobility_adapters
from ..adapters.commercial_real_estate import inspect_commercial_real_estate_adapters
from ..paths import database_path, project_home
from ..scoring import scoring_status
from ..storage import EXTERNAL_TABLE_SPECS, MAX_SINGLE_FILE_BYTES
from ..util import json_dumps, new_id, sha256_file, utc_now


def run_qa(conn: duckdb.DuckDBPyConnection, *, verify_hashes: bool = True) -> dict[str, Any]:
    conn.execute("SET enable_progress_bar=false")
    results: list[dict[str, Any]] = []

    def add(check_id: str, scope: str, status: str, observed: Any, expected: Any, details: Any = None) -> None:
        row = {
            "qa_result_id": new_id("qa"),
            "run_id": None,
            "check_id": check_id,
            "scope": scope,
            "status": status,
            "observed_value": str(observed),
            "expected_value": str(expected),
            "details_json": json_dumps(details or {}),
            "checked_at": utc_now(),
        }
        results.append(row)
        conn.execute(
            "INSERT INTO qa_result BY NAME SELECT " + ",".join(f"? AS {column}" for column in row),
            list(row.values()),
        )
        print(f"[qa {len(results):02d}] {check_id}: {status}", file=sys.stderr, flush=True)

    missing_license = conn.execute(
        "SELECT count(*) FROM source_registry WHERE license IS NULL OR trim(license)=''"
    ).fetchone()[0]
    add("source_license_present", "source_registry", "PASS" if missing_license == 0 else "FAIL", missing_license, 0)

    latest_validation = conn.execute(
        """
        SELECT r.run_id
        FROM ingestion_run r
        WHERE r.source_id='census_geocoder'
          AND r.command='public address validation'
          AND r.status IN ('SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS')
        ORDER BY r.completed_at DESC NULLS LAST
        LIMIT 1
        """
    ).fetchone()
    if latest_validation:
        validation_batch_id = latest_validation[0]
        (
            address_total,
            address_successes,
            address_matches,
            borough_matches,
            average_coordinate_error,
            maximum_coordinate_error,
        ) = conn.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE success),
                   count(*) FILTER (WHERE matched),
                   count(*) FILTER (WHERE borough_match),
                   avg(coordinate_error_m),
                   max(coordinate_error_m)
            FROM address_validation_case
            WHERE validation_batch_id=?
            """,
            [validation_batch_id],
        ).fetchone()
        address_rate = (address_successes / address_total) if address_total else None
        details = {
            "validation_batch_id": validation_batch_id,
            "sample_size": address_total,
            "sampling": "deterministic stratified public DOHMH establishments across all five boroughs",
            "match_rate": (address_matches / address_total) if address_total else None,
            "borough_match_rate": (borough_matches / address_total) if address_total else None,
            "average_coordinate_error_m": average_coordinate_error,
            "maximum_coordinate_error_m": maximum_coordinate_error,
            "target_is_not_proven_citywide_by_this_sample": True,
        }
        add(
            "address_standardization_observed_rate",
            "address_validation_case",
            "PASS" if address_rate is not None and address_rate >= 0.98 else "FAIL",
            f"{address_rate:.4f}" if address_rate is not None else "not_run",
            ">=0.98",
            details,
        )
        add(
            "address_validation_sample_size",
            "address_validation_case",
            "PASS" if address_total >= 100 else "WARN",
            address_total,
            ">=100",
            {"validation_batch_id": validation_batch_id},
        )
    else:
        address_total, address_matches = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE match_status='MATCH') FROM address"
        ).fetchone()
        address_rate = (address_matches / address_total) if address_total else None
        add(
            "address_standardization_observed_rate",
            "address",
            "WARN",
            f"{address_rate:.4f}" if address_rate is not None else "not_run",
            ">=0.98 from a five-borough public validation sample",
            {"sample_size": address_total, "note": "No completed stratified validation batch exists yet."},
        )
        add(
            "address_validation_sample_size",
            "address",
            "WARN",
            0,
            ">=100",
            {"note": "Run validate-addresses to create the required five-borough public validation sample."},
        )

    snapshot_rows = conn.execute(
        "SELECT snapshot_id, raw_path, sha256, byte_size FROM dataset_snapshot WHERE status='SUCCEEDED'"
    ).fetchall()
    missing_files = 0
    bad_hashes = 0
    for snapshot_id, raw_path, digest, byte_size in snapshot_rows:
        path = Path(raw_path)
        if not path.exists() or path.stat().st_size != byte_size:
            missing_files += 1
        elif verify_hashes and sha256_file(path) != digest:
            bad_hashes += 1
    add("raw_files_present_and_sized", "dataset_snapshot", "PASS" if missing_files == 0 else "FAIL", missing_files, 0)
    if verify_hashes:
        add("raw_sha256_matches", "dataset_snapshot", "PASS" if bad_hashes == 0 else "FAIL", bad_hashes, 0)

    external_specs = {
        table_name: spec.id_column
        for table_name, spec in EXTERNAL_TABLE_SPECS.items()
    }
    external_catalog = {
        row[0]: {
            "row_count": int(row[1]),
            "byte_size": int(row[2]),
            "file_count": int(row[3]),
            "storage_root": row[4],
            "manifest_sha256": row[5],
        }
        for row in conn.execute(
            """
            SELECT table_name, row_count, byte_size, file_count, storage_root, manifest_sha256
            FROM external_table_catalog
            """
        ).fetchall()
    }
    external_errors: list[dict[str, Any]] = []
    external_details: dict[str, Any] = {}
    for table_name, id_column in external_specs.items():
        catalog = external_catalog.get(table_name)
        if catalog is None:
            external_errors.append({"table": table_name, "error": "missing external catalog"})
            continue
        view_count, approximate_distinct_ids = conn.execute(
            f'SELECT count(*), approx_count_distinct("{id_column}") FROM "{table_name}"'
        ).fetchone()
        partition_rows, partition_bytes, partition_files = conn.execute(
            """
            SELECT coalesce(sum(row_count),0), coalesce(sum(byte_size),0), count(*)
            FROM external_partition_catalog WHERE table_name=?
            """,
            [table_name],
        ).fetchone()
        table_errors: list[str] = []
        if int(view_count) != catalog["row_count"]:
            table_errors.append("view row count does not match catalog")
        if (
            int(partition_rows) != catalog["row_count"]
            or int(partition_bytes) != catalog["byte_size"]
            or int(partition_files) != catalog["file_count"]
        ):
            table_errors.append("partition totals do not match table catalog")
        missing_partitions = 0
        bad_partition_hashes = 0
        oversized_partitions = 0
        for relative_path, byte_size, digest in conn.execute(
            """
            SELECT relative_path, byte_size, sha256
            FROM external_partition_catalog WHERE table_name=?
            """,
            [table_name],
        ).fetchall():
            path = project_home() / str(relative_path)
            if not path.is_file() or path.stat().st_size != int(byte_size):
                missing_partitions += 1
                continue
            if path.stat().st_size >= MAX_SINGLE_FILE_BYTES:
                oversized_partitions += 1
            if verify_hashes and sha256_file(path) != digest:
                bad_partition_hashes += 1
        manifest = project_home() / catalog["storage_root"] / "_manifest.json"
        manifest_ok = (
            manifest.is_file()
            and (
                not verify_hashes
                or sha256_file(manifest) == catalog["manifest_sha256"]
            )
        )
        if missing_partitions:
            table_errors.append(f"missing or wrongly sized partitions: {missing_partitions}")
        if bad_partition_hashes:
            table_errors.append(f"partition SHA-256 mismatches: {bad_partition_hashes}")
        if oversized_partitions:
            table_errors.append(f"partitions at or above 5 GB: {oversized_partitions}")
        if not manifest_ok:
            table_errors.append("missing or mismatched table manifest")
        if table_errors:
            external_errors.append({"table": table_name, "errors": table_errors})
        external_details[table_name] = {
            **catalog,
            "view_rows": int(view_count),
            "approximate_distinct_ids_diagnostic": int(approximate_distinct_ids),
            "partition_rows": int(partition_rows),
            "partition_files": int(partition_files),
            "manifest_ok": manifest_ok,
        }
    add(
        "external_storage_a_integrity",
        "+".join(external_specs),
        "PASS" if not external_errors and len(external_catalog) == len(external_specs) else "FAIL",
        len(external_errors),
        0,
        {
            "tables": external_details,
            "errors": external_errors,
            "hash_verification": verify_hashes,
            "file_size_limit_bytes": MAX_SINGLE_FILE_BYTES,
        },
    )

    catalog_bytes = database_path().stat().st_size
    add(
        "catalog_single_file_below_5gb",
        "nyc_lens.duckdb",
        "PASS" if catalog_bytes < MAX_SINGLE_FILE_BYTES else "FAIL",
        catalog_bytes,
        f"<{MAX_SINGLE_FILE_BYTES}",
        {"threshold_unit": "decimal bytes", "threshold_label": "5 GB"},
    )

    duplicate_standardized_ids = int(
        conn.execute(
            """
            SELECT count(*) FROM (
                SELECT standardized_record_id
                FROM standardized_record
                GROUP BY standardized_record_id
                HAVING count(*) > 1
            ) duplicates
            """
        ).fetchone()[0]
    )
    add(
        "standardized_record_id_unique",
        "standardized_record",
        "PASS" if duplicate_standardized_ids == 0 else "FAIL",
        duplicate_standardized_ids,
        0,
        {
            "enforcement": "deterministic ids, ingestion anti-joins, and full-table QA",
            "physical_primary_key_omitted": True,
            "reason": "avoid a multi-gigabyte DuckDB ART index on lineage-only rows",
        },
    )

    duplicate_workforce_ids = int(
        conn.execute(
            """
            SELECT count(*) FROM (
                SELECT workforce_estimate_id
                FROM workforce_estimate
                GROUP BY workforce_estimate_id
                HAVING count(*) > 1
            ) duplicates
            """
        ).fetchone()[0]
    )
    add(
        "workforce_estimate_id_unique",
        "workforce_estimate",
        "PASS" if duplicate_workforce_ids == 0 else "FAIL",
        duplicate_workforce_ids,
        0,
        {
            "enforcement": "LODES deterministic-id validation, source replacement transaction, and full-table QA",
            "physical_primary_key_omitted": True,
            "reason": "keep the local DuckDB catalogue below the 5 GB single-file cap",
        },
    )

    for logical_table, logical_key, enforcement in (
        ("parcel", "parcel_id", "staged MapPLUTO partition validation and source replacement"),
        ("census_estimate", "census_estimate_id", "deterministic geography-variable ids and source refresh"),
        ("inspection", "inspection_id", "source-row-derived ids and source replacement"),
        ("transaction_case", "transaction_case_id", "ranked deterministic public-record candidates"),
    ):
        duplicates = int(
            conn.execute(
                f"""
                SELECT count(*) FROM (
                    SELECT {logical_key}
                    FROM {logical_table}
                    GROUP BY {logical_key}
                    HAVING count(*) > 1
                ) duplicate_keys
                """
            ).fetchone()[0]
        )
        add(
            f"{logical_table}_logical_key_unique",
            logical_table,
            "PASS" if duplicates == 0 else "FAIL",
            duplicates,
            0,
            {
                "logical_key": logical_key,
                "enforcement": f"{enforcement}, plus full-table QA",
                "physical_primary_key_omitted": True,
                "reason": "keep the local catalogue safely below the strict 5 GB single-file cap",
            },
        )

    acs_total = conn.execute(
        "SELECT count(*) FROM census_estimate WHERE source_id='acs_2024_5yr'"
    ).fetchone()[0]
    acs_missing_moe = conn.execute(
        """
        SELECT count(*) FROM census_estimate
        WHERE source_id='acs_2024_5yr' AND estimate IS NOT NULL AND margin_of_error IS NULL
        """
    ).fetchone()[0]
    acs_censored_moe = conn.execute(
        """
        SELECT count(*) FROM census_estimate
        WHERE source_id='acs_2024_5yr'
          AND estimate IS NOT NULL AND margin_of_error IS NULL
          AND variable='B19013_001' AND estimate IN (2499, 250001)
        """
    ).fetchone()[0]
    unexpected_acs_missing_moe = acs_missing_moe - acs_censored_moe
    add(
        "acs_estimate_moe_pair",
        "census_estimate",
        "PASS" if acs_total > 0 and unexpected_acs_missing_moe == 0 else ("NOT_RUN" if acs_total == 0 else "FAIL"),
        unexpected_acs_missing_moe,
        0,
        {"rows": acs_total, "source_suppressed_income_moe_rows": acs_censored_moe},
    )
    if acs_censored_moe:
        add(
            "acs_source_suppressed_income_moe",
            "census_estimate",
            "WARN",
            acs_censored_moe,
            0,
            {"values": [2499, 250001], "note": "Census top/bottom-coded income estimates publish an unavailable MOE sentinel."},
        )

    negative_population = conn.execute(
        "SELECT count(*) FROM census_estimate WHERE variable='B01003_001' AND estimate < 0"
    ).fetchone()[0]
    add("nonnegative_population", "census_estimate", "PASS" if negative_population == 0 else "FAIL", negative_population, 0)

    supported_bounds = conn.execute(
        """
        SELECT min(min_longitude), min(min_latitude), max(max_longitude), max(max_latitude)
        FROM geo_boundary
        WHERE source_id='tiger_county_2024'
          AND geography_type='county_equivalent_2024'
          AND geography_id IN (
              SELECT geography_id FROM region_member WHERE region_code='CSA408'
          )
        """
    ).fetchone()
    coordinate_failures = conn.execute(
        """
        SELECT count(*) FROM (
            SELECT longitude, latitude FROM business_location
            UNION ALL SELECT longitude, latitude FROM poi
            UNION ALL SELECT longitude, latitude FROM transit_stop
            UNION ALL SELECT longitude, latitude FROM hotel
            UNION ALL SELECT longitude, latitude FROM short_term_rental
            UNION ALL SELECT longitude, latitude FROM traffic_observation
            UNION ALL SELECT longitude, latitude FROM mobility_proxy
        ) points
        WHERE longitude IS NOT NULL AND latitude IS NOT NULL
          AND NOT (longitude BETWEEN ? AND ? AND latitude BETWEEN ? AND ?)
        """,
        [supported_bounds[0], supported_bounds[2], supported_bounds[1], supported_bounds[3]],
    ).fetchone()[0]
    add(
        "coordinates_inside_supported_region_bbox",
        "point_entities",
        "PASS" if coordinate_failures == 0 else "FAIL",
        coordinate_failures,
        0,
        {"official_csa408_county_bbox": supported_bounds},
    )

    parcel_rows, parcel_bad_bounds = conn.execute(
        """
        SELECT count(*), count(*) FILTER (
            WHERE geom_wkt IS NULL OR min_longitude IS NULL OR min_latitude IS NULL
               OR max_longitude IS NULL OR max_latitude IS NULL
               OR min_longitude > max_longitude OR min_latitude > max_latitude
        )
        FROM parcel WHERE source_id='mappluto_26v2'
        """
    ).fetchone()
    add(
        "mappluto_citywide_coverage",
        "parcel",
        "PASS" if parcel_rows >= 800_000 and parcel_bad_bounds == 0 else "FAIL",
        parcel_rows,
        ">=800000 with valid geometry bounds",
        {"invalid_or_missing_geometry_bounds": parcel_bad_bounds},
    )
    building_rows, building_bad_bounds = conn.execute(
        """
        SELECT count(*), count(*) FILTER (
            WHERE geom_wkt IS NULL OR min_longitude IS NULL OR min_latitude IS NULL
               OR max_longitude IS NULL OR max_latitude IS NULL
               OR min_longitude > max_longitude OR min_latitude > max_latitude
        )
        FROM building WHERE source_id='nyc_building_footprints'
        """
    ).fetchone()
    add(
        "building_footprints_citywide_coverage",
        "building",
        "PASS" if building_rows >= 1_000_000 and building_bad_bounds == 0 else "FAIL",
        building_rows,
        ">=1000000 with valid geometry bounds",
        {"invalid_or_missing_geometry_bounds": building_bad_bounds},
    )
    geography_counts = dict(
        conn.execute(
            "SELECT geography_type, count(*) FROM geo_boundary GROUP BY geography_type"
        ).fetchall()
    )
    required_geographies = {
        "census_block": 35_000,
        "block_group": 6_000,
        "tract": 2_000,
        "nta": 250,
        "community_district": 59,
        "zcta": 100,
        "borough": 5,
        "county": 5,
    }
    geography_failures = {
        key: {"observed": geography_counts.get(key, 0), "minimum": minimum}
        for key, minimum in required_geographies.items()
        if geography_counts.get(key, 0) < minimum
    }
    add(
        "required_nyc_geographies",
        "geo_boundary",
        "PASS" if not geography_failures else "FAIL",
        len(geography_failures),
        0,
        {"counts": geography_counts, "failures": geography_failures},
    )
    h3_cells = conn.execute(
        "SELECT count(*) FROM geo_cell WHERE source_id='derived_h3_grid' AND h3_resolution=10"
    ).fetchone()[0]
    add("h3_r10_citywide_grid", "geo_cell", "PASS" if h3_cells >= 40_000 else "FAIL", h3_cells, ">=40000")

    geometries = conn.execute(
        "SELECT boundary_id, geom_wkt FROM geo_boundary UNION ALL SELECT catchment_id, geom_wkt FROM catchment"
    ).fetchall()
    invalid = []
    for geometry_id, wkt in geometries:
        try:
            geometry = from_wkt(wkt)
            if geometry.is_empty or not geometry.is_valid:
                invalid.append(geometry_id)
        except Exception:
            invalid.append(geometry_id)
    add("geometry_validity", "geo_boundary+catchment", "PASS" if not invalid and geometries else ("NOT_RUN" if not geometries else "FAIL"), len(invalid), 0, {"sample_ids": invalid[:20], "checked": len(geometries)})

    duplicate_poi = conn.execute(
        """
        SELECT count(*) FROM (
            SELECT source_id, lower(trim(coalesce(name,''))), round(longitude,5), round(latitude,5),
                   coalesce(basic_category,''), count(*)
            FROM poi WHERE longitude IS NOT NULL AND latitude IS NOT NULL
            GROUP BY 1,2,3,4,5 HAVING count(*) > 1
        )
        """
    ).fetchone()[0]
    add("poi_within_source_basic_dedupe", "poi", "PASS" if duplicate_poi == 0 else "FAIL", duplicate_poi, 0)
    cross_source_poi = conn.execute(
        """
        SELECT count(*) FROM (
            SELECT lower(trim(coalesce(name,''))), round(longitude,5), round(latitude,5),
                   coalesce(basic_category,''), count(DISTINCT source_id)
            FROM poi WHERE longitude IS NOT NULL AND latitude IS NOT NULL
            GROUP BY 1,2,3,4 HAVING count(DISTINCT source_id) > 1
        )
        """
    ).fetchone()[0]
    add(
        "poi_cross_source_matches_retained",
        "poi",
        "WARN" if cross_source_poi else "PASS",
        cross_source_poi,
        0,
        {
            "note": "Cross-source matches are retained with separate lineage; site metrics select explicit sources and do not sum this table indiscriminately."
        },
    )

    duplicate_business = conn.execute(
        "SELECT count(*) FROM (SELECT dedupe_key FROM business_location WHERE dedupe_key IS NOT NULL GROUP BY dedupe_key HAVING count(*) > 1)"
    ).fetchone()[0]
    add("business_dedupe_key_unique", "business_location", "PASS" if duplicate_business == 0 else "WARN", duplicate_business, 0)

    lodes_period_failures = conn.execute(
        """
        SELECT count(*) FROM workforce_estimate
        WHERE source_id='lodes_2023'
          AND (valid_from <> DATE '2023-01-01' OR valid_to <> DATE '2023-12-31')
        """
    ).fetchone()[0]
    add("lodes_year_consistency", "workforce_estimate", "PASS" if lodes_period_failures == 0 else "FAIL", lodes_period_failures, 0)

    region_members, region_member_counties, region_states = conn.execute(
        """
        SELECT count(*), count(DISTINCT geography_id), count(DISTINCT state_fips)
        FROM region_member WHERE region_code='CSA408'
        """
    ).fetchone()
    region_definitions = conn.execute(
        "SELECT count(*) FROM region_definition WHERE region_code='CSA408'"
    ).fetchone()[0]
    add(
        "greater_nyc_official_csa408_scope",
        "region_definition+region_member",
        "PASS"
        if region_definitions == 1 and region_members == 30 and region_member_counties == 30 and region_states == 4
        else "FAIL",
        region_members,
        "1 official definition, 30 unique county equivalents, 4 states",
        {
            "region_definitions": region_definitions,
            "unique_counties": region_member_counties,
            "states": region_states,
        },
    )

    regional_geography = dict(
        conn.execute(
            """
            SELECT geography_type, count(*) FROM geo_boundary
            WHERE source_id IN ('tiger_county_2024','tiger_tract_2024','tiger_bg_2024')
            GROUP BY geography_type
            """
        ).fetchall()
    )
    regional_bad_bounds = conn.execute(
        """
        SELECT count(*) FROM geo_boundary
        WHERE source_id IN ('tiger_county_2024','tiger_tract_2024','tiger_bg_2024')
          AND (min_longitude IS NULL OR min_latitude IS NULL
               OR max_longitude IS NULL OR max_latitude IS NULL
               OR min_longitude > max_longitude OR min_latitude > max_latitude)
        """
    ).fetchone()[0]
    regional_geography_ok = (
        regional_geography.get("county_equivalent_2024", 0) == 30
        and regional_geography.get("tract_2024", 0) >= 5_400
        and regional_geography.get("block_group", 0) >= 16_500
        and regional_bad_bounds == 0
    )
    add(
        "greater_nyc_tiger_geography_coverage",
        "geo_boundary",
        "PASS" if regional_geography_ok else "FAIL",
        regional_geography,
        "30 counties, >=5400 tracts, >=16500 block groups, valid bounds",
        {"invalid_bounds": regional_bad_bounds},
    )

    regional_lodes_rows, regional_lodes_counties, regional_lodes_states = conn.execute(
        """
        SELECT count(*), count(DISTINCT current_county_geoid), count(DISTINCT state_abbr)
        FROM workforce_estimate WHERE source_id='lodes_2023'
        """
    ).fetchone()
    add(
        "greater_nyc_lodes_2023_coverage",
        "workforce_estimate",
        "PASS"
        if regional_lodes_rows >= 2_000_000 and regional_lodes_counties == 30 and regional_lodes_states == 4
        else "FAIL",
        regional_lodes_rows,
        ">=2000000 rows, 30 counties, 4 states",
        {"counties": regional_lodes_counties, "states": regional_lodes_states},
    )

    overture_places_rows, overture_places_approx_distinct = conn.execute(
        "SELECT count(*), approx_count_distinct(poi_id) FROM poi WHERE source_id='overture_places'"
    ).fetchone()
    overture_building_rows, overture_building_approx_distinct, overture_building_bad = conn.execute(
        """
        SELECT count(*), approx_count_distinct(building_id), count(*) FILTER (
            WHERE geom_wkt IS NULL OR min_longitude IS NULL OR min_latitude IS NULL
               OR max_longitude IS NULL OR max_latitude IS NULL
        )
        FROM building WHERE source_id='overture_buildings'
        """
    ).fetchone()
    add(
        "greater_nyc_overture_compiled_coverage",
        "poi+building",
        "PASS"
        if (
            overture_places_rows >= 1_000_000
            and overture_building_rows >= 5_500_000
            and overture_building_bad == 0
        )
        else "FAIL",
        overture_places_rows + overture_building_rows,
        ">=1M unique places and >=5.5M unique building geometries",
        {
            "places": overture_places_rows,
            "place_approximate_distinct_ids_diagnostic": overture_places_approx_distinct,
            "buildings": overture_building_rows,
            "building_approximate_distinct_ids_diagnostic": overture_building_approx_distinct,
            "building_invalid_geometry_fields": overture_building_bad,
            "building_is_not_parcel": True,
            "exact_uniqueness_enforced_before_partition_write": True,
        },
    )

    rail_rows, rail_distinct, rail_snapshots = conn.execute(
        """
        SELECT count(*), count(DISTINCT transit_stop_id), count(DISTINCT snapshot_id)
        FROM transit_stop WHERE source_id='regional_mta_rail_gtfs_static'
        """
    ).fetchone()
    add(
        "regional_mta_rail_static_coverage",
        "transit_stop",
        "PASS" if rail_rows >= 225 and rail_rows == rail_distinct and rail_snapshots == 2 else "FAIL",
        rail_rows,
        ">=225 unique LIRR/MNR parent stations from two snapshots",
        {
            "distinct_ids": rail_distinct,
            "feed_snapshots": rail_snapshots,
            "njt_path_cttransit_not_misrepresented_as_present": True,
        },
    )

    fema_rows, fema_distinct, fema_bad_bounds = conn.execute(
        """
        SELECT count(*), count(DISTINCT boundary_id), count(*) FILTER (
            WHERE min_longitude IS NULL OR min_latitude IS NULL
               OR max_longitude IS NULL OR max_latitude IS NULL
        )
        FROM geo_boundary WHERE source_id='fema_nfhl_special_flood_hazard_areas'
        """
    ).fetchone()
    fema_run = conn.execute(
        """
        SELECT status, downloaded_bytes, inserted_records
        FROM ingestion_run WHERE source_id='fema_nfhl_special_flood_hazard_areas'
          AND status IN ('SUCCEEDED','SUCCEEDED_WITH_WARNINGS')
        ORDER BY completed_at DESC LIMIT 1
        """
    ).fetchone()
    add(
        "greater_nyc_fema_sfha_coverage",
        "geo_boundary+ingestion_run",
        "PASS"
        if (
            fema_rows >= 35_000
            and fema_rows == fema_distinct
            and fema_bad_bounds == 0
            and fema_run is not None
            and int(fema_run[1]) < 20_000_000_000
        )
        else "FAIL",
        fema_rows,
        ">=35000 unique bounded SFHA polygons and successful transfer under 20 GiB",
        {
            "distinct_ids": fema_distinct,
            "invalid_bounds": fema_bad_bounds,
            "latest_success_run": fema_run,
            "screening_not_insurance_or_engineering": True,
        },
    )

    cdc_current = conn.execute(
        """
        SELECT count(*), count(DISTINCT geography_id), count(DISTINCT indicator_id),
               count(value), count(DISTINCT substr(geography_id,1,5))
        FROM health_indicator WHERE source_id='cdc_places_2025_tract'
        """
    ).fetchone()
    cdc_fallback = conn.execute(
        """
        SELECT count(*), count(DISTINCT geography_id), count(DISTINCT indicator_id), count(value)
        FROM health_indicator WHERE source_id='cdc_places_2024_tract_fallback'
        """
    ).fetchone()
    cdc_missing_current = conn.execute(
        """
        SELECT count(*) FROM (
            SELECT geography_id FROM health_indicator
            WHERE source_id='cdc_places_2025_tract'
            GROUP BY geography_id HAVING count(value)=0
        )
        """
    ).fetchone()[0]
    cdc_ok = (
        cdc_current[0] >= 27_000
        and cdc_current[1] >= 5_400
        and cdc_current[2] == 5
        and cdc_current[4] == 30
        and cdc_fallback[1] == cdc_missing_current
        and cdc_fallback[2] == 5
        and cdc_fallback[3] >= 100
    )
    add(
        "greater_nyc_cdc_places_health_coverage",
        "health_indicator",
        "PASS" if cdc_ok else "FAIL",
        cdc_current[0] + cdc_fallback[0],
        ">=5400 current tracts / 30 counties / 5 measures, older fallback only for fully missing current tracts",
        {
            "current": cdc_current,
            "fully_missing_current_tracts": cdc_missing_current,
            "fallback": cdc_fallback,
            "individual_assignment_forbidden": True,
        },
    )

    decennial_rows, decennial_blocks, decennial_variables, negative_decennial = conn.execute(
        """
        SELECT count(*), count(DISTINCT geography_id), count(DISTINCT variable),
               count(*) FILTER (WHERE estimate < 0)
        FROM census_estimate
        WHERE source_id='census_decennial_2020_pl'
          AND geography_type='census_block'
        """
    ).fetchone()
    decennial_ok = (
        decennial_rows >= 220_000
        and decennial_blocks >= 37_000
        and decennial_variables == 6
        and negative_decennial == 0
    )
    add(
        "decennial_2020_block_facts",
        "census_estimate",
        "PASS" if decennial_ok else "FAIL",
        decennial_rows,
        ">=220000 rows, >=37000 blocks, 6 variables, no negative counts",
        {
            "blocks": decennial_blocks,
            "variables": decennial_variables,
            "negative_counts": negative_decennial,
        },
    )

    license_rows, license_coordinates = conn.execute(
        """
        SELECT count(*), count(*) FILTER (WHERE longitude IS NOT NULL AND latitude IS NOT NULL)
        FROM business_location WHERE source_id='nysla_active_licenses'
        """
    ).fetchone()
    add(
        "nysla_active_license_coverage",
        "business_location",
        "PASS" if license_rows >= 20_000 and license_coordinates / max(1, license_rows) >= 0.95 else "FAIL",
        license_rows,
        ">=20000 rows and >=95% coordinates",
        {"with_coordinates": license_coordinates, "coordinate_rate": license_coordinates / max(1, license_rows)},
    )
    str_rows, str_coordinates = conn.execute(
        """
        SELECT count(*), count(*) FILTER (WHERE longitude IS NOT NULL AND latitude IS NOT NULL)
        FROM short_term_rental WHERE source_id='nyc_str_registration'
        """
    ).fetchone()
    add(
        "short_term_rental_registration_coverage",
        "short_term_rental",
        "PASS" if str_rows >= 3_000 and str_coordinates / max(1, str_rows) >= 0.9 else "FAIL",
        str_rows,
        ">=3000 rows and >=90% building-point coordinates",
        {"with_coordinates": str_coordinates, "coordinate_rate": str_coordinates / max(1, str_rows)},
    )
    facility_rows, facility_coordinates = conn.execute(
        """
        SELECT count(*), count(*) FILTER (WHERE longitude IS NOT NULL AND latitude IS NOT NULL)
        FROM poi WHERE source_id='nyc_facilities'
        """
    ).fetchone()
    add(
        "facility_database_coverage",
        "poi",
        "PASS" if facility_rows >= 30_000 and facility_coordinates / max(1, facility_rows) >= 0.95 else "FAIL",
        facility_rows,
        ">=30000 deduplicated physical facilities and >=95% coordinates",
        {"with_coordinates": facility_coordinates, "coordinate_rate": facility_coordinates / max(1, facility_rows)},
    )

    traffic_rows, traffic_segments, traffic_bad_values = conn.execute(
        """
        SELECT count(*), count(DISTINCT geography_id),
               count(*) FILTER (WHERE value IS NULL OR value < 0)
        FROM traffic_observation WHERE source_id='nyc_dot_traffic_counts'
        """
    ).fetchone()
    add(
        "dot_vehicle_count_coverage",
        "traffic_observation",
        "PASS" if traffic_rows >= 90_000 and traffic_segments >= 100 and traffic_bad_values == 0 else "FAIL",
        traffic_rows,
        ">=90000 observations, >=100 segments, nonnegative values",
        {"segments": traffic_segments, "invalid_values": traffic_bad_values},
    )
    pedestrian_rows, pedestrian_sites, pedestrian_missing_values, pedestrian_bad_values = conn.execute(
        """
        SELECT count(*), count(DISTINCT geography_id),
               count(*) FILTER (WHERE value IS NULL),
               count(*) FILTER (WHERE value < 0)
        FROM traffic_observation WHERE source_id='nyc_dot_pedestrian_counts'
        """
    ).fetchone()
    add(
        "dot_pedestrian_count_coverage",
        "traffic_observation",
        "PASS" if pedestrian_rows >= 300 and pedestrian_sites >= 100 and pedestrian_missing_values <= 5 and pedestrian_bad_values == 0 else "FAIL",
        pedestrian_rows,
        ">=300 observations, >=100 sites, <=5 source-missing values, no negative values",
        {
            "sites": pedestrian_sites,
            "source_missing_values": pedestrian_missing_values,
            "negative_values": pedestrian_bad_values,
        },
    )
    speed_rows, speed_links, speed_bad_values = conn.execute(
        """
        SELECT count(*), count(DISTINCT geography_id),
               count(*) FILTER (WHERE value IS NULL OR value < 0 OR value > 120)
        FROM traffic_observation WHERE source_id='nyc_dot_traffic_speed_current'
        """
    ).fetchone()
    add(
        "dot_current_speed_coverage",
        "traffic_observation",
        "PASS" if speed_rows >= 100 and speed_links >= 100 and speed_bad_values == 0 else "FAIL",
        speed_rows,
        ">=100 latest monitored links and values in 0..120 mph",
        {"links": speed_links, "invalid_values": speed_bad_values},
    )

    ridership_rows, ridership_complexes, ridership_total, ridership_bad = conn.execute(
        """
        SELECT count(*), count(DISTINCT complex_id), coalesce(sum(ridership),0),
               count(*) FILTER (WHERE ridership < 0 OR day_type NOT IN ('weekday','weekend')
                                      OR hour_of_day NOT BETWEEN 0 AND 23)
        FROM transit_ridership WHERE source_id='mta_hourly_2025'
        """
    ).fetchone()
    add(
        "mta_2025_hourly_ridership_coverage",
        "transit_ridership",
        "PASS"
        if (
            ridership_rows >= 70_000
            and ridership_complexes >= 425
            and ridership_total >= 1_000_000_000
            and ridership_bad == 0
        )
        else "FAIL",
        ridership_rows,
        ">=70000 complex-day-type-hour cells, >=425 complexes, >=1B entries, valid dimensions",
        {
            "complexes": ridership_complexes,
            "annual_entries": ridership_total,
            "invalid_cells": ridership_bad,
        },
    )
    entrance_rows, entrance_ids, entrance_boroughs, entrance_bad_coordinates = conn.execute(
        """
        SELECT count(*), count(DISTINCT transit_stop_id), count(DISTINCT borough),
               count(*) FILTER (
                   WHERE longitude IS NULL OR latitude IS NULL
                      OR longitude NOT BETWEEN -74.25909 AND -73.700272
                      OR latitude NOT BETWEEN 40.477399 AND 40.917577
               )
        FROM transit_stop WHERE source_id='mta_subway_entrances_2024'
        """
    ).fetchone()
    add(
        "mta_subway_entrance_exit_coverage",
        "transit_stop",
        "PASS"
        if entrance_rows >= 2_000 and entrance_rows == entrance_ids and entrance_boroughs == 5 and entrance_bad_coordinates == 0
        else "FAIL",
        entrance_rows,
        ">=2000 unique association rows, five boroughs, valid coordinates",
        {
            "distinct_ids": entrance_ids,
            "boroughs": entrance_boroughs,
            "invalid_coordinates": entrance_bad_coordinates,
        },
    )
    bus_rows, bus_distinct_ids, bus_snapshots, bus_bad_coordinates = conn.execute(
        """
        SELECT count(*), count(DISTINCT transit_stop_id), count(DISTINCT snapshot_id),
               count(*) FILTER (WHERE longitude IS NULL OR latitude IS NULL)
        FROM transit_stop WHERE source_id='mta_bus_gtfs_static'
        """
    ).fetchone()
    add(
        "mta_bus_static_six_feed_coverage",
        "transit_stop",
        "PASS"
        if (
            bus_rows >= 14_000
            and bus_rows == bus_distinct_ids
            and bus_snapshots == 6
            and bus_bad_coordinates == 0
        )
        else "FAIL",
        bus_rows,
        ">=14000 unique scheduled stop rows from 6 feed snapshots with coordinates",
        {
            "distinct_ids": bus_distinct_ids,
            "feed_snapshots": bus_snapshots,
            "missing_coordinates": bus_bad_coordinates,
        },
    )
    realtime_catalog_rows = conn.execute(
        """
        SELECT coalesce(max(row_count),0) FROM dataset_snapshot
        WHERE source_id='mta_realtime_feed_catalog' AND status='SUCCEEDED'
        """
    ).fetchone()[0]
    realtime_live_rows = conn.execute(
        """
        SELECT sum(row_count) FROM (
            SELECT count(*) AS row_count FROM traffic_observation
            WHERE source_id IN ('mta_realtime_feed_catalog','mta_bus_realtime')
            UNION ALL
            SELECT count(*) AS row_count FROM mobility_proxy
            WHERE source_id IN ('mta_realtime_feed_catalog','mta_bus_realtime')
        )
        """
    ).fetchone()[0]
    latest_bus_realtime_status = conn.execute(
        """
        SELECT status FROM ingestion_run WHERE source_id='mta_bus_realtime'
        ORDER BY started_at DESC LIMIT 1
        """
    ).fetchone()
    latest_bus_realtime_status = (
        latest_bus_realtime_status[0] if latest_bus_realtime_status else "NOT_RUN"
    )
    add(
        "mta_realtime_metadata_privacy_boundary",
        "dataset_snapshot+mobility tables",
        "PASS"
        if (
            realtime_catalog_rows == 10
            and realtime_live_rows == 0
            and latest_bus_realtime_status in {"BLOCKED_CREDENTIALS", "SUCCEEDED_WITH_WARNINGS"}
        )
        else "FAIL",
        realtime_catalog_rows,
        "10 documented feeds, no live observations retained, credential gate recorded",
        {
            "live_observation_rows": realtime_live_rows,
            "latest_bus_realtime_status": latest_bus_realtime_status,
            "account_created_by_nyc_lens": False,
        },
    )

    citibike_stations, citibike_distinct_stations = conn.execute(
        """
        SELECT count(*), count(DISTINCT transit_stop_id)
        FROM transit_stop WHERE source_id='citibike_gbfs_current'
        """
    ).fetchone()
    (
        citibike_proxy_rows,
        citibike_proxy_types,
        citibike_proxy_stations,
        citibike_bad_values,
    ) = conn.execute(
        """
        SELECT count(*), count(DISTINCT proxy_type), count(DISTINCT geography_id),
               count(*) FILTER (WHERE value IS NULL OR value < 0)
        FROM mobility_proxy WHERE source_id='citibike_gbfs_current'
        """
    ).fetchone()
    add(
        "citibike_current_station_status_coverage",
        "transit_stop+mobility_proxy",
        "PASS"
        if (
            citibike_stations >= 2_400
            and citibike_stations == citibike_distinct_stations
            and citibike_proxy_rows == citibike_stations * 3
            and citibike_proxy_types == 3
            and citibike_proxy_stations == citibike_stations
            and citibike_bad_values == 0
        )
        else "FAIL",
        citibike_stations,
        ">=2400 unique stations and exactly 3 nonnegative station proxy types per station",
        {
            "proxy_rows": citibike_proxy_rows,
            "proxy_types": citibike_proxy_types,
            "proxy_stations": citibike_proxy_stations,
            "invalid_values": citibike_bad_values,
        },
    )
    citibike_monthly_rows, citibike_monthly_types, citibike_monthly_bad_geo = conn.execute(
        """
        SELECT count(*), count(DISTINCT proxy_type),
               count(*) FILTER (
                   WHERE geography_type <> 'bike_share_system'
                      OR longitude IS NOT NULL OR latitude IS NOT NULL
               )
        FROM mobility_proxy WHERE source_id='citibike_monthly_operating_report'
        """
    ).fetchone()
    citibike_trip_snapshots = conn.execute(
        """
        SELECT count(*) FROM dataset_snapshot
        WHERE source_id IN ('citibike_gbfs_current','citibike_monthly_operating_report')
          AND (lower(raw_path) LIKE '%tripdata%' OR lower(raw_path) LIKE '%trip_history%')
        """
    ).fetchone()[0]
    add(
        "citibike_system_aggregate_privacy",
        "mobility_proxy+dataset_snapshot",
        "PASS"
        if (
            citibike_monthly_rows == 10
            and citibike_monthly_types == 10
            and citibike_monthly_bad_geo == 0
            and citibike_trip_snapshots == 0
        )
        else "FAIL",
        citibike_monthly_rows,
        "10 system-only monthly metrics and no individual trip snapshot",
        {
            "metric_types": citibike_monthly_types,
            "rows_with_station_or_point_geography": citibike_monthly_bad_geo,
            "trip_snapshots": citibike_trip_snapshots,
        },
    )

    hazard_minimums = {
        "nyc_hurricane_evacuation_zones": 8,
        "nyc_future_floodplain_2050s": 10_000,
        "nyc_sea_level_rise_2100_floodplain": 1,
    }
    hazard_results: dict[str, Any] = {}
    hazard_failures: dict[str, Any] = {}
    for source_id, minimum in hazard_minimums.items():
        rows, distinct_ids, bad_bounds = conn.execute(
            """
            SELECT count(*), count(DISTINCT boundary_id), count(*) FILTER (
                WHERE min_longitude IS NULL OR min_latitude IS NULL
                   OR max_longitude IS NULL OR max_latitude IS NULL
                   OR min_longitude > max_longitude OR min_latitude > max_latitude
            )
            FROM geo_boundary WHERE source_id=?
            """,
            [source_id],
        ).fetchone()
        hazard_results[source_id] = {
            "rows": rows,
            "distinct_ids": distinct_ids,
            "invalid_bounds": bad_bounds,
            "minimum": minimum,
        }
        if rows < minimum or rows != distinct_ids or bad_bounds:
            hazard_failures[source_id] = hazard_results[source_id]
    add(
        "nyc_hazard_planning_layer_coverage",
        "geo_boundary",
        "PASS" if not hazard_failures else "FAIL",
        len(hazard_failures),
        0,
        {"layers": hazard_results, "failures": hazard_failures},
    )

    taxi_zone_rows, taxi_zone_distinct, taxi_zone_bad_bounds = conn.execute(
        """
        SELECT count(*), count(DISTINCT geography_id), count(*) FILTER (
            WHERE min_longitude IS NULL OR min_latitude IS NULL
               OR max_longitude IS NULL OR max_latitude IS NULL
        )
        FROM geo_boundary WHERE source_id='tlc_taxi_zones'
        """
    ).fetchone()
    tlc_rows, tlc_distinct, tlc_types, tlc_bad_values, tlc_unlinked_zones = conn.execute(
        """
        SELECT count(*), count(DISTINCT mobility_proxy_id), count(DISTINCT proxy_type),
               count(*) FILTER (WHERE value IS NULL OR value < 5),
               count(*) FILTER (WHERE z.geography_id IS NULL)
        FROM mobility_proxy m
        LEFT JOIN geo_boundary z
          ON z.source_id='tlc_taxi_zones'
         AND z.geography_type='tlc_taxi_zone'
         AND z.geography_id=m.geography_id
        WHERE m.source_id='tlc_trip_record_monthly_aggregates'
        """
    ).fetchone()
    tlc_individual_raw_snapshots = conn.execute(
        """
        SELECT count(*) FROM dataset_snapshot
        WHERE source_id='tlc_trip_record_monthly_aggregates'
          AND (
              lower(raw_path) LIKE '%yellow_tripdata_%'
              OR lower(raw_path) LIKE '%green_tripdata_%'
          )
        """
    ).fetchone()[0]
    tlc_manifest_failures = conn.execute(
        """
        SELECT count(*) FROM dataset_snapshot
        WHERE source_id='tlc_trip_record_monthly_aggregates'
          AND lower(raw_path) LIKE '%tlc_zone_dow_hour_aggregate_%'
          AND (
              json_extract_string(query_json, '$.individual_trip_rows_retained') <> 'false'
              OR json_extract_string(query_json, '$.origin_destination_linkage_retained') <> 'false'
              OR try_cast(json_extract_string(query_json, '$.minimum_published_cell_count') AS INTEGER) < 5
          )
        """
    ).fetchone()[0]
    add(
        "tlc_privacy_guarded_monthly_zone_hour_aggregate",
        "geo_boundary+mobility_proxy+dataset_snapshot",
        "PASS"
        if (
            taxi_zone_rows >= 250
            and taxi_zone_rows == taxi_zone_distinct
            and taxi_zone_bad_bounds == 0
            and tlc_rows >= 40_000
            and tlc_rows == tlc_distinct
            and tlc_types == 4
            and tlc_bad_values == 0
            and tlc_unlinked_zones == 0
            and tlc_individual_raw_snapshots == 0
            and tlc_manifest_failures == 0
        )
        else "FAIL",
        tlc_rows,
        ">=40000 unique cells, 4 proxy types, values >=5, all zones linked, no individual Raw trips",
        {
            "taxi_zones": taxi_zone_rows,
            "taxi_zone_distinct_ids": taxi_zone_distinct,
            "taxi_zone_invalid_bounds": taxi_zone_bad_bounds,
            "aggregate_distinct_ids": tlc_distinct,
            "proxy_types": tlc_types,
            "invalid_or_unsuppressed_low_values": tlc_bad_values,
            "unlinked_zone_cells": tlc_unlinked_zones,
            "individual_trip_raw_snapshots": tlc_individual_raw_snapshots,
            "aggregate_manifest_failures": tlc_manifest_failures,
        },
    )

    sales_rows, sales_linked, negative_sales = conn.execute(
        """
        SELECT count(*), count(*) FILTER (WHERE geography_id IS NOT NULL),
               count(*) FILTER (WHERE amount < 0)
        FROM property_record WHERE source_id='nyc_rolling_sales'
        """
    ).fetchone()
    add(
        "rolling_sales_coverage",
        "property_record",
        "PASS" if sales_rows >= 75_000 and sales_linked / max(1, sales_rows) >= 0.99 and negative_sales == 0 else "FAIL",
        sales_rows,
        ">=75000 rows, >=99% BBL linked, no negative sale prices",
        {
            "linked_bbl": sales_linked,
            "linked_rate": sales_linked / max(1, sales_rows),
            "negative_sale_prices": negative_sales,
        },
    )
    acris_rows, acris_distinct, acris_linked, acris_negative = conn.execute(
        """
        SELECT count(*), count(DISTINCT property_record_id),
               count(*) FILTER (WHERE geography_id IS NOT NULL),
               count(*) FILTER (WHERE amount < 0)
        FROM property_record WHERE source_id='acris_real_property_master_recent'
        """
    ).fetchone()
    add(
        "acris_recent_document_coverage",
        "property_record",
        "PASS"
        if (
            acris_rows >= 1_300_000
            and acris_rows == acris_distinct
            and acris_linked / max(1, acris_rows) >= 0.99
            and acris_negative == 0
        )
        else "FAIL",
        acris_rows,
        ">=1300000 unique document-lot records, >=99% BBL linked, no negative amount",
        {
            "distinct_ids": acris_distinct,
            "linked_bbl": acris_linked,
            "linked_rate": acris_linked / max(1, acris_rows),
            "negative_amounts": acris_negative,
        },
    )
    request_311_rows, request_311_distinct, request_311_linked, request_311_bad_counts = conn.execute(
        """
        SELECT count(*), count(DISTINCT regulatory_record_id),
               count(*) FILTER (WHERE geography_id IS NOT NULL),
               count(*) FILTER (
                   WHERE try_cast(json_extract_string(attributes_json, '$.request_count') AS BIGINT) <= 0
                      OR try_cast(json_extract_string(attributes_json, '$.request_count') AS BIGINT) IS NULL
               )
        FROM regulatory_record WHERE source_id='nyc_311_recent_aggregates'
        """
    ).fetchone()
    add(
        "privacy_minimized_311_aggregate_coverage",
        "regulatory_record",
        "PASS"
        if (
            request_311_rows >= 1_500_000
            and request_311_rows == request_311_distinct
            and request_311_linked / max(1, request_311_rows) >= 0.99
            and request_311_bad_counts == 0
        )
        else "FAIL",
        request_311_rows,
        ">=1500000 unique BBL-problem-agency aggregates, >=99% BBL linked, positive counts",
        {
            "distinct_ids": request_311_distinct,
            "linked_bbl": request_311_linked,
            "linked_rate": request_311_linked / max(1, request_311_rows),
            "invalid_request_counts": request_311_bad_counts,
            "individual_311_rows_retained": False,
        },
    )
    regulatory_minimums = {
        "dob_permit_issuance_recent": 150_000,
        "dob_now_approved_permits_recent": 800_000,
        "dob_violations_active": 500_000,
        "dob_safety_violations_active": 600_000,
        "dob_ecb_violations_active": 250_000,
        "dob_certificate_occupancy_legacy": 140_000,
        "dob_now_certificate_occupancy": 75_000,
    }
    regulatory_counts = dict(
        conn.execute(
            """
            SELECT source_id, count(*) FROM regulatory_record
            WHERE source_id IN (
                'dob_permit_issuance_recent','dob_now_approved_permits_recent',
                'dob_violations_active','dob_safety_violations_active',
                'dob_ecb_violations_active','dob_certificate_occupancy_legacy',
                'dob_now_certificate_occupancy'
            ) GROUP BY source_id
            """
        ).fetchall()
    )
    regulatory_failures = {
        source_id: {"observed": regulatory_counts.get(source_id, 0), "minimum": minimum}
        for source_id, minimum in regulatory_minimums.items()
        if regulatory_counts.get(source_id, 0) < minimum
    }
    regulatory_unlinked = dict(
        conn.execute(
            """
            SELECT source_id,
                   count(*) FILTER (WHERE geography_id IS NULL) / greatest(count(*),1)::DOUBLE
            FROM regulatory_record
            WHERE source_id IN (
                'dob_permit_issuance_recent','dob_now_approved_permits_recent',
                'dob_violations_active','dob_safety_violations_active',
                'dob_ecb_violations_active','dob_certificate_occupancy_legacy',
                'dob_now_certificate_occupancy'
            ) GROUP BY source_id
            """
        ).fetchall()
    )
    excessive_unlinked = {
        source_id: rate for source_id, rate in regulatory_unlinked.items() if rate > 0.15
    }
    add(
        "property_regulatory_source_coverage",
        "regulatory_record",
        "PASS" if not regulatory_failures and not excessive_unlinked else "FAIL",
        sum(regulatory_counts.values()),
        "all seven source minimums and <=15% unresolved BBL per source",
        {
            "counts": regulatory_counts,
            "minimum_failures": regulatory_failures,
            "unresolved_bbl_rates": regulatory_unlinked,
            "excessive_unresolved": excessive_unlinked,
        },
    )

    prohibited_api_fields = {
        "applicant_first_name",
        "applicant_middle_name",
        "applicant_last_name",
        "applicant_business_address",
        "filing_representative_first_name",
        "filing_representative_last_name",
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
        "apartment_number",
        "apt_condo_no_s",
    }
    property_source_ids = {"nyc_rolling_sales", *regulatory_minimums}
    selected_queries = conn.execute(
        """
        SELECT source_id, query_json
        FROM dataset_snapshot
        WHERE source_id IN (
            'nyc_rolling_sales','dob_permit_issuance_recent','dob_now_approved_permits_recent',
            'dob_violations_active','dob_safety_violations_active','dob_ecb_violations_active',
            'dob_certificate_occupancy_legacy','dob_now_certificate_occupancy'
        ) AND query_json IS NOT NULL
        """
    ).fetchall()
    privacy_failures: list[dict[str, Any]] = []
    sources_with_data_select: set[str] = set()
    for source_id, query_payload in selected_queries:
        query = json.loads(query_payload) if isinstance(query_payload, str) else query_payload
        select = str(query.get("$select", "")).lower()
        if select and "count(*)" not in select:
            sources_with_data_select.add(source_id)
            matches = sorted(field for field in prohibited_api_fields if field in select)
            if matches:
                privacy_failures.append({"source_id": source_id, "fields": matches})
    missing_privacy_queries = sorted(property_source_ids - sources_with_data_select)
    add(
        "property_api_personal_field_exclusion",
        "dataset_snapshot.query_json",
        "PASS" if not privacy_failures and not missing_privacy_queries else "FAIL",
        len(privacy_failures) + len(missing_privacy_queries),
        0,
        {
            "prohibited_field_matches": privacy_failures,
            "sources_without_a_data_select_snapshot": missing_privacy_queries,
            "note": "Official dataset metadata can list all publisher columns; this check audits the actual selected record-data queries.",
        },
    )

    acris_query_rows = conn.execute(
        """
        SELECT source_id, query_json
        FROM dataset_snapshot
        WHERE source_id IN (
            'acris_real_property_master_recent','acris_real_property_legals_recent'
        ) AND query_json IS NOT NULL
          AND json_extract_string(query_json, '$.\"$select\"') IS NOT NULL
        """
    ).fetchall()
    acris_personal_fields = {
        "party_type",
        "name",
        "address_1",
        "address_2",
        "city",
        "state",
        "zip",
        "country",
    }
    acris_query_failures: list[dict[str, Any]] = []
    acris_query_sources: set[str] = set()
    for source_id, query_payload in acris_query_rows:
        query = json.loads(query_payload) if isinstance(query_payload, str) else query_payload
        selected = {
            item.strip().lower().split(" as ", 1)[0]
            for item in str(query.get("$select", "")).split(",")
            if item.strip()
        }
        if selected and not any(item.startswith("count(") for item in selected):
            acris_query_sources.add(source_id)
            prohibited = sorted(selected & acris_personal_fields)
            if prohibited:
                acris_query_failures.append(
                    {"source_id": source_id, "prohibited_fields": prohibited}
                )
    expected_acris_sources = {
        "acris_real_property_master_recent",
        "acris_real_property_legals_recent",
    }
    missing_acris_queries = sorted(expected_acris_sources - acris_query_sources)
    add(
        "acris_party_field_exclusion",
        "dataset_snapshot.query_json",
        "PASS" if not acris_query_failures and not missing_acris_queries else "FAIL",
        len(acris_query_failures) + len(missing_acris_queries),
        0,
        {
            "prohibited_field_matches": acris_query_failures,
            "sources_without_record_data_select": missing_acris_queries,
            "note": "Only document and tax-lot linkage fields are retained; ACRIS party records are not requested.",
        },
    )

    prohibited_311_fields = {
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
    aggregate_311_queries = 0
    privacy_311_failures: list[dict[str, Any]] = []
    for snapshot_id, query_payload in conn.execute(
        """
        SELECT snapshot_id, query_json FROM dataset_snapshot
        WHERE source_id='nyc_311_recent_aggregates' AND query_json IS NOT NULL
          AND json_extract_string(query_json, '$.\"$select\"') IS NOT NULL
        """
    ).fetchall():
        query = json.loads(query_payload) if isinstance(query_payload, str) else query_payload
        selected_text = str(query.get("$select", "")).lower()
        grouped_text = str(query.get("$group", "")).lower()
        selected_fields = {
            item.strip().split(" as ", 1)[0].split("(", 1)[-1].rstrip("*)").strip()
            for item in selected_text.split(",")
            if item.strip()
        }
        if "count(*)" in selected_text and grouped_text:
            aggregate_311_queries += 1
        prohibited = sorted(selected_fields & prohibited_311_fields)
        required_groups = {"bbl", "complaint_type", "agency"}
        observed_groups = {item.strip() for item in grouped_text.split(",") if item.strip()}
        if prohibited or (grouped_text and observed_groups != required_groups):
            privacy_311_failures.append(
                {
                    "snapshot_id": snapshot_id,
                    "prohibited_fields": prohibited,
                    "groups": sorted(observed_groups),
                }
            )
    add(
        "311_server_aggregation_privacy",
        "dataset_snapshot.query_json",
        "PASS" if aggregate_311_queries >= 24 and not privacy_311_failures else "FAIL",
        len(privacy_311_failures),
        "0 failures and >=24 monthly aggregate queries",
        {
            "aggregate_query_snapshots": aggregate_311_queries,
            "failures": privacy_311_failures,
            "individual_request_rows_requested": False,
        },
    )

    regional_inputs = [
        "100 Supreme Court Drive, Mineola, NY 11501",
        "920 Broad Street, Newark, NJ 07102",
        "888 Washington Boulevard, Stamford, CT 06901",
        "412 Broad Street, Milford, PA 18337",
    ]
    placeholders = ",".join("?" for _ in regional_inputs)
    regional_latest = conn.execute(
        f"""
        SELECT site_id, json_extract_string(result_json, '$.state_abbr') AS state_abbr
        FROM site_query
        WHERE original_input IN ({placeholders}) AND status='SUCCEEDED'
        QUALIFY row_number() OVER (PARTITION BY original_input ORDER BY created_at DESC)=1
        """,
        regional_inputs,
    ).fetchall()
    regional_site_ids = [row[0] for row in regional_latest]
    if regional_site_ids:
        site_placeholders = ",".join("?" for _ in regional_site_ids)
        regional_catchments, regional_walks = conn.execute(
            f"""
            SELECT count(*), count(*) FILTER (
                WHERE catchment_type IN ('5min_walk','10min_walk','15min_walk')
                  AND network_snapshot_id IS NOT NULL
            )
            FROM catchment WHERE site_id IN ({site_placeholders})
            """,
            regional_site_ids,
        ).fetchone()
        fema_metric_catchments, cdc_metric_catchments, regional_poi_metric_rows = conn.execute(
            f"""
            SELECT
                count(DISTINCT catchment_id) FILTER (
                    WHERE metric_id='fema_special_flood_hazard_overlap_pct' AND value IS NOT NULL
                ),
                count(DISTINCT catchment_id) FILTER (
                    WHERE metric_id='cdc_places_current_asthma_pct' AND value IS NOT NULL
                ),
                count(*) FILTER (
                    WHERE metric_id IN (
                        'regional_hotel_poi_count','regional_school_poi_count',
                        'regional_healthcare_poi_count','regional_park_poi_count',
                        'regional_library_poi_count'
                    ) AND value IS NOT NULL
                )
            FROM site_metric WHERE site_id IN ({site_placeholders})
            """,
            regional_site_ids,
        ).fetchone()
        regional_report_formats = conn.execute(
            f"""
            SELECT count(*) FROM (
                SELECT DISTINCT site_id, format FROM site_report
                WHERE site_id IN ({site_placeholders})
                  AND format IN ('json','csv','html','xlsx')
            )
            """,
            regional_site_ids,
        ).fetchone()[0]
    else:
        regional_catchments = regional_walks = 0
        fema_metric_catchments = cdc_metric_catchments = regional_poi_metric_rows = 0
        regional_report_formats = 0
    regional_sample_ok = (
        len(regional_latest) == 4
        and len({row[1] for row in regional_latest}) == 4
        and regional_catchments == 24
        and regional_walks == 12
        and fema_metric_catchments == 24
        and cdc_metric_catchments == 24
        and regional_poi_metric_rows == 120
        and regional_report_formats == 16
    )
    add(
        "four_state_regional_end_to_end_samples",
        "site_query+catchment+site_metric+site_report",
        "PASS" if regional_sample_ok else "FAIL",
        len(regional_latest),
        "4 states, 24 catchments, 12 walk catchments, FEMA+CDC+regional POI metrics, 4 report formats per site",
        {
            "latest_sites": regional_latest,
            "catchments": regional_catchments,
            "walk_catchments_with_raw_network": regional_walks,
            "fema_metric_catchments": fema_metric_catchments,
            "cdc_asthma_metric_catchments": cdc_metric_catchments,
            "regional_poi_metric_rows": regional_poi_metric_rows,
            "distinct_site_report_formats": regional_report_formats,
        },
    )

    comparison_rows = conn.execute(
        """
        SELECT count(*) FROM site_comparison
        WHERE status='SUCCEEDED'
          AND json_array_length(site_ids_json) BETWEEN 2 AND 20
          AND strpos(lower(result_json::VARCHAR), '"composite_site_score"') = 0
          AND lower(warnings_json::VARCHAR) LIKE '%no composite site score%'
        """
    ).fetchone()[0]
    comparison_file_failures = 0
    comparison_formats: set[str] = set()
    for report_format, local_path, expected_hash in conn.execute(
        "SELECT format, local_path, sha256 FROM comparison_report"
    ).fetchall():
        comparison_formats.add(str(report_format))
        report_file = Path(local_path)
        if not report_file.is_file() or sha256_file(report_file) != expected_hash:
            comparison_file_failures += 1
    add(
        "multi_address_comparison_and_exports",
        "site_comparison+comparison_report",
        "PASS"
        if comparison_rows >= 1 and {"json", "csv", "xlsx"}.issubset(comparison_formats) and comparison_file_failures == 0
        else "FAIL",
        comparison_rows,
        ">=1 successful 2-20 address comparison, no composite score, traceable JSON/CSV/XLSX",
        {
            "formats": sorted(comparison_formats),
            "file_or_hash_failures": comparison_file_failures,
            "rank_semantics": "raw high-to-low is not better/recommended",
        },
    )

    transaction_rows, transaction_distinct, transaction_negative = conn.execute(
        """
        SELECT count(*), count(DISTINCT transaction_case_id),
               count(*) FILTER (WHERE amount < 0)
        FROM transaction_case WHERE source_id='derived_public_transaction_cases'
        """
    ).fetchone()
    prohibited_transaction_keys = {
        "address",
        "street_address",
        "unit",
        "apartment",
        "party_name",
        "owner_name",
        "phone",
        "email",
    }
    observed_transaction_keys = {
        str(row[0]).lower()
        for row in conn.execute(
            "SELECT DISTINCT unnest(json_keys(attributes_json)) FROM transaction_case"
        ).fetchall()
    }
    observed_sales_keys = {
        str(row[0]).lower()
        for row in conn.execute(
            """
            SELECT DISTINCT unnest(json_keys(attributes_json))
            FROM property_record WHERE source_id='nyc_rolling_sales'
            """
        ).fetchall()
    }
    transaction_privacy_failures = sorted(
        (observed_transaction_keys | observed_sales_keys) & prohibited_transaction_keys
    )
    add(
        "public_transaction_case_coverage_and_privacy",
        "transaction_case+property_record",
        "PASS"
        if transaction_rows >= 250_000 and transaction_rows == transaction_distinct and transaction_negative == 0 and not transaction_privacy_failures
        else "FAIL",
        transaction_rows,
        ">=250000 unique nonnegative cases and no personal/street/unit/contact fields",
        {
            "distinct_ids": transaction_distinct,
            "negative_amounts": transaction_negative,
            "prohibited_keys": transaction_privacy_failures,
            "scope": "public real-property comparables, not business-transfer valuations",
        },
    )

    mobility_adapter = inspect_commercial_mobility_adapters()
    real_estate_adapter = inspect_commercial_real_estate_adapters()
    adapter_statuses = [
        provider["status"]
        for provider in [
            *mobility_adapter["providers"].values(),
            *real_estate_adapter["providers"].values(),
        ]
    ]
    adapters_safe = (
        not mobility_adapter["network_contact_attempted"]
        and not mobility_adapter["account_created"]
        and not mobility_adapter["commercial_data_downloaded"]
        and not real_estate_adapter["network_contact_attempted"]
        and not real_estate_adapter["account_created"]
        and not real_estate_adapter["commercial_data_downloaded"]
        and not real_estate_adapter["commercial_data_ingested"]
        and all(status != "ACTIVE" for status in adapter_statuses)
    )
    score_gate = scoring_status(conn)
    add(
        "commercial_adapters_and_scoring_gates",
        "adapter_configs+scoring_model",
        "PASS" if adapters_safe and score_gate["status"] == "BLOCKED_CALIBRATION" else "FAIL",
        {"adapters_safe": adapters_safe, "scoring_status": score_gate["status"]},
        "no contact/account/download/ingest; no active commercial adapter; scoring blocked without calibration",
        {
            "provider_statuses": adapter_statuses,
            "scoring_minimum_requirements": score_gate.get("minimum_requirements"),
        },
    )

    share_failures = conn.execute(
        "SELECT count(*) FROM site_metric WHERE unit='share' AND value IS NOT NULL AND (value < 0 OR value > 1)"
    ).fetchone()[0]
    add("share_metric_range", "site_metric", "PASS" if share_failures == 0 else "FAIL", share_failures, 0)

    percentile_failures = conn.execute(
        "SELECT count(*) FROM site_metric WHERE percentile IS NOT NULL AND (percentile < 0 OR percentile > 100)"
    ).fetchone()[0]
    add(
        "site_metric_percentile_range",
        "site_metric",
        "PASS" if percentile_failures == 0 else "FAIL",
        percentile_failures,
        0,
    )
    benchmark_centers, benchmark_catchments, benchmark_metrics = conn.execute(
        """
        SELECT count(DISTINCT sample_cell_id), count(DISTINCT catchment_type), count(DISTINCT metric_id)
        FROM benchmark_observation
        WHERE benchmark_scope='nyc_citywide_h3_r10_stratified'
        """
    ).fetchone()
    benchmark_ok = benchmark_centers >= 250 and benchmark_catchments == 3 and benchmark_metrics >= 25
    add(
        "citywide_radial_benchmark_coverage",
        "benchmark_observation",
        "PASS" if benchmark_ok else "FAIL",
        benchmark_centers,
        ">=250 centers, 3 catchments, >=25 metrics",
        {"catchment_types": benchmark_catchments, "numeric_metrics": benchmark_metrics},
    )
    benchmark_snapshot_row = conn.execute(
        """
        SELECT snapshot_set_json, method_version
        FROM benchmark_observation
        WHERE benchmark_scope='nyc_citywide_h3_r10_stratified'
        ORDER BY created_at DESC LIMIT 1
        """
    ).fetchone()
    if benchmark_snapshot_row:
        benchmark_snapshot_set = (
            json.loads(benchmark_snapshot_row[0])
            if isinstance(benchmark_snapshot_row[0], str)
            else benchmark_snapshot_row[0]
        )
        acs_snapshots = benchmark_snapshot_set.get("acs_2024_5yr", [])
        bus_snapshots = benchmark_snapshot_set.get("mta_bus_gtfs_static", [])
        benchmark_snapshot_ok = (
            benchmark_snapshot_row[1] == "0.2.0"
            and isinstance(acs_snapshots, list)
            and len(acs_snapshots) >= 14
            and isinstance(bus_snapshots, list)
            and len(bus_snapshots) >= 6
        )
        benchmark_snapshot_details = {
            "method_version": benchmark_snapshot_row[1],
            "source_count": len(benchmark_snapshot_set),
            "acs_snapshots": len(acs_snapshots),
            "mta_bus_snapshots": len(bus_snapshots),
        }
    else:
        benchmark_snapshot_ok = False
        benchmark_snapshot_details = {"note": "No benchmark observation exists."}
    add(
        "benchmark_snapshot_reproducibility",
        "benchmark_observation.snapshot_set_json",
        "PASS" if benchmark_snapshot_ok else "FAIL",
        benchmark_snapshot_details,
        "method 0.2.0, >=14 ACS table snapshots, >=6 MTA bus feed snapshots",
    )

    catchment_duplicates = conn.execute(
        """
        SELECT count(*) FROM (
            SELECT site_id, catchment_type, count(*)
            FROM catchment GROUP BY 1,2 HAVING count(*) > 1
        )
        """
    ).fetchone()[0]
    add("catchment_unique_per_query", "catchment", "PASS" if catchment_duplicates == 0 else "FAIL", catchment_duplicates, 0)

    running_queries = conn.execute(
        "SELECT count(*) FROM site_query WHERE status='RUNNING'"
    ).fetchone()[0]
    add("no_orphan_running_queries", "site_query", "PASS" if running_queries == 0 else "FAIL", running_queries, 0)

    metric_metadata_missing = conn.execute(
        """
        WITH latest_sites AS (
            SELECT site_id FROM (
                SELECT site_id, row_number() OVER (
                    PARTITION BY original_input ORDER BY created_at DESC
                ) AS rank
                FROM site_query WHERE status='SUCCEEDED'
            ) WHERE rank=1
        )
        SELECT count(*) FROM site_metric m JOIN latest_sites USING(site_id)
        WHERE (m.value IS NOT NULL OR m.text_value IS NOT NULL)
          AND (m.source_id IS NULL OR m.source_year IS NULL OR m.measure_type IS NULL OR m.confidence IS NULL)
        """
    ).fetchone()[0]
    add("visible_metric_metadata", "site_metric", "PASS" if metric_metadata_missing == 0 else "FAIL", metric_metadata_missing, 0)

    lineage_missing = conn.execute(
        """
        SELECT count(*) FROM site_metric m
        LEFT JOIN calculation_version c ON c.calculation_version_id=m.calculation_version_id
        LEFT JOIN dataset_snapshot s ON s.snapshot_id=m.snapshot_id
        WHERE c.calculation_version_id IS NULL
           OR (
               m.source_id NOT IN ('derived')
               AND (m.value IS NOT NULL OR m.text_value IS NOT NULL)
               AND (m.snapshot_id IS NULL OR s.snapshot_id IS NULL)
           )
        """
    ).fetchone()[0]
    add("metric_lineage", "site_metric", "PASS" if lineage_missing == 0 else "FAIL", lineage_missing, 0)

    # Parse the modest number of metric JSON arrays in a streaming Python loop.
    # DuckDB's JSON table function can repeatedly materialize large intermediate
    # vectors for these arrays; hashing IDs here keeps the sample deterministic
    # while avoiding that query-plan memory spike.
    evidence_payload_rows = conn.execute(
        """
        WITH latest_sites AS (
            SELECT site_id FROM (
                SELECT site_id, row_number() OVER (
                    PARTITION BY original_input ORDER BY created_at DESC
                ) AS rank
                FROM site_query WHERE status='SUCCEEDED'
            ) WHERE rank=1
        )
        SELECT evidence_record_ids_json
        FROM site_metric JOIN latest_sites USING(site_id)
        """
    ).fetchall()
    current_evidence_total_links = 0
    evidence_sample: set[str] = set()
    for (payload,) in evidence_payload_rows:
        values = json.loads(payload) if isinstance(payload, str) else (payload or [])
        if not isinstance(values, list):
            continue
        current_evidence_total_links += len(values)
        for value in values:
            entity_id = str(value) if value is not None else ""
            if not entity_id:
                continue
            digest_prefix = int.from_bytes(
                hashlib.sha256(entity_id.encode("utf-8")).digest()[:4], "big"
            )
            if digest_prefix % 101 == 0 and len(evidence_sample) < 10_000:
                evidence_sample.add(entity_id)
    conn.execute("CREATE OR REPLACE TEMP TABLE qa_current_evidence_ids(entity_id VARCHAR)")
    if evidence_sample:
        conn.executemany(
            "INSERT INTO qa_current_evidence_ids VALUES (?)",
            [(value,) for value in sorted(evidence_sample)],
        )
    sampled_evidence_count = len(evidence_sample)
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE qa_matched_evidence_ids AS
        SELECT DISTINCT entity_id FROM standardized_record
        WHERE entity_id IN (SELECT entity_id FROM qa_current_evidence_ids)
        """
    )
    evidence_lineage_missing = conn.execute(
        """
        SELECT count(*) FROM qa_current_evidence_ids evidence
        LEFT JOIN qa_matched_evidence_ids matched USING(entity_id)
        WHERE matched.entity_id IS NULL
        """
    ).fetchone()[0]
    add(
        "sampled_evidence_to_standardized_record",
        "site_metric",
        "PASS" if sampled_evidence_count >= 1_000 and evidence_lineage_missing == 0 else "FAIL",
        evidence_lineage_missing,
        0,
        {
            "latest_evidence_links": current_evidence_total_links,
            "deterministic_hash_sample_checked": sampled_evidence_count,
            "sampling_rule": "hash(entity_id) modulo 101 equals 0, capped at 10000",
        },
    )

    report_file_failures = 0
    for local_path, expected_hash in conn.execute("SELECT local_path, sha256 FROM site_report").fetchall():
        report_file = Path(local_path)
        if not report_file.is_file() or sha256_file(report_file) != expected_hash:
            report_file_failures += 1
    add("report_files_traceable", "site_report", "PASS" if report_file_failures == 0 else "FAIL", report_file_failures, 0)

    missing_http_status = conn.execute(
        """
        SELECT count(*) FROM dataset_snapshot
        WHERE status='SUCCEEDED' AND http_status IS NULL
          AND source_url LIKE 'http%'
        """
    ).fetchone()[0]
    add(
        "snapshot_http_status_recorded",
        "dataset_snapshot",
        "PASS" if missing_http_status == 0 else "WARN",
        missing_http_status,
        0,
        {"note": "Older snapshots predate the additive HTTP-status field; hashes and files remain intact."},
    )

    sources_without_success = conn.execute(
        """
        SELECT list(source_id ORDER BY source_id) FROM source_registry s
        WHERE NOT EXISTS (
            SELECT 1 FROM ingestion_run r WHERE r.source_id=s.source_id
              AND r.status IN ('SUCCEEDED','SUCCEEDED_WITH_WARNINGS')
        )
        """
    ).fetchone()[0] or []
    add(
        "source_refresh_coverage",
        "source_registry",
        "PASS" if not sources_without_success else "WARN",
        len(sources_without_success),
        0,
        {"source_ids": sources_without_success},
    )

    passed = sum(result["status"] == "PASS" for result in results)
    failed = sum(result["status"] == "FAIL" for result in results)
    warned = sum(result["status"] == "WARN" for result in results)
    report = {
        "checked_at": utc_now().isoformat(),
        "summary": {"passed": passed, "failed": failed, "warned": warned, "total": len(results)},
        "results": results,
    }
    report_path = project_home() / "reports" / "qa" / "latest.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return report
