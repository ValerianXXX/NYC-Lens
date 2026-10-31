"""Synthetic source refresh contracts, frozen before changing the three adapters."""
from pathlib import Path
import shutil

import duckdb
import pandas as pd
import pytest

from nyc_lens.db import SCHEMA_PATH
from nyc_lens.ingest import socrata
from nyc_lens.provenance import save_bytes_snapshot
from nyc_lens.util import sha256_file


SPECS = {
    "restaurants": ("dohmh_restaurants", socrata.refresh_dohmh,
                    ("business_location", "inspection"), "_inspection"),
    "hotels": ("nyc_hotels", socrata.refresh_hotels, ("hotel",), "_hotels"),
    "air": ("air_quality_health", socrata.refresh_air_quality,
            ("health_indicator", "environment_indicator"), "_environment"),
}


def input_frame(kind, revision):
    if kind == "restaurants":
        return pd.DataFrame([{
            "camis": f"synthetic-{revision}", "dba": f"Synthetic restaurant {revision}",
            "boro": "MANHATTAN", "building": "10", "street": "SYNTHETIC AVENUE",
            "zipcode": "10001", "cuisine_description": "Synthetic cuisine",
            "inspection_date": "2026-01-15", "action": "No closure", "violation_code": "X",
            "violation_description": "Synthetic record only", "score": "10", "grade": "A",
            "latitude": "40.75", "longitude": "-73.99", "nta": "synthetic-nta",
        }])
    if kind == "hotels":
        return pd.DataFrame([{
            "parid": f"synthetic-{revision}", "bbl": f"synthetic-lot-{revision}",
            "taxyear": "2026", "street_num": "20", "street_name": "SYNTHETIC AVENUE",
            "borough": "MANHATTAN", "latitude": "40.75", "longitude": "-73.99",
            "nta": "synthetic-nta",
        }])
    return pd.DataFrame([{
        "unique_id": f"synthetic-{revision}-{index}", "indicator_id": str(index),
        "name": label, "measure": "Synthetic area measure", "measure_info": "synthetic units",
        "data_value": str(revision * 10 + index), "start_date": "2026-01-01",
        "message": "Synthetic only", "time_period": "2026", "geo_type_name": "synthetic_area",
        "geo_join_id": "synthetic-area",
    } for index, label in enumerate(("Asthma estimate", "Air observation"))])


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("NYC_LENS_HOME", str(tmp_path))
    monkeypatch.delenv("NYC_LENS_DATABASE", raising=False)
    shutil.copytree(Path(__file__).resolve().parents[2] / "config", tmp_path / "config")
    conn = duckdb.connect(str(tmp_path / "synthetic.duckdb"))
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    for source, *_ in SPECS.values():
        conn.execute("""INSERT INTO source_registry
            (source_id,name,steward,source_url,config_json,registered_at)
            VALUES (?, 'Synthetic only', 'Test fixture', 'synthetic://fixture', '{}', now())""", [source])
    try:
        yield conn, tmp_path
    finally:
        conn.close()


def install_downloads(monkeypatch, kind, revision, *, empty=False):
    def metadata(conn, source_id, vintage, run_id):
        return save_bytes_snapshot(conn, source_id=source_id, vintage=vintage,
            filename="metadata.json", content=b'{"synthetic":true}',
            source_url="synthetic://fixture/metadata", run_id=run_id, content_type="application/json")

    def download(conn, source_id, vintage, run_id, **_kwargs):
        frame = input_frame(kind, revision)
        if empty:
            frame = frame.iloc[0:0]
        return save_bytes_snapshot(conn, source_id=source_id, vintage=vintage,
            filename="rows.csv", content=frame.to_csv(index=False).encode("utf-8"),
            source_url="synthetic://fixture/rows", run_id=run_id, content_type="text/csv")

    monkeypatch.setattr(socrata, "fetch_metadata_snapshot", metadata)
    monkeypatch.setattr(socrata, "download_socrata_csv", download)


def active_state(conn, kind):
    source, _, tables, _ = SPECS[kind]
    return {
        "tables": [conn.execute(f"SELECT * FROM {table} WHERE source_id=? ORDER BY 1", [source]).fetchall()
                   for table in tables],
        "lineage": conn.execute("SELECT * FROM standardized_record WHERE source_id=? ORDER BY 1", [source]).fetchall(),
        "freshness": conn.execute("SELECT last_successful_update FROM source_registry WHERE source_id=?", [source]).fetchone(),
        "successful_runs": conn.execute("SELECT * FROM ingestion_run WHERE source_id=? AND status='SUCCEEDED' ORDER BY 1", [source]).fetchall(),
    }


