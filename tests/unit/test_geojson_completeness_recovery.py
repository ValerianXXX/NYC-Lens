"""Real multi-page assembly, audit failures and malformed-response boundaries."""
import json
from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd
import pytest

import test_boundary_publication as fixtures
from test_boundary_publication import active_state, database, refresh, seed
from test_geojson_completeness import install_source
from nyc_lens.ingest import geojson, geographies
from nyc_lens.provenance import save_bytes_snapshot
from nyc_lens.util import sha256_file


def test_three_chunks_publish_each_expected_object_once_with_raw_lineage(database, monkeypatch):
    conn, root = database
    _, _, exports = seed(database, monkeypatch, "zcta")
    original = fixtures.input_frame
    def larger(kind, revision):
        frame = original(kind, revision)
        if kind == "zcta":
            frame = gpd.GeoDataFrame(pd.concat([frame] * 3, ignore_index=True).iloc[:201], crs=frame.crs)
            frame["OBJECTID"] = range(1, 202)
            frame["GEOID"] = [f"synthetic-{i}" for i in range(201)]
        return frame
    monkeypatch.setattr(fixtures, "input_frame", larger)
    fixtures.install_downloads(monkeypatch, "zcta", 2)
    result = refresh(conn, "zcta")
    assert result["bbox_features"] == result["nyc_intersecting_zcta"] == 201
    audit = json.loads(Path(result["completeness_check"]).read_text())
    assert audit["request_count"] == 5
    assert [item["role"] for item in audit["responses"]] == [
        "ids-before", "features-001", "features-002", "features-003", "ids-after"]
    pages = audit["responses"][1:4]
    assert [len(json.loads(Path(page["raw_path"]).read_text())["features"]) for page in pages] == [100, 100, 1]
    raw, query = conn.execute("SELECT raw_path,query_json FROM dataset_snapshot WHERE snapshot_id=?",
                             [result["snapshot_id"]]).fetchone()
    assert json.loads(query)["upstream_snapshot_ids"] == [page["snapshot_id"] for page in audit["responses"]]
    ids = [item["properties"]["OBJECTID"] for item in json.loads(Path(raw).read_text())["features"]]
    assert sorted(ids) == list(range(1, 202))
    assert all(sha256_file(path) == digest for path, digest in exports.items())
    assert conn.execute("SELECT request_count,downloaded_bytes FROM ingestion_run WHERE run_id=?",
                        [result["run_id"]]).fetchone() == (5, sum(page["byte_size"] for page in audit["responses"]))


@pytest.mark.parametrize("kind,variant,requests", [
    ("nta", "short", 3), ("hazard", "drift", 4),
    ("zcta", "id_drift", 3), ("zcta", "ids_error", 1),
])
def test_failed_run_counts_real_attempts_and_retained_response_bytes(database, monkeypatch, kind, variant, requests):
    conn, _ = database
    seed(database, monkeypatch, kind)
    install_source(monkeypatch, kind, variant, [])
    with pytest.raises(geojson.CompletenessError):
        refresh(conn, kind)
    run_id, actual_requests, size = conn.execute("SELECT run_id,request_count,downloaded_bytes FROM ingestion_run ORDER BY started_at DESC LIMIT 1").fetchone()
    assert actual_requests == requests
    assert size == conn.execute("SELECT sum(byte_size) FROM dataset_snapshot WHERE run_id=?", [run_id]).fetchone()[0]


@pytest.mark.parametrize("kind", ["nta", "hazard", "zcta"])
def test_failed_audit_write_never_publishes_or_overwrites_a_receipt(database, monkeypatch, kind):
    conn, _ = database
    _, baseline, exports = seed(database, monkeypatch, kind)
    original = Path.open
    attempts = []
    def fail(path, mode="r", *args, **kwargs):
        if path.name == "completeness-check.json" and mode == "x":
            attempts.append(path)
            raise OSError("synthetic receipt storage unavailable")
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", fail)
    with pytest.raises(OSError, match="receipt storage unavailable"):
        refresh(conn, kind)
    assert len(attempts) == 1
    assert active_state(conn) == baseline
    assert all(sha256_file(path) == digest for path, digest in exports.items())


@pytest.mark.parametrize("content", [b'{"count":1,"count":2}', b'[{"count":NaN}]', b'not json', b'\xff'])
def test_malformed_json_is_retained_and_rejected(database, monkeypatch, content):
    conn, root = database
    _, baseline, _ = seed(database, monkeypatch, "hazard")
    def get(url, **kwargs):
        return httpx.Response(200, content=content, request=httpx.Request("GET", url))
    monkeypatch.setattr(geojson.httpx, "get", get)
    with pytest.raises(geojson.CompletenessError, match="COMPLETENESS_INVALID_JSON"):
        refresh(conn, "hazard")
    assert active_state(conn) == baseline
    assert any(path.read_bytes() == content for path in root.rglob("count-before.json"))


@pytest.mark.parametrize("failure", ["http_error", "timeout_after_geometry"])
def test_upstream_failure_preserves_data_and_has_no_automatic_retry(database, monkeypatch, failure):
    conn, root = database
    _, baseline, _ = seed(database, monkeypatch, "hazard")
    original = geojson.httpx.get
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        if failure == "http_error":
            return httpx.Response(503, content=b'synthetic service unavailable', request=httpx.Request("GET", url))
        if len(calls) == 2:
            raise httpx.ReadTimeout("synthetic timeout")
        return original(url, **kwargs)
    monkeypatch.setattr(geojson.httpx, "get", get)
    with pytest.raises((geojson.CompletenessError, httpx.ReadTimeout)):
        refresh(conn, "hazard")
    assert len(calls) == (1 if failure == "http_error" else 2)
    assert active_state(conn) == baseline
    row = conn.execute("SELECT status,request_count FROM ingestion_run ORDER BY started_at DESC LIMIT 1").fetchone()
    assert row == ("FAILED", 2 if failure == "http_error" else 4)


def test_downloaded_empty_geometry_cannot_silently_disappear_above_floor(database, monkeypatch):
    conn, _ = database
    _, baseline, _ = seed(database, monkeypatch, "nta")
    frame = fixtures.input_frame("nta", 2)
    payload = json.loads(frame.to_json())
    payload["features"][0]["geometry"]["coordinates"] = [[]]
    def download(conn, *, source_id, vintage, filename, run_id, **kwargs):
        return save_bytes_snapshot(conn, source_id=source_id, vintage=vintage, filename=filename,
            run_id=run_id, source_url="synthetic://empty-geometry", content=json.dumps(payload).encode())
    monkeypatch.setattr(geographies, "download_snapshot", download)
    with pytest.raises(geojson.CompletenessError, match="COMPLETENESS_GEOMETRY_ROWS_LOST"):
        refresh(conn, "nta")
    assert active_state(conn) == baseline
