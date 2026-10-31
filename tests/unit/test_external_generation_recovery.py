"""Additional synthetic migration, writer-conflict and operator-restore checks."""
import json
from pathlib import Path
import shutil

import duckdb
import pytest

from nyc_lens import storage
from nyc_lens.publication import publication_transaction
from nyc_lens.util import sha256_file
from test_external_generation_contract import database, frame, publish, ids, catalogs, active_paths
from test_external_storage import SCHEMA_PATH


def test_unregistered_files_are_ignored_even_before_first_source_publication(tmp_path, monkeypatch):
    monkeypatch.setenv("NYC_LENS_HOME", str(tmp_path))
    with duckdb.connect(":memory:") as conn:
        conn.execute(storage.schema_sql_without_external_tables(SCHEMA_PATH.read_text(encoding="utf-8")))
        storage.ensure_external_storage(conn)
        conn.register("_rogue", frame("rogue"))
        rogue = tmp_path / "data/standardized/facts/building/rogue.parquet"
        conn.execute("COPY _rogue TO ? (FORMAT PARQUET)", [str(rogue)])
        storage.ensure_external_storage(conn)
        assert ids(conn) == []
        assert conn.execute("SELECT count(*) FROM external_table_catalog").fetchone()[0] == 5


def test_legacy_layout_is_imported_once_and_retained(database):
    conn, _, home = database
    original = active_paths(conn, home)[0]
    legacy = home / "data/standardized/facts/building/region=nyc/source=fixture_buildings/year=2026/part-00000.parquet"
    legacy.parent.mkdir(parents=True)
    shutil.copyfile(original, legacy)
    conn.execute("DELETE FROM external_partition_catalog WHERE table_name='building'")
    conn.execute("DELETE FROM external_table_catalog WHERE table_name='building'")
    storage.ensure_external_storage(conn)
    assert active_paths(conn, home) == [legacy]
    digest = sha256_file(legacy)
    publish(conn, frame("new", 2))
    storage.ensure_external_storage(conn)
    assert ids(conn) == ["new_0", "new_1"]
    assert sha256_file(legacy) == digest


def test_legacy_manifest_is_linked_without_overwriting_it(database):
    conn, _, home = database
    root, digest = conn.execute("SELECT storage_root, manifest_sha256 FROM external_table_catalog WHERE table_name='building'").fetchone()
    legacy_root = home / "data/standardized/facts/building"
    legacy_manifest = legacy_root / "_manifest.json"
    shutil.copyfile(home / root / "_manifest.json", legacy_manifest)
    conn.execute("UPDATE external_table_catalog SET storage_root=?, layout_version='1.0.0' WHERE table_name='building'",
                 [legacy_root.relative_to(home).as_posix()])
    publish(conn, frame("new"))
    new_root = conn.execute("SELECT storage_root FROM external_table_catalog WHERE table_name='building'").fetchone()[0]
    manifest = json.loads((home / new_root / "_manifest.json").read_text())
    assert manifest["previous_manifest"] == {"relative_path": legacy_manifest.relative_to(home).as_posix(), "sha256": digest}
    assert sha256_file(legacy_manifest) == digest


def test_conflicting_writer_does_not_displace_the_committed_winner(database, monkeypatch):
    conn, path, _ = database
    other = duckdb.connect(str(path))
    original = storage._write_source_partitions
    injected = False
    def overlap(*args, **kwargs):
        nonlocal injected
        result = original(*args, **kwargs)
        if not injected:
            injected = True
            publish(other, frame("winner", 3))
        return result
    monkeypatch.setattr(storage, "_write_source_partitions", overlap)
    try:
        with pytest.raises(duckdb.TransactionException):
            publish(conn, frame("loser", 2))
        assert ids(conn) == ["winner_0", "winner_1", "winner_2"]
        assert conn.execute("SELECT row_count FROM external_table_catalog WHERE table_name='building'").fetchone()[0] == 3
        storage.ensure_external_storage(conn)
        assert ids(conn) == ids(other)
    finally:
        other.close()


def test_direct_caller_transaction_is_not_silently_committed(database):
    conn, _, _ = database
    before = catalogs(conn)
    conn.execute("BEGIN")
    conn.execute("CREATE TABLE unrelated (id INT)")
    with pytest.raises(duckdb.TransactionException):
        publish(conn, frame("new"))
    conn.execute("ROLLBACK")
    assert storage.table_kind(conn, "unrelated") is None
    assert catalogs(conn) == before
    assert ids(conn) == ["old_0"]


def test_swallowed_nested_failure_still_rolls_back_the_entire_publication(database, monkeypatch):
    conn, _, _ = database
    before = catalogs(conn)
    original = storage.rebuild_external_catalog
    def fail_after_catalog(*args, **kwargs):
        original(*args, **kwargs)
        raise ValueError("synthetic late failure")
    monkeypatch.setattr(storage, "rebuild_external_catalog", fail_after_catalog)
    with pytest.raises(RuntimeError, match="PUBLICATION_ABORTED"):
        with publication_transaction(conn):
            try:
                publish(conn, frame("new", 2))
            except ValueError:
                pass
    assert catalogs(conn) == before
    assert ids(conn) == ["old_0"]


@pytest.mark.parametrize("fault", ["missing", "resized", "manifest"])
def test_damaged_active_generation_prevents_publication_without_rebinding(database, fault):
    conn, _, home = database
    before = catalogs(conn)
    path = active_paths(conn, home)[0]
    if fault == "manifest":
        root = conn.execute("SELECT storage_root FROM external_table_catalog WHERE table_name='building'").fetchone()[0]
        (home / root / "_manifest.json").write_text("{}")
    elif fault == "resized":
        with path.open("ab") as stream:
            stream.write(b"synthetic damage")
    else:
        path.rename(path.with_suffix(".absent"))
    with pytest.raises((ValueError, duckdb.Error)):
        publish(conn, frame("new"))
    assert catalogs(conn) == before


def test_closed_catalog_backup_restores_prior_rows_and_catalog_without_deleting_generations(database):
    conn, path, home = database
    before = catalogs(conn)
    old_files = {item: sha256_file(item) for item in active_paths(conn, home)}
    conn.execute("CHECKPOINT")
    conn.close()
    backup = path.with_name("synthetic-reviewed-backup.duckdb")
    shutil.copyfile(path, backup)
    with duckdb.connect(str(path)) as writer:
        publish(writer, frame("new", 2))
        newer = active_paths(writer, home)
        assert ids(writer) == ["new_0", "new_1"]
    # Both connections are closed before replacing this synthetic catalog.
    shutil.copyfile(backup, path)
    with duckdb.connect(str(path)) as restored:
        storage.ensure_external_storage(restored)
        assert ids(restored) == ["old_0"]
        assert catalogs(restored) == before
    assert all(sha256_file(item) == digest for item, digest in old_files.items())
    assert all(item.is_file() for item in newer)
