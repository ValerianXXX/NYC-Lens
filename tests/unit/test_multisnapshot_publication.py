"""Frozen multi-file publication contracts; every input geometry is synthetic.

Real ZIP/shapefile and GeoJSON parsing, 30 configured county identifiers, four
states and the unchanged 10,000-polygon FEMA minimum run through both adapters.
Only transport is replaced; these are not official coverage or city findings.
"""
from functools import lru_cache
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

import duckdb
import geopandas as gpd
import pytest
from shapely.geometry import box, mapping

from nyc_lens.db import SCHEMA_PATH
from nyc_lens.ingest import census, fema
from nyc_lens.ingest.common import GREATER_NYC_COUNTY_FIPS, GREATER_NYC_STATE_FIPS
from nyc_lens.provenance import save_bytes_snapshot
from nyc_lens.util import sha256_file
from test_socrata_publication import ProbeConnection


SOURCES = {"tiger": "tiger_bg_2024", "fema": "fema_nfhl_special_flood_hazard_areas"}
COUNTIES = sorted(GREATER_NYC_COUNTY_FIPS)


@lru_cache(maxsize=8)
def tiger_zip(state, revision):
    rows = []
    for index, county in enumerate(COUNTIES):
        if county[:2] != state:
            continue
        west = -74 + index * 0.02 + revision * 0.0001
        rows.append({"GEOID": county + "0001001", "STATEFP": state,
                     "COUNTYFP": county[2:], "TRACTCE": "000100", "BLKGRPCE": "1",
                     "NAMELSAD": f"Synthetic revision {revision}",
                     "geometry": box(west, 40.5, west + 0.01, 40.51)})
    with tempfile.TemporaryDirectory(prefix="nyc-synthetic-tiger-") as directory:
        root = Path(directory)
        gpd.GeoDataFrame(rows, crs="EPSG:4326").to_file(root / "synthetic.shp")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.iterdir()):
                archive.writestr(path.name, path.read_bytes())
        return output.getvalue()


