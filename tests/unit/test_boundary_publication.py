"""Frozen publication contracts: synthetic geography, real geometry/H3/DB/files."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import zipfile

import duckdb
import geopandas as gpd
import httpx
import pandas as pd
import pytest
from shapely.geometry import box

from test_socrata_publication import ProbeConnection
from nyc_lens.db import SCHEMA_PATH
from nyc_lens.ingest import environment, geographies, tlc
from nyc_lens.provenance import save_bytes_snapshot
from nyc_lens.util import sha256_file


SOURCES = {
    "boroughs": "nyc_borough_boundaries",
    "nta": "nyc_nta_2020",
    "zcta": "census_zcta_2020",
    "hazard": "nyc_hurricane_evacuation_zones",
    "h3": "derived_h3_grid",
    "taxi": "tlc_taxi_zones",
}


def input_frame(kind, revision):
    shift = (revision - 1) * 0.00001
    if kind == "boroughs":
        return gpd.GeoDataFrame({
            "borocode": range(1, 6),
            "boroname": [f"Synthetic borough {i} revision {revision}" for i in range(1, 6)],
        }, geometry=[box(-74.12 + i * .056, 40.60, -74.12 + (i + 1) * .056, 40.85)
                     for i in range(5)], crs="EPSG:4326")
    count = {"nta": 250, "zcta": 100, "hazard": 6, "taxi": 263}[kind]
    fields = {
        "nta": {"nta2020": [f"synthetic-{i}" for i in range(count)],
                "ntaname": [f"Synthetic area {i} revision {revision}" for i in range(count)],
                "cdta2020": ["synthetic-parent"] * count},
        "zcta": {"GEOID": [f"synthetic-{i}" for i in range(count)],
                 "NAME": [f"Synthetic ZCTA {i} revision {revision}" for i in range(count)]},
        "hazard": {"hurricane_": [str(i + 1) for i in range(count)]},
        "taxi": {"LocationID": list(range(1, count + 1)),
                 "zone": [f"Synthetic zone {i} revision {revision}" for i in range(count)],
                 "borough": ["Synthetic only"] * count},
    }[kind]
    polygons = [box(-74.10 + (i % 20) * .001 + shift, 40.62 + (i // 20) * .001,
                    -74.0995 + (i % 20) * .001 + shift, 40.6205 + (i // 20) * .001)
                for i in range(count)]
    return gpd.GeoDataFrame(fields, geometry=polygons, crs="EPSG:4326")


def install_downloads(monkeypatch, kind, revision):
    def metadata(conn, source_id, vintage, run_id):
        return save_bytes_snapshot(conn, source_id=source_id, vintage=vintage,
            filename="metadata.json", content=b'{"synthetic":true}',
            source_url="synthetic://fixture/metadata", run_id=run_id, content_type="application/json")

    def download(conn, *, source_id, vintage, filename, run_id, **_kwargs):
        frame = input_frame(kind, revision)
        content = frame.to_json().encode()
        if kind == "taxi":
            package = Path(os.environ["NYC_LENS_HOME"]) / "fixture-package" / f"revision-{revision}"
            package.mkdir(parents=True, exist_ok=True)
            frame.to_file(package / "taxi_zones.shp", driver="ESRI Shapefile", index=False)
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
                for path in sorted(package.glob("taxi_zones.*")):
                    output.write(path, "taxi_zones/" + path.name)
            content = archive.getvalue()
        return save_bytes_snapshot(conn, source_id=source_id, vintage=vintage, filename=filename,
            content=content, source_url="synthetic://fixture/geometry", run_id=run_id,
            content_type="application/zip" if kind == "taxi" else "application/geo+json")

    def post(url, **_kwargs):
        return httpx.Response(200, content=input_frame("zcta", revision).to_json().encode(),
                              request=httpx.Request("POST", url))

    for module in (geographies, environment):
        monkeypatch.setattr(module, "fetch_metadata_snapshot", metadata)
    for module in (geographies, environment, tlc):
        monkeypatch.setattr(module, "download_snapshot", download)
    monkeypatch.setattr(geographies.httpx, "post", post)


def refresh(conn, kind):
    if kind == "taxi":
        return tlc.refresh_taxi_zones(conn)
    if kind == "hazard":
        return environment.refresh_hazard_layer(conn, SOURCES[kind])
    if kind == "h3":
        return geographies.refresh_h3_grid(conn)
    if kind == "zcta":
        return geographies.refresh_zcta(conn)
    return geographies.refresh_nyc_geography(conn, SOURCES[kind])


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
    try:
        yield conn, tmp_path
    finally:
        conn.close()


def table_state(conn, statement):
    rows = conn.execute(statement).fetchall()
    digest = hashlib.sha256(json.dumps(rows, default=str, separators=(",", ":")).encode()).hexdigest()
    return len(rows), digest


def active_state(conn):
    return {
        "boundaries": table_state(conn, "SELECT * FROM geo_boundary ORDER BY boundary_id"),
        "cells": table_state(conn, "SELECT * FROM geo_cell ORDER BY cell_id"),
        "lineage": table_state(conn, "SELECT * FROM standardized_record ORDER BY standardized_record_id"),
        "freshness": conn.execute("SELECT source_id,last_successful_update FROM source_registry ORDER BY source_id").fetchall(),
        "successful_runs": table_state(conn, "SELECT * FROM ingestion_run WHERE status IN ('SUCCEEDED','SUCCEEDED_WITH_WARNINGS') ORDER BY run_id"),
    }


def seed(database, monkeypatch, kind):
    conn, root = database
    if kind in ("zcta", "h3"):
        install_downloads(monkeypatch, "boroughs", 1)
        refresh(conn, "boroughs")
    install_downloads(monkeypatch, kind, 1)
    result = refresh(conn, kind)
    assert result["snapshot_id"]
    if kind == "h3":
        assert result["cells"] >= 40_000
    conn.execute("""INSERT INTO geo_boundary BY NAME SELECT * REPLACE
        ('synthetic-unrelated-boundary' AS boundary_id, 'synthetic-unrelated-source' AS source_id)
        FROM geo_boundary LIMIT 1""")
    baseline = active_state(conn)
    exports = {path: sha256_file(path) for path in (root / "data/standardized").rglob("*.parquet")}
    assert exports
    install_downloads(monkeypatch, kind, 2)
    return result, baseline, exports


@pytest.mark.parametrize("kind", SOURCES)
@pytest.mark.parametrize("stage", ("lineage", "export", "freshness", "commit"))
def test_failed_refresh_preserves_complete_previous_publication(database, monkeypatch, kind, stage):
    conn, _ = database
    _, baseline, exports = seed(database, monkeypatch, kind)
    triggered = False
    statement_match = {"lineage": "INSERT INTO standardized_record",
                       "freshness": "UPDATE source_registry", "commit": "COMMIT"}.get(stage)

    def fail_once(statement):
        nonlocal triggered
        if not triggered and statement_match and statement_match in statement:
            triggered = True
            raise ValueError("synthetic publication failure")

    if stage == "export":
        def failed_export(self, path, *args, **kwargs):
            nonlocal triggered
            triggered = True
            Path(path).write_bytes(b"synthetic partial candidate export")
            raise ValueError("synthetic publication failure")
        monkeypatch.setattr(pd.DataFrame if kind == "h3" else gpd.GeoDataFrame, "to_parquet", failed_export)
    with pytest.raises(ValueError, match="synthetic publication failure"):
        refresh(ProbeConnection(conn, before=fail_once), kind)
    assert triggered
    assert active_state(conn) == baseline
    assert all(sha256_file(path) == digest for path, digest in exports.items())
    assert conn.execute("SELECT status FROM ingestion_run ORDER BY started_at DESC LIMIT 1").fetchone() == ("FAILED",)
    assert conn.execute("SELECT count(*) FROM duckdb_views() WHERE view_name IN ('_new_boundaries','_h3_grid')").fetchone() == (0,)


@pytest.mark.parametrize("kind", SOURCES)
def test_reader_sees_old_publication_until_freshness_and_facts_commit_together(database, monkeypatch, kind):
    conn, root = database
    _, baseline, _ = seed(database, monkeypatch, kind)
    observations = []
    def observe(statement):
        if "UPDATE source_registry" in statement:
            with duckdb.connect(str(root / "synthetic.duckdb")) as reader:
                observations.append(active_state(reader))
    result = refresh(ProbeConnection(conn, before=observe), kind)
    assert observations == [baseline]
    assert active_state(conn) != baseline
    table = "geo_cell" if kind == "h3" else "geo_boundary"
    assert conn.execute(f"SELECT DISTINCT snapshot_id FROM {table} WHERE source_id=?",
                        [SOURCES[kind]]).fetchall() == [(result["snapshot_id"],)]


@pytest.mark.parametrize("kind", SOURCES)
def test_success_retains_history_and_previous_exports(database, monkeypatch, kind):
    conn, _ = database
    first, _, exports = seed(database, monkeypatch, kind)
    old_lineage = conn.execute("SELECT count(*) FROM standardized_record WHERE snapshot_id=?",
                               [first["snapshot_id"]]).fetchone()[0]
    assert old_lineage > 0
    result = refresh(conn, kind)
    assert conn.execute("SELECT count(*) FROM standardized_record WHERE snapshot_id=?",
                        [first["snapshot_id"]]).fetchone() == (old_lineage,)
    assert all(sha256_file(path) == digest for path, digest in exports.items())
    output = Path(result["standardized_parquet"])
    assert output.is_file() and result["snapshot_id"] in output.parts
    assert output not in exports
    if kind == "h3":
        assert result["cells"] >= 40_000
        assert set(pd.read_parquet(output)["snapshot_id"]) == {result["snapshot_id"]}


@pytest.mark.parametrize("kind", ("hazard", "h3"))
def test_interrupt_rolls_back_and_leaves_attempt_indeterminate(database, monkeypatch, kind):
    conn, _ = database
    _, baseline, exports = seed(database, monkeypatch, kind)
    def interrupt(statement):
        if "INSERT INTO geo_" in statement:
            raise KeyboardInterrupt("synthetic interruption")
    with pytest.raises(KeyboardInterrupt):
        refresh(ProbeConnection(conn, after=interrupt), kind)
    assert active_state(conn) == baseline
    assert all(sha256_file(path) == digest for path, digest in exports.items())
    assert conn.execute("SELECT status FROM ingestion_run ORDER BY started_at DESC LIMIT 1").fetchone() == ("INDETERMINATE",)
    conn.execute("BEGIN")
    conn.execute("ROLLBACK")
    assert conn.execute("SELECT count(*) FROM duckdb_views() WHERE view_name IN ('_new_boundaries','_h3_grid')").fetchone() == (0,)
