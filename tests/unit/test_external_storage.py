from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from nyc_lens.storage import ensure_external_storage, replace_source_from_relation, schema_sql_without_external_tables
from nyc_lens.storage_migration import compact_catalog_database, migrate_to_external_storage


SCHEMA_PATH = Path(__file__).parents[2] / "src" / "nyc_lens" / "schema.sql"


def _building_frame(source_id: str = "fixture_buildings") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "building_id": "building_1",
                "bin": "1000001",
                "bbl": "1000010001",
                "name": "Fixture",
                "address": "1 TEST STREET",
                "min_longitude": -74.0,
                "min_latitude": 40.7,
                "max_longitude": -73.999,
                "max_latitude": 40.701,
                "geom_wkt": "POLYGON ((-74 40.7, -73.999 40.7, -73.999 40.701, -74 40.7))",
                "attributes_json": "{}",
                "source_id": source_id,
                "snapshot_id": "snap_building",
                "retrieved_at": datetime(2026, 8, 29),
                "valid_from": date(2026, 1, 1),
                "valid_to": None,
                "geography_type": "building",
                "geography_id": "1000001",
                "measure_type": "administrative_record",
                "confidence": 0.98,
                "method_version": "0.2.0",
            }
        ]
    )


def _property_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "property_record_id": "property_1",
                "parcel_id": "parcel_1000010001",
                "record_type": "sale",
                "record_date": date(2026, 1, 2),
                "amount": 1_000_000.0,
                "attributes_json": "{}",
                "source_id": "fixture_property",
                "snapshot_id": "snap_property",
                "retrieved_at": datetime(2026, 8, 29),
                "valid_from": date(2026, 1, 2),
                "valid_to": None,
                "geography_type": "tax_lot",
                "geography_id": "1000010001",
                "measure_type": "administrative_record",
                "confidence": 0.95,
                "method_version": "0.1.0",
            }
        ]
    )


def _regulatory_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "regulatory_record_id": "regulatory_1",
                "parcel_id": "parcel_1000010001",
                "record_type": "permit",
                "status": "issued",
                "issued_at": date(2026, 1, 3),
                "closed_at": None,
                "attributes_json": "{}",
                "source_id": "fixture_regulatory",
                "snapshot_id": "snap_regulatory",
                "retrieved_at": datetime(2026, 8, 29),
                "valid_from": date(2026, 1, 3),
                "valid_to": None,
                "geography_type": "tax_lot",
                "geography_id": "1000010001",
                "measure_type": "administrative_record",
                "confidence": 0.95,
                "method_version": "0.1.0",
            }
        ]
    )


def _insert_frame(conn: duckdb.DuckDBPyConnection, table_name: str, frame: pd.DataFrame) -> None:
    relation = f"_{table_name}_fixture"
    conn.register(relation, frame)
    conn.execute(f"INSERT INTO {table_name} BY NAME SELECT * FROM {relation}")
    conn.unregister(relation)


def test_external_source_replace_preserves_table_name_and_catalog(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NYC_LENS_HOME", str(tmp_path))
    (tmp_path / "data").mkdir()
    conn = duckdb.connect(str(tmp_path / "data" / "catalog.duckdb"))
    conn.execute(schema_sql_without_external_tables(SCHEMA_PATH.read_text(encoding="utf-8")))
    status = ensure_external_storage(conn)
    assert status["building"] == "external_parquet_view"
    frame = _building_frame()
    conn.register("_incoming", frame)
    previous, inserted = replace_source_from_relation(
        conn,
        table_name="building",
        source_id="fixture_buildings",
        relation="_incoming",
    )
    conn.unregister("_incoming")
    assert previous == 0 and inserted == 1
    assert conn.execute("SELECT count(*) FROM building").fetchone()[0] == 1
    assert conn.execute("SELECT table_type FROM information_schema.tables WHERE table_name='building'").fetchone()[0] == "VIEW"
    assert conn.execute("SELECT row_count FROM external_table_catalog WHERE table_name='building'").fetchone()[0] == 1
    assert list((tmp_path / "data" / "standardized" / "facts" / "building").rglob("part-*.parquet"))
    conn.close()


def test_full_storage_migration_swaps_only_after_verification(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NYC_LENS_HOME", str(tmp_path))
    database = tmp_path / "data" / "nyc_lens.duckdb"
    database.parent.mkdir(parents=True)
    conn = duckdb.connect(str(database))
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    _insert_frame(conn, "building", _building_frame())
    _insert_frame(conn, "property_record", _property_frame())
    _insert_frame(conn, "regulatory_record", _regulatory_frame())
    conn.execute(
        """
        INSERT INTO standardized_record VALUES (
            'std_fixture', 'building', 'building_1', 'fixture_buildings',
            'snap_building', 'building_1', '0.2.0', TIMESTAMP '2026-08-29'
        )
        """
    )
    conn.close()

    result = migrate_to_external_storage()

    assert result["status"] == "SUCCEEDED"
    assert result["retired_materialized_backup_deleted"] is True
    assert not list(database.parent.glob("nyc_lens.pre_a.*.duckdb"))
    migrated = duckdb.connect(str(database), read_only=True)
    assert migrated.execute("SELECT count(*) FROM building").fetchone()[0] == 1
    assert migrated.execute("SELECT count(*) FROM property_record").fetchone()[0] == 1
    assert migrated.execute("SELECT count(*) FROM regulatory_record").fetchone()[0] == 1
    assert migrated.execute("SELECT count(*) FROM standardized_record").fetchone()[0] == 1
    assert migrated.execute("SELECT count(*) FROM external_table_catalog").fetchone()[0] == 5
    assert {
        row[0]
        for row in migrated.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_type='VIEW'"
        ).fetchall()
    } >= {"building", "property_record", "regulatory_record", "poi", "workforce_estimate"}
    migrated.close()

    compacted = compact_catalog_database()
    assert compacted["status"] == "SUCCEEDED"
    assert compacted["retired_index_bloated_backup_deleted"] is True
    assert compacted["standardized_record_duplicate_ids"] == 0
    assert compacted["standardized_record_physical_primary_keys"] == 0
    assert compacted["workforce_estimate_duplicate_ids"] == 0
    assert compacted["workforce_estimate_physical_primary_keys"] == 0
    assert all(
        details["duplicate_ids"] == 0 and details["physical_primary_keys"] == 0
        for details in compacted["compacted_logical_key_checks"].values()
    )
    assert not list(database.parent.glob("nyc_lens.pre_compact.*.duckdb"))
    assert compacted["temporary_spill_directory_deleted"] is True
    assert not list((database.parent / "cache" / "duckdb_temp").glob("compact_*"))