@lru_cache(maxsize=40)
def fema_page(offset, revision):
    features = []
    for index in range(offset, offset + 500):
        west = -73.99 + (index % 100) * 0.001 + revision * 0.00001
        south = 40.01 + (index // 100) * 0.001
        features.append({"type": "Feature", "properties": {
            "OBJECTID": index + 1, "GlobalID": f"synthetic-sfha-{index:05d}",
            "FLD_ZONE": "AE", "ZONE_SUBTY": f"Synthetic revision {revision}", "SFHA_TF": "T",
        }, "geometry": mapping(box(west, south, west + 0.0007, south + 0.0007))})
    return json.dumps({"type": "FeatureCollection", "features": features}).encode()


def seed_counties(conn):
    for index, county in enumerate(COUNTIES):
        west, south, size = (-74, 40, 0.2) if index == 0 else (-73.7 + index * 0.02, 40.5, 0.01)
        geometry = box(west, south, west + size, south + size)
        conn.execute("""INSERT INTO geo_boundary
            (boundary_id,geography_type,geography_id,name,geom_wkt,source_id,snapshot_id,retrieved_at,method_version,
             min_longitude,min_latitude,max_longitude,max_latitude)
            VALUES (?, 'county_equivalent_2024', ?, 'Synthetic county only', ?,
                    'tiger_county_2024', 'synthetic-counties-only', now(), 'synthetic-fixture', ?, ?, ?, ?)""",
            [f"synthetic-county-{county}", county, geometry.wkt, *geometry.bounds])
        conn.execute("""INSERT INTO region_member
            (region_member_id,region_code,region_name,geography_type,geography_id,
             geography_name,state_fips,state_abbr,source_id,snapshot_id,retrieved_at,method_version)
            VALUES (?, 'CSA408', 'Synthetic region', 'county_equivalent_2024', ?,
                    'Synthetic county', ?, 'XX', 'synthetic-fixture',
                    'synthetic-counties-only', now(), 'synthetic-fixture')""",
                     [f"synthetic-member-{county}", county, county[:2]])


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("NYC_LENS_HOME", str(tmp_path))
    monkeypatch.delenv("NYC_LENS_DATABASE", raising=False)
    shutil.copytree(Path(__file__).resolve().parents[2] / "config", tmp_path / "config")
    conn = duckdb.connect(str(tmp_path / "synthetic.duckdb"))
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    for source in SOURCES.values():
        conn.execute("""INSERT INTO source_registry
            (source_id,name,steward,source_url,config_json,registered_at)
            VALUES (?, 'Synthetic only', 'Test fixture', 'synthetic://fixture', '{}', now())""", [source])
    seed_counties(conn)
    try:
        yield conn, tmp_path
    finally:
        conn.close()


def install_downloads(monkeypatch, kind, revision, *, force_tiger_download=True):
    calls = []
    def download(conn, *, source_id, vintage, url, filename, run_id, params=None, **_kwargs):
        calls.append(filename)
        if kind == "tiger":
            content = tiger_zip(filename.split("_")[2], revision)
            media = "application/zip"
        elif filename == "nfhl_layer_28_metadata.json":
            content, media = b'{"synthetic":true,"maxRecordCount":500}', "application/json"
        elif filename.startswith("count_"):
            count = 10_000 if filename == f"count_{COUNTIES[0]}.json" else 0
            content, media = json.dumps({"count": count}).encode(), "application/json"
        else:
            content, media = fema_page(int(params["resultOffset"]), revision), "application/geo+json"
        return save_bytes_snapshot(conn, source_id=source_id, vintage=vintage,
            filename=filename, content=content, source_url=url, run_id=run_id,
            content_type=media, query={"synthetic": True, "revision": revision, "params": params},
            warnings=["Synthetic geometry; not official source data."], http_status=200)
    monkeypatch.setattr(census if kind == "tiger" else fema, "download_snapshot", download)
    if kind == "tiger" and force_tiger_download:
        monkeypatch.setattr(census, "_reusable_immutable_snapshot", lambda *_args: None)
    return calls


def refresh(conn, kind):
    return census.refresh_tiger_boundaries(conn) if kind == "tiger" else fema.refresh_fema_sfha(conn)


def table_digest(conn, statement):
    rows = conn.execute(statement).fetchall()
    return len(rows), hashlib.sha256(json.dumps(rows, default=str, separators=(",", ":")).encode()).hexdigest()


def active_state(conn):
    return {
        "facts": table_digest(conn, "SELECT * FROM geo_boundary ORDER BY boundary_id"),
        "lineage": table_digest(conn, "SELECT * FROM standardized_record ORDER BY standardized_record_id"),
        "freshness": conn.execute("SELECT source_id,last_successful_update FROM source_registry ORDER BY source_id").fetchall(),
        "successful_runs": table_digest(conn, "SELECT * FROM ingestion_run WHERE status IN ('SUCCEEDED','SUCCEEDED_WITH_WARNINGS') ORDER BY run_id"),
    }


def seed(database, monkeypatch, kind):
    conn, root = database
    install_downloads(monkeypatch, kind, 1)
    first = refresh(conn, kind)
    assert conn.execute("SELECT count(*) FROM geo_boundary WHERE source_id=?", [SOURCES[kind]]).fetchone() == ((30 if kind == "tiger" else 10_000),)
    baseline = active_state(conn)
    exports = {path: sha256_file(path) for path in (root / "data/standardized").rglob("*.parquet")}
    assert len(exports) == 1
    install_downloads(monkeypatch, kind, 2)
    return first, baseline, exports


@pytest.mark.parametrize("kind", SOURCES)
@pytest.mark.parametrize("stage", ("export", "lineage", "freshness", "commit"))
def test_failed_refresh_preserves_previous_publication(database, monkeypatch, kind, stage):
    conn, _ = database
    _, baseline, exports = seed(database, monkeypatch, kind)
    triggered = False
    match = {"lineage": "INSERT INTO standardized_record", "freshness": "UPDATE source_registry", "commit": "COMMIT"}.get(stage)
    def fail_once(statement):
        nonlocal triggered
        if not triggered and ((match and match in statement) or
                              (stage == "export" and kind == "fema" and statement.startswith("COPY "))):
            triggered = True
            if stage == "export":
                output = re.search(r"TO '((?:[^']|'')+)'", statement).group(1).replace("''", "'")
                Path(output).write_bytes(b"synthetic incomplete export")
            raise OSError("synthetic publication failure")
    if stage == "export" and kind == "tiger":
        def failed_export(self, output, **_kwargs):
            nonlocal triggered
            triggered = True
            Path(output).write_bytes(b"synthetic incomplete export")
            raise OSError("synthetic publication failure")
        monkeypatch.setattr(gpd.GeoDataFrame, "to_parquet", failed_export)
    with pytest.raises(OSError, match="synthetic publication failure"):
        refresh(ProbeConnection(conn, before=fail_once), kind)
    assert triggered
    assert active_state(conn) == baseline
    assert all(sha256_file(path) == digest for path, digest in exports.items())
    assert conn.execute("SELECT status FROM ingestion_run ORDER BY started_at DESC LIMIT 1").fetchone() == ("FAILED",)


@pytest.mark.parametrize("kind", SOURCES)
def test_reader_sees_complete_old_publication_until_commit(database, monkeypatch, kind):
    conn, root = database
    _, baseline, _ = seed(database, monkeypatch, kind)
    observations = []
    def observe(statement):
        if "UPDATE source_registry" in statement:
            with duckdb.connect(str(root / "synthetic.duckdb")) as reader:
                observations.append(active_state(reader))
    refresh(ProbeConnection(conn, before=observe), kind)
    assert observations == [baseline]
    assert active_state(conn) != baseline


@pytest.mark.parametrize("kind", SOURCES)
def test_success_retains_parent_links_exports_and_manifest(database, monkeypatch, kind):
    conn, _ = database
    _, _, exports = seed(database, monkeypatch, kind)
    old_links = set(conn.execute("SELECT * FROM standardized_record").fetchall())
    result = refresh(conn, kind)
    assert old_links <= set(conn.execute("SELECT * FROM standardized_record").fetchall())
    assert all(sha256_file(path) == digest for path, digest in exports.items())
    output = Path(result["standardized_parquet"])
    assert output.is_file() and output not in exports
    assert result["run_id"] in output.parts
    manifest = json.loads(Path(result["publication_manifest"]).read_text(encoding="utf-8"))
    assert manifest["kind"] == "derived-multisnapshot-publication"
    assert manifest["run_id"] == result["run_id"]
    assert manifest["export"]["sha256"] == sha256_file(output)
    assert manifest["row_count"] == (30 if kind == "tiger" else 10_000)
    parents = {item["snapshot_id"]: item for item in manifest["inputs"]}
    assert len(parents) == (4 if kind == "tiger" else 51)
    assert all(item["role"] in {"metadata", "count", "geometry"} for item in parents.values())
    fact_parents = {row[0] for row in conn.execute("SELECT DISTINCT snapshot_id FROM geo_boundary WHERE source_id=?", [SOURCES[kind]]).fetchall()}
    assert len(fact_parents) == (4 if kind == "tiger" else 20)
    assert fact_parents == {key for key, item in parents.items() if item["role"] == "geometry"}
    for snapshot_id, item in parents.items():
        raw_path, digest = conn.execute("SELECT raw_path,sha256 FROM dataset_snapshot WHERE snapshot_id=?", [snapshot_id]).fetchone()
        assert item["sha256"] == digest == sha256_file(Path(raw_path))
    record = conn.execute("SELECT run_id,source_url FROM dataset_snapshot WHERE snapshot_id=?", [result["publication_snapshot_id"]]).fetchone()
    assert record == (result["run_id"], "local-derived://multisnapshot-publication")


@pytest.mark.parametrize("kind", SOURCES)
def test_interrupt_rolls_back_and_does_not_report_success(database, monkeypatch, kind):
    conn, _ = database
    _, baseline, exports = seed(database, monkeypatch, kind)
    def interrupt(statement):
        if "INSERT INTO geo_boundary" in statement:
            raise KeyboardInterrupt("synthetic interruption after inserting facts")
    with pytest.raises(KeyboardInterrupt):
        refresh(ProbeConnection(conn, after=interrupt), kind)
    assert active_state(conn) == baseline
    assert all(sha256_file(path) == digest for path, digest in exports.items())
    assert conn.execute("SELECT status FROM ingestion_run ORDER BY started_at DESC LIMIT 1").fetchone() == ("INDETERMINATE",)
    conn.execute("BEGIN")
    conn.execute("ROLLBACK")
    assert conn.execute("SELECT count(*) FROM duckdb_views() WHERE view_name IN ('_boundaries','_fema_page_boundaries')").fetchone() == (0,)
    assert conn.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name='_fema_sfha_staging'").fetchone() == (0,)


def test_tiger_verified_cache_hit_records_zero_requests_and_keeps_raw_metadata(database, monkeypatch):
    conn, root = database
    first_calls = install_downloads(monkeypatch, "tiger", 1, force_tiger_download=False)
    first = refresh(conn, "tiger")
    assert len(first_calls) == len(GREATER_NYC_STATE_FIPS) == 4
    raw_before = conn.execute("SELECT * FROM dataset_snapshot WHERE source_url LIKE 'https://www2.census.gov/%' ORDER BY snapshot_id").fetchall()
    old_links = set(conn.execute("SELECT * FROM standardized_record").fetchall())
    exports = {path: sha256_file(path) for path in (root / "data/standardized").rglob("*.parquet")}
    second_calls = install_downloads(monkeypatch, "tiger", 2, force_tiger_download=False)
    second = refresh(conn, "tiger")
    assert second_calls == []
    assert first["snapshots"] == second["snapshots"]
    assert conn.execute("SELECT request_count,downloaded_bytes FROM ingestion_run WHERE run_id=?", [second["run_id"]]).fetchone() == (0, 0)
    assert conn.execute("SELECT * FROM dataset_snapshot WHERE source_url LIKE 'https://www2.census.gov/%' ORDER BY snapshot_id").fetchall() == raw_before
    assert set(conn.execute("SELECT * FROM standardized_record").fetchall()) == old_links
    assert all(sha256_file(path) == digest for path, digest in exports.items())
    assert second["reused_snapshots"] == 4
