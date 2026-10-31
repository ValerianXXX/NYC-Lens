"""Synthetic publication contracts, frozen before changing the storage layout."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import duckdb
import pandas as pd
import pytest

from nyc_lens import storage
from nyc_lens.util import sha256_file
from test_external_storage import _building_frame, SCHEMA_PATH


def frame(prefix="old", count=1, source="fixture_buildings"):
    rows = []
    for index in range(count):
        row = _building_frame(source).iloc[0].to_dict()
        row.update(building_id=f"{prefix}_{index}", snapshot_id=f"snapshot_{prefix}")
        rows.append(row)
    return pd.DataFrame(rows) if rows else _building_frame(source).iloc[0:0]


def publish(conn, data, source="fixture_buildings", region="nyc"):
    conn.register("_generation_incoming", data)
    try:
        return storage.replace_source_from_relation(conn, table_name="building", source_id=source,
                                                    relation="_generation_incoming", region=region)
    finally:
        conn.unregister("_generation_incoming")


def ids(conn):
    return [row[0] for row in conn.execute("SELECT building_id FROM building ORDER BY building_id").fetchall()]


def catalogs(conn):
    return [conn.execute("SELECT * FROM external_table_catalog WHERE table_name='building'").fetchall(),
            conn.execute("SELECT * FROM external_partition_catalog WHERE table_name='building' ORDER BY relative_path").fetchall()]


def active_paths(conn, home):
    return [home / row[0] for row in conn.execute(
        "SELECT relative_path FROM external_partition_catalog WHERE table_name='building'").fetchall()]


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("NYC_LENS_HOME", str(tmp_path))
    monkeypatch.delenv("NYC_LENS_DATABASE", raising=False)
    database = tmp_path / "data/catalog.duckdb"
    database.parent.mkdir()
    conn = duckdb.connect(str(database))
    conn.execute(storage.schema_sql_without_external_tables(SCHEMA_PATH.read_text(encoding="utf-8")))
    storage.ensure_external_storage(conn)
    publish(conn, frame())
    try:
        yield conn, database, tmp_path
    finally:
        conn.close()


def test_an_existing_reader_retains_old_rows_after_source_replacement(database):
    writer, path, _ = database
    reader = duckdb.connect(str(path))
    try:
        reader.execute("BEGIN")
        assert ids(reader) == ["old_0"]
        publish(writer, frame("new", 2))
        assert ids(reader) == ["old_0"]
        assert reader.execute("SELECT row_count FROM external_table_catalog WHERE table_name='building'").fetchone()[0] == 1
        reader.execute("COMMIT")
        assert ids(reader) == ["new_0", "new_1"]
    finally:
        reader.close()


def test_other_connections_never_see_new_view_with_old_catalog(database, monkeypatch):
    writer, path, _ = database
    reader = duckdb.connect(str(path))
    original = storage.rebuild_external_catalog
    observed = []
    def during_publish(*args, **kwargs):
        observed.append((ids(reader), reader.execute(
            "SELECT row_count FROM external_table_catalog WHERE table_name='building'").fetchone()[0]))
        return original(*args, **kwargs)
    monkeypatch.setattr(storage, "rebuild_external_catalog", during_publish)
    try:
        publish(writer, frame("new", 2))
        assert observed == [(["old_0"], 1)]
        assert ids(reader) == ["new_0", "new_1"]
    finally:
        reader.close()


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_failure_after_catalog_write_rolls_back_view_and_all_catalog_rows(database, monkeypatch, error_type):
    conn, _, _ = database
    before = catalogs(conn)
    original = storage.rebuild_external_catalog
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise error_type("SYNTHETIC_AFTER_CATALOG_FAILURE")
    monkeypatch.setattr(storage, "rebuild_external_catalog", fail)
    with pytest.raises(error_type, match="SYNTHETIC_AFTER_CATALOG_FAILURE"):
        publish(conn, frame("new", 2))
    assert ids(conn) == ["old_0"]
    assert catalogs(conn) == before


def test_old_partition_paths_and_bytes_are_retained(database):
    conn, _, home = database
    original = {path: sha256_file(path) for path in active_paths(conn, home)}
    publish(conn, frame("new", 2))
    assert all(path.is_file() and sha256_file(path) == digest for path, digest in original.items())
    assert set(original).isdisjoint(active_paths(conn, home))


def test_old_manifest_bytes_are_retained(database):
    conn, _, home = database
    location, digest = conn.execute("SELECT storage_root, manifest_sha256 FROM external_table_catalog WHERE table_name='building'").fetchone()
    manifest = home / location / "_manifest.json"
    assert sha256_file(manifest) == digest
    publish(conn, frame("new", 2))
    assert sha256_file(manifest) == digest
    assert json.loads(manifest.read_text(encoding="utf-8"))["row_count"] == 1


def test_empty_replacement_does_not_break_an_existing_reader_or_resurrect_rows(database):
    conn, path, _ = database
    reader = duckdb.connect(str(path))
    try:
        reader.execute("BEGIN")
        assert ids(reader) == ["old_0"]
        assert publish(conn, frame(count=0)) == (1, 0)
        assert ids(reader) == ["old_0"]
        reader.execute("COMMIT")
        storage.ensure_external_storage(conn)
        assert ids(conn) == []
        assert conn.execute("SELECT row_count FROM external_table_catalog WHERE table_name='building'").fetchone()[0] == 0
    finally:
        reader.close()


def test_other_sources_survive_replacement_and_failed_publication(database, monkeypatch):
    conn, _, _ = database
    publish(conn, frame("other", 1, "other_source"), source="other_source")
    publish(conn, frame("new", 2))
    assert ids(conn) == ["new_0", "new_1", "other_0"]
    before = catalogs(conn)
    def fail(*args, **kwargs):
        raise RuntimeError("SYNTHETIC_CATALOG_FAILURE")
    monkeypatch.setattr(storage, "rebuild_external_catalog", fail)
    with pytest.raises(RuntimeError):
        publish(conn, frame("bad", 3))
    assert ids(conn) == ["new_0", "new_1", "other_0"]
    assert catalogs(conn) == before


def test_source_moving_region_replaces_the_whole_source_once(database):
    conn, _, _ = database
    assert publish(conn, frame("new", 2), region="greater_nyc") == (1, 2)
    assert ids(conn) == ["new_0", "new_1"]
    assert conn.execute("SELECT DISTINCT region FROM external_partition_catalog WHERE table_name='building'").fetchall() == [("greater_nyc",)]


def test_duplicate_input_is_rejected_without_changing_the_active_generation(database):
    conn, _, home = database
    before = catalogs(conn)
    paths = {path: sha256_file(path) for path in active_paths(conn, home)}
    with pytest.raises(ValueError, match="duplicate ids"):
        publish(conn, pd.concat([frame("duplicate"), frame("duplicate")], ignore_index=True))
    assert ids(conn) == ["old_0"]
    assert catalogs(conn) == before
    assert all(sha256_file(path) == digest for path, digest in paths.items())


def test_failed_staging_is_not_adopted_by_initialization(database, monkeypatch):
    conn, _, _ = database
    original = storage._write_source_partitions
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("SYNTHETIC_STAGING_FAILURE")
    monkeypatch.setattr(storage, "_write_source_partitions", fail)
    with pytest.raises(RuntimeError):
        publish(conn, frame("orphan", 2))
    storage.ensure_external_storage(conn)
    assert ids(conn) == ["old_0"]


def test_initialization_does_not_promote_an_unregistered_parquet_file(database):
    conn, _, home = database
    rogue = home / "data/standardized/facts/building/rogue.parquet"
    conn.execute(f"COPY (SELECT * REPLACE ('rogue' AS building_id) FROM building) TO '{rogue.as_posix()}' (FORMAT PARQUET)")
    before = catalogs(conn)
    storage.ensure_external_storage(conn)
    assert ids(conn) == ["old_0"]
    assert catalogs(conn) == before


def test_commit_failure_retains_the_old_generation(database):
    conn, _, _ = database
    before = catalogs(conn)
    class RefusedCommit:
        def __getattr__(self, name):
            return getattr(conn, name)
        def execute(self, sql, *args, **kwargs):
            if sql.strip().upper() == "COMMIT":
                raise RuntimeError("SYNTHETIC_COMMIT_FAILURE")
            return conn.execute(sql, *args, **kwargs)
    with pytest.raises(RuntimeError, match="SYNTHETIC_COMMIT_FAILURE"):
        publish(RefusedCommit(), frame("uncommitted", 2))
    assert ids(conn) == ["old_0"]
    assert catalogs(conn) == before


def test_process_death_before_catalog_commit_keeps_the_previous_generation(database):
    conn, database_path, home = database
    before = catalogs(conn)
    conn.close()
    child = Path(__file__).parents[1] / "fixtures/external_generation_exit.py"
    result = subprocess.run([sys.executable, str(child), str(database_path)],
                            capture_output=True, text=True, timeout=60, env=dict(os.environ))
    assert result.returncode == 23, result.stderr
    reopened = duckdb.connect(str(database_path))
    try:
        assert ids(reopened) == ["old_0"]
        assert catalogs(reopened) == before
        storage.ensure_external_storage(reopened)
        assert ids(reopened) == ["old_0"]
        assert all(path.is_file() for path in active_paths(reopened, home))
    finally:
        reopened.close()