class ProbeConnection:
    def __init__(self, conn, *, before=None, after=None):
        self.conn, self.before, self.after = conn, before, after

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def execute(self, statement, *args, **kwargs):
        if self.before:
            self.before(statement)
        result = self.conn.execute(statement, *args, **kwargs)
        if self.after:
            self.after(statement)
        return result


def seed(database, monkeypatch, kind):
    conn, root = database
    install_downloads(monkeypatch, kind, 1)
    result = SPECS[kind][1](conn)
    assert result["run_id"]
    baseline = active_state(conn, kind)
    assert all(baseline["tables"])
    exports = {path: sha256_file(path) for path in (root / "data/standardized").rglob("*.parquet")}
    assert exports
    install_downloads(monkeypatch, kind, 2)
    return baseline, exports


@pytest.mark.parametrize("kind", SPECS)
@pytest.mark.parametrize("stage", ("fact", "lineage", "freshness", "commit", "export"))
def test_failed_refresh_keeps_previous_publication(database, monkeypatch, kind, stage):
    conn, _ = database
    baseline, exports = seed(database, monkeypatch, kind)
    source, refresh, _, relation = SPECS[kind]
    match = {"fact": f"SELECT * FROM {relation}", "lineage": "INSERT INTO standardized_record",
             "freshness": "UPDATE source_registry", "commit": "COMMIT"}.get(stage)
    raised = False

    def fail_once(statement):
        nonlocal raised
        if not raised and match and match in statement:
            raised = True
            raise OSError("synthetic publication failure")

    if stage == "export":
        def refuse_export(*_args, **_kwargs):
            raise OSError("synthetic publication failure")
        monkeypatch.setattr(pd.DataFrame, "to_parquet", refuse_export)
    with pytest.raises(OSError, match="synthetic publication failure"):
        refresh(ProbeConnection(conn, before=fail_once))
    assert active_state(conn, kind) == baseline
    assert all(path.exists() and sha256_file(path) == digest for path, digest in exports.items())
    assert conn.execute("SELECT status FROM ingestion_run WHERE source_id=? ORDER BY started_at DESC LIMIT 1", [source]).fetchone() == ("FAILED",)
    assert conn.execute("SELECT count(*) FROM dataset_snapshot WHERE source_id=?", [source]).fetchone()[0] == 4
    assert not conn.execute("SELECT view_name FROM duckdb_views() WHERE view_name IN ('_business','_inspection','_hotels','_health','_environment','_standardized_records')").fetchall()


@pytest.mark.parametrize("kind", SPECS)
def test_readers_never_observe_partial_success(database, monkeypatch, kind):
    conn, root = database
    baseline, exports = seed(database, monkeypatch, kind)
    reader = duckdb.connect(str(root / "synthetic.duckdb"))
    observations = []
    def observe(statement):
        if any(word in statement for word in ("DELETE FROM", "INSERT INTO standardized_record", "UPDATE ingestion_run", "UPDATE source_registry", "COMMIT")):
            observations.append(active_state(reader, kind))
    try:
        SPECS[kind][1](ProbeConnection(conn, after=observe))
        final = active_state(reader, kind)
        assert final != baseline
        assert observations and all(state in (baseline, final) for state in observations)
        assert all(sha256_file(path) == digest for path, digest in exports.items())
        assert len(final["successful_runs"]) == 2
    finally:
        reader.close()


def test_interrupted_refresh_does_not_publish_new_facts(database, monkeypatch):
    conn, _ = database
    baseline, _ = seed(database, monkeypatch, "restaurants")
    def interrupt(statement):
        if "SELECT * FROM _inspection" in statement:
            raise KeyboardInterrupt("synthetic interrupt")
    with pytest.raises(KeyboardInterrupt, match="synthetic interrupt"):
        socrata.refresh_dohmh(ProbeConnection(conn, before=interrupt))
    assert active_state(conn, "restaurants") == baseline
    assert conn.execute("SELECT status FROM ingestion_run ORDER BY started_at DESC LIMIT 1").fetchone() == ("INDETERMINATE",)


def test_successful_empty_hotel_refresh_has_complete_audit(database, monkeypatch):
    conn, _ = database
    seed(database, monkeypatch, "hotels")
    install_downloads(monkeypatch, "hotels", 2, empty=True)
    result = socrata.refresh_hotels(conn)
    assert result["hotels"] == 0
    assert conn.execute("SELECT count(*) FROM hotel WHERE source_id='nyc_hotels'").fetchone() == (0,)
    assert conn.execute("SELECT status, inserted_records FROM ingestion_run WHERE run_id=?", [result["run_id"]]).fetchone() == ("SUCCEEDED", 0)
    assert conn.execute("SELECT row_count FROM dataset_snapshot WHERE snapshot_id=?", [result["snapshot_id"]]).fetchone() == (0,)
