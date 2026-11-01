"""Long local paths must preserve publication, rollback and provenance contracts."""
import hashlib
import json
import os
from pathlib import Path

import duckdb
import pytest

from nyc_lens import storage
from nyc_lens.publication_readiness import require_ready_external_publications
from nyc_lens.util import sha256_file
from test_external_generation_contract import catalogs, frame, ids, publish
from test_external_storage import SCHEMA_PATH


def fixture_os_path(path):
    # Independently address actual fixture bytes on Windows, including files
    # which the application could not access before this regression was added.
    return Path('\\\\?\\' + str(path)) if os.name == 'nt' else path


@pytest.fixture(params=['ascii', "材料's folder"])
def long_database(tmp_path, monkeypatch, request):
    parent = tmp_path / request.param
    home = parent / ('h' * max(1, 180 - len(str(parent)) - 1))
    assert 180 <= len(str(home)) < 220
    home.mkdir(parents=True)
    monkeypatch.setenv('NYC_LENS_HOME', str(home))
    monkeypatch.delenv('NYC_LENS_DATABASE', raising=False)
    path = home / 'catalog.duckdb'
    with duckdb.connect(str(path)) as conn:
        conn.execute(storage.schema_sql_without_external_tables(SCHEMA_PATH.read_text(encoding='utf-8')))
        storage.ensure_external_storage(conn)
        yield conn, path, home


def partition_bytes(conn, home):
    rows = conn.execute("SELECT relative_path, sha256 FROM external_partition_catalog WHERE table_name='building'").fetchall()
    found = {}
    for relative, digest in rows:
        assert relative.startswith('data/') and '\\' not in relative and '..' not in Path(relative).parts
        logical = home / relative
        assert len(str(logical)) > 320
        raw = fixture_os_path(logical).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == digest == sha256_file(logical)
        found[relative] = raw
    assert found
    return found


def test_long_generation_replacement_preserves_reader_hashes_and_source_proof(long_database):
    writer, path, home = long_database
    assert publish(writer, frame('old')) == (0, 1)
    before = partition_bytes(writer, home)
    old_root, old_digest = writer.execute("SELECT storage_root, manifest_sha256 FROM external_table_catalog WHERE table_name='building'").fetchone()
    old_manifest = fixture_os_path(home / old_root / '_manifest.json').read_bytes()
    with duckdb.connect(str(path)) as reader:
        reader.execute('BEGIN')
        assert ids(reader) == ['old_0']
        assert publish(writer, frame('new', 2), region='greater_nyc') == (1, 2)
        assert ids(reader) == ['old_0']
        reader.execute('COMMIT')
        assert ids(reader) == ['new_0', 'new_1']
    after = partition_bytes(writer, home)
    assert set(before).isdisjoint(after)
    assert all(fixture_os_path(home / relative).read_bytes() == raw for relative, raw in before.items())
    assert fixture_os_path(home / old_root / '_manifest.json').read_bytes() == old_manifest
    current_root = writer.execute("SELECT storage_root FROM external_table_catalog WHERE table_name='building'").fetchone()[0]
    manifest = json.loads(fixture_os_path(home / current_root / '_manifest.json').read_text(encoding='utf-8'))
    assert manifest['previous_manifest'] == {'relative_path': old_root + '/_manifest.json', 'sha256': old_digest}
    with pytest.raises(ValueError, match='EXTERNAL_PUBLICATION_NOT_READY'):
        require_ready_external_publications(writer)
    writer.execute("""INSERT INTO dataset_snapshot(snapshot_id, source_id, run_id, retrieved_at, status, vintage, source_url)
        VALUES ('snapshot_new','fixture_buildings','fixture_run',TIMESTAMP '2026-10-30','SUCCEEDED','SYNTHETIC','synthetic://long-path')""")
    writer.execute("""INSERT INTO ingestion_run(run_id,source_id,status,completed_at,command,started_at)
        VALUES ('fixture_run','fixture_buildings','SUCCEEDED',TIMESTAMP '2026-10-31','synthetic test',TIMESTAMP '2026-10-29')""")
    require_ready_external_publications(writer)
    storage.ensure_external_storage(writer)
    assert ids(writer) == ['new_0', 'new_1']


def test_long_generation_failed_publication_retains_prior_catalog_and_bytes(long_database, monkeypatch):
    conn, _, home = long_database
    publish(conn, frame('old'))
    before, saved = catalogs(conn), partition_bytes(conn, home)
    original = storage.rebuild_external_catalog
    def fail_after_catalog(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('SYNTHETIC_AFTER_LONG_CATALOG')
    monkeypatch.setattr(storage, 'rebuild_external_catalog', fail_after_catalog)
    with pytest.raises(RuntimeError, match='SYNTHETIC_AFTER_LONG_CATALOG'):
        publish(conn, frame('uncommitted', 2), region='greater_nyc')
    assert ids(conn) == ['old_0'] and catalogs(conn) == before
    assert partition_bytes(conn, home) == saved
    storage.ensure_external_storage(conn)
    assert ids(conn) == ['old_0']


def test_long_quoted_parquet_path_round_trips_through_real_duckdb(long_database):
    conn, _, home = long_database
    destination = home / ("quoted's-" + 'p' * 70) / '材料.parquet'
    assert len(str(destination)) > 260
    fixture_os_path(destination.parent).mkdir(parents=True)
    supplied = storage._sql_path(destination)
    conn.execute(f"COPY (SELECT 42 AS value) TO '{supplied}' (FORMAT PARQUET)")
    assert conn.execute(f"SELECT * FROM read_parquet('{supplied}')").fetchall() == [(42,)]
    assert hashlib.sha256(fixture_os_path(destination).read_bytes()).hexdigest() == sha256_file(destination)
