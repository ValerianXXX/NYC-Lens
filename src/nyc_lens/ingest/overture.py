from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import duckdb
import httpx

from ..config import geographies_config
from ..db import begin_run, finish_run
from ..paths import data_dir
from ..provenance import save_bytes_snapshot, save_file_snapshot
from ..storage import MAX_SINGLE_FILE_BYTES, replace_source_from_relation
from .common import register_table_lineage, standardized_path


STAC_URL = "https://stac.overturemaps.org/catalog.json"


def refresh_overture_places(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "overture_places"
    run_id = begin_run(conn, source_id, "refresh")
    temporary_path: Path | None = None
    try:
        stac_response = httpx.get(STAC_URL, timeout=60, follow_redirects=True, headers={"User-Agent": "NYC-Lens/0.1"})
        stac_response.raise_for_status()
        stac_payload = stac_response.json()
        release = stac_payload["latest"]
        catalog_snapshot = save_bytes_snapshot(
            conn,
            source_id=source_id,
            vintage=release,
            filename="stac_catalog.json",
            content=stac_response.content,
            source_url=STAC_URL,
            run_id=run_id,
            content_type="application/json",
            query={"kind": "release discovery"},
            http_status=stac_response.status_code,
        )
        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")
        conn.execute("LOAD spatial")
        conn.execute("SET s3_region='us-west-2'")
        conn.execute("PRAGMA disable_progress_bar")
        bbox = geographies_config()["greater_nyc"]["bbox"]
        region_row = conn.execute(
            """
            SELECT geom_wkt FROM geo_boundary
            WHERE source_id='tiger_county_2024'
              AND geography_type='combined_statistical_area'
              AND geography_id='408'
            LIMIT 1
            """
        ).fetchone()
        if not region_row:
            raise ValueError("Load the official CSA 408 county boundary union before refreshing regional Overture Places.")
        conn.execute(
            "CREATE OR REPLACE TEMP TABLE _greater_nyc_region AS SELECT ST_GeomFromText(?) AS geometry",
            [region_row[0]],
        )
        parquet_glob = (
            f"s3://overturemaps-us-west-2/release/{release}/"
            "theme=places/type=place/*.parquet"
        )
        cache_dir = data_dir() / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            prefix="overture_places_", suffix=".parquet", dir=cache_dir, delete=False
        )
        temporary_path = Path(handle.name)
        handle.close()
        # DuckDB COPY refuses to overwrite an existing file. NamedTemporaryFile
        # reserves a unique path for us, so remove the empty placeholder first.
        temporary_path.unlink()
        source_sql = parquet_glob.replace("'", "''")
        output_sql = str(temporary_path).replace("'", "''")
        conn.execute(
            f"""
            COPY (
                SELECT
                    id,
                    names.primary AS name,
                    basic_category,
                    to_json(categories) AS categories_json,
                    to_json(taxonomy) AS taxonomy_json,
                    to_json(addresses) AS addresses_json,
                    concat_ws(', ', addresses[1].freeform, addresses[1].locality, addresses[1].region, addresses[1].postcode) AS address,
                    ST_X(place.geometry) AS longitude,
                    ST_Y(place.geometry) AS latitude,
                    to_json(sources) AS source_items_json,
                    operating_status,
                    confidence,
                    version,
                    place.bbox,
                    place.geometry
                FROM read_parquet('{source_sql}', hive_partitioning=1) AS place
                CROSS JOIN _greater_nyc_region AS region
                WHERE place.bbox.xmin >= {float(bbox['west'])}
                  AND place.bbox.xmax <= {float(bbox['east'])}
                  AND place.bbox.ymin >= {float(bbox['south'])}
                  AND place.bbox.ymax <= {float(bbox['north'])}
                  AND ST_Intersects(place.geometry, region.geometry)
            ) TO '{output_sql}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        snapshot = save_file_snapshot(
            conn,
            source_id=source_id,
            vintage=release,
            filename=f"overture_places_greater_nyc_{release}.parquet",
            source_path=temporary_path,
            source_url=parquet_glob,
            run_id=run_id,
            content_type="application/vnd.apache.parquet",
            query={
                "release": release,
                "bbox": bbox,
                "columns_excluded_for_privacy": ["emails", "phones", "socials"],
            },
            warnings=[
                "Spatially filtered official CSA 408 extract, not the global dataset.",
                "Per-feature sources and license fields are retained in source_items_json.",
            ],
        )
        temporary_path.unlink(missing_ok=True)
        temporary_path = None
        raw_path_sql = str(snapshot.path).replace("'", "''")
        row_count = conn.execute(f"SELECT count(*) FROM read_parquet('{raw_path_sql}')").fetchone()[0]
        if row_count < 500_000:
            raise ValueError(f"Greater NYC Overture Places coverage unexpectedly low: {row_count}")
        schema_rows = conn.execute(f"DESCRIBE SELECT * FROM read_parquet('{raw_path_sql}')").fetchall()
        schema = {row[0]: row[1] for row in schema_rows}
        conn.execute(
            "UPDATE dataset_snapshot SET row_count=?, schema_json=?::JSON WHERE snapshot_id=?",
            [row_count, json.dumps(schema), snapshot.snapshot_id],
        )
        source_id_sql = source_id.replace("'", "''")
        snapshot_id_sql = snapshot.snapshot_id.replace("'", "''")
        retrieved_at_sql = snapshot.retrieved_at.strftime("%Y-%m-%d %H:%M:%S.%f")
        conn.execute(
            f"""
            CREATE OR REPLACE TEMP VIEW _overture_pois AS
            SELECT
                'poi_' || substr(sha256(id), 1, 24) AS poi_id,
                id AS source_poi_id,
                name,
                basic_category,
                taxonomy_json,
                address,
                longitude,
                latitude,
                source_items_json,
                operating_status AS status,
                '{source_id_sql}' AS source_id,
                '{snapshot_id_sql}' AS snapshot_id,
                TIMESTAMP '{retrieved_at_sql}' AS retrieved_at,
                NULL::DATE AS valid_from,
                NULL::DATE AS valid_to,
                'point' AS geography_type,
                NULL::VARCHAR AS geography_id,
                'compiled_record' AS measure_type,
                confidence,
                '0.2.0' AS method_version
            FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY lower(trim(coalesce(name, ''))),
                                 round(longitude, 5), round(latitude, 5),
                                 coalesce(basic_category, '')
                    ORDER BY confidence DESC NULLS LAST, id
                ) AS dedupe_rank
                FROM read_parquet('{raw_path_sql}')
            )
            WHERE dedupe_rank=1
            """
        )
        try:
            previous, published = replace_source_from_relation(
                conn,
                table_name="poi",
                source_id=source_id,
                relation="_overture_pois",
                region="greater_nyc",
            )
        finally:
            conn.execute("DROP VIEW IF EXISTS _overture_pois")
        if published <= 0:
            raise ValueError("Overture Places external publish produced no rows")

        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM standardized_record WHERE source_id=? AND entity_type='poi'", [source_id])
            register_table_lineage(
                conn,
                table_name="poi",
                entity_type="poi",
                id_column="poi_id",
                source_id=source_id,
                snapshot_id=snapshot.snapshot_id,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        deduplicated_count = published
        standardized = standardized_path(source_id, release, "places_greater_nyc.parquet")
        standardized_sql = str(standardized).replace("'", "''")
        conn.execute(
            f"""
            COPY (
                SELECT * EXCLUDE (dedupe_rank) FROM (
                    SELECT *, row_number() OVER (
                        PARTITION BY lower(trim(coalesce(name, ''))),
                                     round(longitude, 5), round(latitude, 5),
                                     coalesce(basic_category, '')
                        ORDER BY confidence DESC NULLS LAST, id
                    ) AS dedupe_rank
                    FROM read_parquet('{raw_path_sql}')
                ) WHERE dedupe_rank=1
            ) TO '{standardized_sql}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        finish_run(
            conn,
            run_id,
            "SUCCEEDED",
            request_count=2,
            downloaded_bytes=snapshot.byte_size + catalog_snapshot.byte_size,
            inserted_records=deduplicated_count,
            deleted_records=previous,
        )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "release": release,
            "raw_places": row_count,
            "places": deduplicated_count,
            "exact_name_coordinate_category_duplicates_removed": row_count - deduplicated_count,
            "raw_bytes": snapshot.byte_size + catalog_snapshot.byte_size,
        }
    except Exception as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        conn.execute("DROP TABLE IF EXISTS _greater_nyc_region")


