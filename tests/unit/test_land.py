from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from nyc_lens.ingest.common import register_standardized_records
from nyc_lens.ingest.land import _building_partition, _mappluto_partition, _replace_table_from_parquet
from nyc_lens.provenance import Snapshot


def _snapshot(tmp_path) -> Snapshot:
    return Snapshot(
        snapshot_id="snap_fixture",
        source_id="fixture",
        path=tmp_path / "source.geojson",
        metadata_path=tmp_path / "manifest.json",
        sha256="fixture",
        byte_size=1,
        retrieved_at=datetime(2026, 8, 29, tzinfo=UTC),
        vintage="fixture",
    )


def test_mappluto_partition_normalizes_bbl_and_bounds(tmp_path) -> None:
    polygon = Polygon([(-74.0, 40.7), (-73.999, 40.7), (-73.999, 40.701), (-74.0, 40.701)])
    frame = gpd.GeoDataFrame(
        {
            "BBL": [1010157502.0],
            "Borough": ["MN"],
            "Block": [1015],
            "Lot": [7502],
            "Address": ["1501 BROADWAY"],
            "LotArea": [1000],
            "BldgArea": [5000],
            "YearBuilt": [1920],
        },
        geometry=[polygon],
        crs="EPSG:4326",
    )

    result, invalid, removed = _mappluto_partition(frame, _snapshot(tmp_path))

    assert invalid == 0 and removed == 0
    assert result.iloc[0]["parcel_id"] == "parcel_1010157502"
    assert result.iloc[0]["min_longitude"] == -74.0
    assert result.iloc[0]["max_latitude"] == 40.701
    assert result.iloc[0]["geom_wkt"].startswith("POLYGON")


def test_building_partition_retains_public_attributes(tmp_path) -> None:
    polygon = Polygon([(-74.0, 40.7), (-73.999, 40.7), (-73.999, 40.701), (-74.0, 40.701)])
    frame = gpd.GeoDataFrame(
        {
            "objectid": [42],
            "bin": [1012345],
            "mappluto_bbl": [1010157502],
            "name": ["TEST BUILDING"],
            "construction_year": [1920],
            "height_roof": [30.5],
        },
        geometry=[polygon],
        crs="EPSG:4326",
    )

    result, invalid, removed = _building_partition(frame, _snapshot(tmp_path))

    assert invalid == 0 and removed == 0
    assert result.iloc[0]["bin"] == "1012345"
    assert result.iloc[0]["bbl"] == "1010157502"
    assert '"construction_year": 1920' in result.iloc[0]["attributes_json"]
    assert result.iloc[0]["confidence"] == 0.98


def test_partitioned_parquet_replacement_is_atomic_and_registers_lineage(tmp_path) -> None:
    conn = duckdb.connect()
    schema = Path(__file__).parents[2] / "src" / "nyc_lens" / "schema.sql"
    conn.execute(schema.read_text(encoding="utf-8"))
    frame = pd.DataFrame(
        [
            {
                "parcel_id": "parcel_1010157502",
                "bbl": "1010157502",
                "borough": "MN",
                "block": "1015",
                "lot": "7502",
                "address": "1501 BROADWAY",
                "land_use": "05",
                "zoning": "C6-7",
                "building_class": "O4",
                "lot_area_sqft": 1000.0,
                "building_area_sqft": 5000.0,
                "residential_area_sqft": 0.0,
                "office_area_sqft": 4000.0,
                "retail_area_sqft": 1000.0,
                "units_residential": 0.0,
                "units_total": 1.0,
                "year_built": 1920,
                "min_longitude": -74.0,
                "min_latitude": 40.7,
                "max_longitude": -73.999,
                "max_latitude": 40.701,
                "geom_wkt": "POLYGON ((-74 40.7, -73.999 40.7, -73.999 40.701, -74 40.7))",
                "source_id": "mappluto_26v2",
                "snapshot_id": "snap_fixture",
                "retrieved_at": datetime(2026, 8, 29),
                "valid_from": date(2026, 1, 1),
                "valid_to": None,
                "geography_type": "tax_lot",
                "geography_id": "1010157502",
                "measure_type": "administrative_record",
                "confidence": 0.98,
                "method_version": "0.2.0",
            }
        ]
    )
    frame.to_parquet(tmp_path / "parcel_part_0001.parquet", index=False)

    previous, inserted = _replace_table_from_parquet(
        conn,
        table_name="parcel",
        id_column="parcel_id",
        entity_type="parcel",
        source_id="mappluto_26v2",
        parquet_glob=tmp_path / "parcel_part_*.parquet",
    )

    assert previous == 0 and inserted == 1
    assert conn.execute("SELECT count(*) FROM standardized_record").fetchone()[0] == 1
    conn.close()


def test_standardized_lineage_is_idempotent_without_a_physical_primary_key() -> None:
    conn = duckdb.connect()
    schema = Path(__file__).parents[2] / "src" / "nyc_lens" / "schema.sql"
    conn.execute(schema.read_text(encoding="utf-8"))
    frame = pd.DataFrame([{"entity_id": "fixture_1"}, {"entity_id": "fixture_1"}])

    for _ in range(2):
        register_standardized_records(
            conn,
            frame,
            entity_type="fixture",
            entity_id_column="entity_id",
            source_id="fixture_source",
            snapshot_id="snap_fixture",
        )

    assert conn.execute("SELECT count(*) FROM standardized_record").fetchone()[0] == 1
    assert conn.execute(
        """
        SELECT count(*) FROM duckdb_constraints()
        WHERE table_name IN (
            'standardized_record', 'workforce_estimate', 'parcel',
            'census_estimate', 'inspection', 'transaction_case'
        )
          AND constraint_type='PRIMARY KEY'
        """
    ).fetchone()[0] == 0
    conn.close()
