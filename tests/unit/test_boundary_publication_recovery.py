"""Supplementary real local recovery and unchanged coverage gates; synthetic inputs."""
import json
import os
from pathlib import Path
import subprocess
import sys

import duckdb
import h3
import pytest
from shapely.geometry import Polygon, box

import test_boundary_publication as fixture
from test_boundary_publication import ProbeConnection, SOURCES, active_state, database, refresh, seed
from nyc_lens.ingest import geographies
from nyc_lens.ingest.common import register_table_lineage
from nyc_lens.util import sha256_file


@pytest.mark.parametrize("kind,message", [
    ("boroughs", "Expected five borough"),
    ("nta", "coverage unexpectedly low"),
    ("zcta", "coverage is unexpected"),
    ("hazard", "feature count outside expected range"),
    ("taxi", "coverage unexpectedly has"),
    ("h3", "coverage unexpectedly low"),
])
def test_existing_coverage_rejections_preserve_current_data(database, monkeypatch, kind, message):
    conn, _ = database
    _, _, exports = seed(database, monkeypatch, kind)
    if kind == "h3":
        tiny = box(-74.1, 40.62, -74.099, 40.621)
        conn.execute("UPDATE geo_boundary SET geom_wkt=? WHERE source_id='nyc_borough_boundaries'",
                     [tiny.wkt])
    else:
        original = fixture.input_frame
        count = {"boroughs": 4, "nta": 249, "zcta": 99, "hazard": 5, "taxi": 249}[kind]
        monkeypatch.setattr(fixture, "input_frame", lambda name, revision: original(name, revision).iloc[:count].copy())
    baseline = active_state(conn)
    with pytest.raises(ValueError, match=message):
        refresh(conn, kind)
    assert active_state(conn) == baseline
    assert all(sha256_file(path) == digest for path, digest in exports.items())
    assert conn.execute("SELECT status FROM ingestion_run ORDER BY started_at DESC LIMIT 1").fetchone() == ("FAILED",)


def test_duplicate_taxi_zone_identifiers_remain_rejected(database, monkeypatch):
    conn, _ = database
    _, baseline, exports = seed(database, monkeypatch, "taxi")
    original = fixture.input_frame
    def duplicate(kind, revision):
        frame = original(kind, revision)
        frame.loc[frame.index[-1], "LocationID"] = frame.iloc[0]["LocationID"]
        return frame
    monkeypatch.setattr(fixture, "input_frame", duplicate)
    with pytest.raises(ValueError, match="duplicate LocationID"):
        refresh(conn, "taxi")
    assert active_state(conn) == baseline
    assert all(sha256_file(path) == digest for path, digest in exports.items())


def test_boundary_helper_does_not_commit_an_unrelated_caller_transaction(database, monkeypatch):
    conn, _ = database
    first, baseline, _ = seed(database, monkeypatch, "nta")
    rows = conn.execute("SELECT * FROM geo_boundary WHERE source_id=?", [SOURCES["nta"]]).df()
    conn.execute("BEGIN")
    conn.execute("UPDATE source_registry SET name='synthetic caller pending'")
    with pytest.raises(duckdb.TransactionException):
        geographies._replace_boundaries(conn, SOURCES["nta"], rows, first["snapshot_id"])
    conn.execute("ROLLBACK")
    assert active_state(conn) == baseline
    assert conn.execute("SELECT DISTINCT name FROM source_registry").fetchall() == [("Synthetic only",)]


def test_failed_attempt_can_be_explicitly_retried_without_replacing_evidence(database, monkeypatch):
    conn, _ = database
    _, baseline, exports = seed(database, monkeypatch, "hazard")
    def fail(statement):
        if "UPDATE source_registry" in statement:
            raise ValueError("synthetic freshness failure")
    with pytest.raises(ValueError, match="synthetic freshness failure"):
        refresh(ProbeConnection(conn, before=fail), "hazard")
    assert active_state(conn) == baseline
    result = refresh(conn, "hazard")
    assert result["features"] == 6
    assert conn.execute("SELECT status FROM ingestion_run ORDER BY started_at").fetchall() == [
        ("SUCCEEDED_WITH_WARNINGS",), ("FAILED",), ("SUCCEEDED_WITH_WARNINGS",)]
    assert conn.execute("SELECT count(*) FROM dataset_snapshot").fetchone() == (6,)
    assert all(sha256_file(path) == digest for path, digest in exports.items())


def test_owned_exit_after_freshness_keeps_previous_publication(database, monkeypatch):
    conn, root = database
    _, baseline, exports = seed(database, monkeypatch, "hazard")
    conn.close()
    script = Path(__file__).resolve().parents[1] / "fixtures/boundary_publication_exit.py"
    result = subprocess.run([sys.executable, str(script), str(root)], capture_output=True,
        text=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 83, result.stderr
    with duckdb.connect(str(root / "synthetic.duckdb")) as reader:
        assert active_state(reader) == baseline
        assert reader.execute("SELECT status FROM ingestion_run ORDER BY started_at DESC LIMIT 1").fetchone() == ("INDETERMINATE",)
        assert reader.execute("SELECT count(*) FROM dataset_snapshot").fetchone() == (4,)
    assert all(sha256_file(path) == digest for path, digest in exports.items())


def test_h3_rebuild_removes_legacy_active_alias_but_retains_its_lineage(database, monkeypatch):
    conn, _ = database
    first, _, exports = seed(database, monkeypatch, "h3")
    legacy_cell = h3.latlng_to_cell(40.90, -73.90, 10)
    polygon = Polygon([(longitude, latitude) for latitude, longitude in h3.cell_to_boundary(legacy_cell)])
    latitude, longitude = h3.cell_to_latlng(legacy_cell)
    conn.execute("""INSERT INTO geo_cell BY NAME SELECT * REPLACE
        (? AS cell_id, ? AS geography_id, ? AS geom_wkt, ? AS centroid_lon, ? AS centroid_lat,
         'derived_h3' AS source_id) FROM geo_cell LIMIT 1""",
        [legacy_cell, legacy_cell, polygon.wkt, longitude, latitude])
    register_table_lineage(conn, table_name="geo_cell", entity_type="geo_cell", id_column="cell_id",
        source_id="derived_h3", snapshot_id=first["snapshot_id"])
    legacy_lineage = conn.execute("SELECT * FROM standardized_record WHERE source_id='derived_h3'").fetchall()
    assert len(legacy_lineage) == 1
    result = refresh(conn, "h3")
    assert conn.execute("SELECT count(*) FROM geo_cell WHERE source_id='derived_h3'").fetchone() == (0,)
    assert conn.execute("SELECT * FROM standardized_record WHERE source_id='derived_h3'").fetchall() == legacy_lineage
    assert result["cells"] >= 40_000
    derivation_path = conn.execute("SELECT raw_path FROM dataset_snapshot WHERE snapshot_id=?", [result["snapshot_id"]]).fetchone()[0]
    derivation = json.loads(Path(derivation_path).read_text(encoding="utf-8"))
    assert derivation["parent_boundary_snapshot_ids"] == result["parent_boundary_snapshot_ids"]
    assert all(sha256_file(path) == digest for path, digest in exports.items())