def refresh_overture_buildings(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "overture_buildings"
    run_id = begin_run(conn, source_id, "refresh")
    temporary_path: Path | None = None
    try:
        stac_response = httpx.get(
            STAC_URL,
            timeout=60,
            follow_redirects=True,
            headers={"User-Agent": "NYC-Lens/0.1"},
        )
        stac_response.raise_for_status()
        release = stac_response.json()["latest"]
        catalog_snapshot = save_bytes_snapshot(
            conn,
            source_id=source_id,
            vintage=release,
            filename="stac_catalog.json",
            content=stac_response.content,
            source_url=STAC_URL,
            run_id=run_id,
            content_type="application/json",
            query={"kind": "release discovery"},
            http_status=stac_response.status_code,
        )
        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")
        conn.execute("LOAD spatial")
        conn.execute("SET s3_region='us-west-2'")
        conn.execute("PRAGMA disable_progress_bar")
        bbox = geographies_config()["greater_nyc"]["bbox"]
        region_row = conn.execute(
            """
            SELECT geom_wkt FROM geo_boundary
            WHERE source_id='tiger_county_2024'
              AND geography_type='combined_statistical_area'
              AND geography_id='408'
            LIMIT 1
            """
        ).fetchone()
        if not region_row:
            raise ValueError("Load the official CSA 408 county boundary union before refreshing regional buildings.")
        conn.execute(
            "CREATE OR REPLACE TEMP TABLE _greater_nyc_region AS SELECT ST_GeomFromText(?) AS geometry",
            [region_row[0]],
        )
        parquet_glob = (
            f"s3://overturemaps-us-west-2/release/{release}/"
            "theme=buildings/type=building/*.parquet"
        )
        cache_dir = data_dir() / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            prefix="overture_buildings_", suffix=".parquet", dir=cache_dir, delete=False
        )
        temporary_path = Path(handle.name)
        handle.close()
        temporary_path.unlink()
        source_sql = parquet_glob.replace("'", "''")
        output_sql = str(temporary_path).replace("'", "''")
        conn.execute(
            f"""
            COPY (
                SELECT building.*
                FROM read_parquet('{source_sql}', hive_partitioning=1) AS building
                CROSS JOIN _greater_nyc_region AS region
                WHERE building.bbox.xmax >= {float(bbox['west'])}
                  AND building.bbox.xmin <= {float(bbox['east'])}
                  AND building.bbox.ymax >= {float(bbox['south'])}
                  AND building.bbox.ymin <= {float(bbox['north'])}
                  AND ST_Intersects(building.geometry, region.geometry)
            ) TO '{output_sql}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        if temporary_path.stat().st_size >= MAX_SINGLE_FILE_BYTES:
            raise ValueError(
                "The regional Overture Buildings extract reached the 5 GB safety threshold and requires user confirmation."
            )
        snapshot = save_file_snapshot(
            conn,
            source_id=source_id,
            vintage=release,
            filename=f"overture_buildings_greater_nyc_{release}.parquet",
            source_path=temporary_path,
            source_url=parquet_glob,
            run_id=run_id,
            content_type="application/vnd.apache.parquet",
            query={"release": release, "bbox": bbox, "official_region": "CSA408"},
            warnings=[
                "Spatially filtered official CSA 408 extract, not the global dataset.",
                "Overture Buildings is ODbL-1.0; per-feature source arrays are retained.",
                "NYC authoritative Building Footprints remain the preferred source inside the five boroughs.",
            ],
        )
        temporary_path.unlink(missing_ok=True)
        temporary_path = None
        raw_path_sql = str(snapshot.path).replace("'", "''")
        row_count = int(conn.execute(f"SELECT count(*) FROM read_parquet('{raw_path_sql}')").fetchone()[0])
        if row_count < 1_000_000:
            raise ValueError(f"Greater NYC Overture Buildings coverage unexpectedly low: {row_count}")
        schema_rows = conn.execute(f"DESCRIBE SELECT * FROM read_parquet('{raw_path_sql}')").fetchall()
        schema = {row[0]: row[1] for row in schema_rows}
        conn.execute(
            "UPDATE dataset_snapshot SET row_count=?, schema_json=?::JSON WHERE snapshot_id=?",
            [row_count, json.dumps(schema), snapshot.snapshot_id],
        )
        conn.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE _overture_building_incoming AS
            SELECT
                'building_' || substr(sha256(id), 1, 24) AS building_id,
                NULL::VARCHAR AS bin,
                NULL::VARCHAR AS bbl,
                names.primary AS name,
                NULL::VARCHAR AS address,
                bbox.xmin AS min_longitude,
                bbox.ymin AS min_latitude,
                bbox.xmax AS max_longitude,
                bbox.ymax AS max_latitude,
                ST_AsText(geometry) AS geom_wkt,
                to_json(struct_pack(
                    overture_id := id,
                    subtype := subtype,
                    building_class := "class",
                    level := level,
                    height := height,
                    min_height := min_height,
                    num_floors := num_floors,
                    is_underground := is_underground,
                    sources := sources,
                    version := version
                )) AS attributes_json,
                ?::VARCHAR AS source_id,
                ?::VARCHAR AS snapshot_id,
                ?::TIMESTAMP AS retrieved_at,
                NULL::DATE AS valid_from,
                NULL::DATE AS valid_to,
                'building' AS geography_type,
                NULL::VARCHAR AS geography_id,
                'compiled_record' AS measure_type,
                0.75::DOUBLE AS confidence,
                '0.2.0' AS method_version
            FROM (
                SELECT *, row_number() OVER (PARTITION BY id ORDER BY version DESC) AS dedupe_rank
                FROM read_parquet('{raw_path_sql}')
            )
            WHERE dedupe_rank=1
            """,
            [source_id, snapshot.snapshot_id, snapshot.retrieved_at],
        )
        incoming_count = int(
            conn.execute("SELECT count(*) FROM _overture_building_incoming").fetchone()[0]
        )
        previous, inserted = replace_source_from_relation(
            conn,
            table_name="building",
            source_id=source_id,
            relation="_overture_building_incoming",
            region="greater_nyc",
        )
        if inserted != incoming_count:
            raise ValueError(f"Overture external building count mismatch: {inserted}/{incoming_count}")
        conn.execute(
            "DELETE FROM standardized_record WHERE source_id=? AND entity_type='building'",
            [source_id],
        )
        register_table_lineage(
            conn,
            table_name="building",
            entity_type="building",
            id_column="building_id",
            source_id=source_id,
            snapshot_id=snapshot.snapshot_id,
        )
        warnings = [
            "Overture Buildings is a compiled ODbL layer and is not an authoritative parcel source.",
            "NYC authoritative Building Footprints are preferred inside the five boroughs; Overture supplies regional coverage.",
        ]
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=2,
            downloaded_bytes=snapshot.byte_size + catalog_snapshot.byte_size,
            inserted_records=inserted,
            deleted_records=previous,
            warnings=warnings,
        )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "release": release,
            "raw_buildings": row_count,
            "buildings": inserted,
            "duplicates_removed": row_count - inserted,
            "raw_bytes": snapshot.byte_size + catalog_snapshot.byte_size,
            "warnings": warnings,
        }
    except Exception as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        conn.execute("DROP TABLE IF EXISTS _greater_nyc_region")
        conn.execute("DROP TABLE IF EXISTS _overture_building_incoming")
