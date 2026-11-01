"""Keep logical catalog paths separate from native Windows file addressing."""
from pathlib import Path

import pytest

from nyc_lens import storage
from test_external_storage_long_paths import fixture_os_path, long_database


@pytest.mark.parametrize('relative', [
    '../outside.parquet', 'data/standardized/facts/../outside.parquet',
    '/outside.parquet', 'C:/outside.parquet', 'data\\standardized\\facts\\outside.parquet',
    '//?/C:/outside.parquet', 'reports/outside.parquet',
])
def test_native_addressing_does_not_expand_the_catalog_boundary(tmp_path, monkeypatch, relative):
    monkeypatch.setenv('NYC_LENS_HOME', str(tmp_path))
    with pytest.raises(ValueError, match='EXTERNAL_CATALOG_PATH_INVALID'):
        storage._catalog_file(relative)


def test_long_legacy_discovery_returns_logical_paths_and_readable_files(long_database):
    conn, _, home = long_database
    logical_root = home / ('legacy-export-' + 'x' * 65)
    seed = storage._ensure_empty_parquet(conn, storage.EXTERNAL_TABLE_SPECS['building'], root=logical_root)
    assert len(str(seed)) > 260
    files = storage.external_parquet_files('building', root=logical_root)
    assert files == [seed]
    assert not str(files[0]).startswith('\\\\?\\')
    assert fixture_os_path(seed).is_file()
    assert conn.execute(f'SELECT count(*) FROM {storage._read_parquet_expression(files)}').fetchone() == (0,)
