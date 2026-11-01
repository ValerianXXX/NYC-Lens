"""Actual local crash recovery and unchanged source gates; synthetic data only."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

import duckdb
import geopandas as gpd
import pytest

import test_multisnapshot_publication as fixture
from test_multisnapshot_publication import (
    COUNTIES, SOURCES, ProbeConnection, active_state, database, install_downloads, refresh, seed,
)
from nyc_lens.ingest import census, fema, multisnapshot
from nyc_lens.provenance import save_bytes_snapshot
from nyc_lens.util import sha256_file


@pytest.mark.parametrize("kind", SOURCES)
def test_manifest_failure_does_not_publish_prepared_export(database, monkeypatch, kind):
    conn, _ = database
    _, baseline, exports = seed(database, monkeypatch, kind)
    def fail(*_args, **_kwargs):
        raise OSError("synthetic manifest disk failure")
    monkeypatch.setattr(multisnapshot, "save_bytes_snapshot", fail)
    with pytest.raises(OSError, match="synthetic manifest disk failure"):
        refresh(conn, kind)
    assert active_state(conn) == baseline
    assert all(sha256_file(path) == digest for path, digest in exports.items())
    assert conn.execute("SELECT status FROM ingestion_run ORDER BY started_at DESC LIMIT 1").fetchone() == ("FAILED",)


@pytest.mark.parametrize("kind", SOURCES)
def test_actual_owned_process_exit_before_commit_keeps_old_publication(database, monkeypatch, kind):
    conn, root = database
    _, baseline, exports = seed(database, monkeypatch, kind)
    conn.close()
    script = Path(__file__).resolve().parents[1] / "fixtures/multisnapshot_publication_exit.py"
    result = subprocess.run([sys.executable, str(script), str(root), kind], capture_output=True,
        text=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 83, result.stderr
    with duckdb.connect(str(root / "synthetic.duckdb")) as reader:
        assert active_state(reader) == baseline
        assert reader.execute("SELECT status FROM ingestion_run ORDER BY started_at DESC LIMIT 1").fetchone() == ("INDETERMINATE",)
        # Both attempts' raw sources and locally derived receipts survive.
        assert reader.execute("SELECT count(*) FROM dataset_snapshot WHERE source_id=?", [SOURCES[kind]]).fetchone() == ((10 if kind == "tiger" else 284),)
        assert reader.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name='_fema_sfha_staging'").fetchone() == (0,)
        # An explicit new attempt succeeds; the crashed attempt is never relabelled.
        install_downloads(monkeypatch, kind, 2)
        retry = refresh(reader, kind)
        assert reader.execute("SELECT status FROM ingestion_run ORDER BY started_at").fetchall() == [
            ("SUCCEEDED" if kind == "tiger" else "SUCCEEDED_WITH_WARNINGS",),
            ("INDETERMINATE",),
            ("SUCCEEDED" if kind == "tiger" else "SUCCEEDED_WITH_WARNINGS",),
        ]
        assert Path(retry["publication_manifest"]).is_file()
    assert all(sha256_file(path) == digest for path, digest in exports.items())


def test_verified_tiger_cache_redownloads_only_changed_raw_file(database, monkeypatch):
    conn, _ = database
    install_downloads(monkeypatch, "tiger", 1, force_tiger_download=False)
    first = refresh(conn, "tiger")
    replaced = first["snapshots"][0]
    path = Path(conn.execute("SELECT raw_path FROM dataset_snapshot WHERE snapshot_id=?", [replaced]).fetchone()[0])
    # Deliberately damage a synthetic cache file; do not update its stored hash.
    path.write_bytes(b"synthetic damaged archive")
    calls = install_downloads(monkeypatch, "tiger", 2, force_tiger_download=False)
    second = refresh(conn, "tiger")
    assert len(calls) == 1 and second["reused_snapshots"] == 3
    assert replaced not in second["snapshots"]
    assert set(first["snapshots"]) & set(second["snapshots"]) == set(first["snapshots"]) - {replaced}
    requests, downloaded = conn.execute("SELECT request_count,downloaded_bytes FROM ingestion_run WHERE run_id=?", [second["run_id"]]).fetchone()
    assert requests == 1 and downloaded > 0


@pytest.mark.parametrize("kind", SOURCES)
def test_original_county_and_polygon_floors_still_reject(database, monkeypatch, kind):
    conn, _ = database
    _, baseline, exports = seed(database, monkeypatch, kind)
    module = census if kind == "tiger" else fema
    original_download = module.download_snapshot
    def incomplete(conn, **kwargs):
        filename = kwargs["filename"]
        params = kwargs.get("params") or {}
        if (kind == "fema" and params.get("geometry") == "-74.0,40.0,-73.8,40.2"
                and params.get("returnCountOnly") == "true"):
            # A complete 9,999-record response must still fail the unchanged
            # regional floor, independently of the new membership checks.
            content, media = b'{"count":9999}', "application/json"
        elif (kind == "fema" and params.get("geometry") == "-74.0,40.0,-73.8,40.2"
                and params.get("returnIdsOnly") == "true"):
            content = json.dumps({"objectIdFieldName": "OBJECTID", "objectIds": list(range(1, 10_000))}).encode()
            media = "application/json"
        elif kind == "fema" and filename == f"sfha_{COUNTIES[0]}_0009500.geojson":
            payload = json.loads(fixture.fema_page(9500, 2))
            payload["features"].pop()
            content = json.dumps(payload).encode()
            media = "application/geo+json"
        elif kind == "tiger" and filename == f"tl_2024_{COUNTIES[0][:2]}_bg.zip":
            frame = gpd.read_file(io.BytesIO(fixture.tiger_zip(COUNTIES[0][:2], 2)))
            frame = frame.loc[frame["COUNTYFP"] != COUNTIES[0][2:]].copy()
            with tempfile.TemporaryDirectory(prefix="nyc-synthetic-missing-county-") as directory:
                root = Path(directory)
                frame.to_file(root / "synthetic.shp")
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w") as archive:
                    for path in root.iterdir():
                        archive.writestr(path.name, path.read_bytes())
                content = buffer.getvalue()
            media = "application/zip"
        else:
            return original_download(conn, **kwargs)
        return save_bytes_snapshot(conn, source_id=kwargs["source_id"], vintage=kwargs["vintage"],
            filename=filename, content=content, source_url=kwargs["url"], run_id=kwargs["run_id"],
            content_type=media, query={"synthetic": True, "incomplete": True})
    monkeypatch.setattr(module, "download_snapshot", incomplete)
    with pytest.raises(ValueError, match="coverage mismatch|coverage is unexpectedly low: 9999"):
        refresh(conn, kind)
    assert active_state(conn) == baseline
    assert all(sha256_file(path) == digest for path, digest in exports.items())


def test_fema_page_staging_failure_cleans_up_for_explicit_retry(database, monkeypatch):
    conn, _ = database
    _, baseline, exports = seed(database, monkeypatch, "fema")
    def fail(statement):
        if "INSERT INTO _fema_sfha_staging" in statement:
            raise OSError("synthetic page staging failure")
    with pytest.raises(OSError, match="synthetic page staging failure"):
        refresh(ProbeConnection(conn, before=fail), "fema")
    assert active_state(conn) == baseline
    assert conn.execute("SELECT count(*) FROM duckdb_views() WHERE view_name='_fema_page_boundaries'").fetchone() == (0,)
    assert conn.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name='_fema_sfha_staging'").fetchone() == (0,)
    retry = refresh(conn, "fema")
    assert retry["standardized_sfha_polygons"] == 10_000
    assert all(sha256_file(path) == digest for path, digest in exports.items())
