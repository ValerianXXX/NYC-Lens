"""Additional synthetic recovery observations, distinct from the frozen baseline."""
from pathlib import Path
import os
import subprocess
import sys

import duckdb
import pandas as pd
import pytest

from test_socrata_publication import (
    ProbeConnection, SPECS, active_state, database, seed,
)
from nyc_lens.ingest import socrata
from nyc_lens.util import sha256_file


def test_owned_process_exit_preserves_old_data_and_attempt_audit(database, monkeypatch):
    conn, root = database
    baseline, exports = seed(database, monkeypatch, "restaurants")
    conn.close()
    script = Path(__file__).resolve().parents[1] / "fixtures/socrata_publication_exit.py"
    result = subprocess.run([sys.executable, str(script), str(root)], capture_output=True,
        text=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 79, result.stderr
    with duckdb.connect(str(root / "synthetic.duckdb")) as reader:
        assert active_state(reader, "restaurants") == baseline
        assert reader.execute("SELECT status FROM ingestion_run ORDER BY started_at DESC LIMIT 1").fetchone() == ("INDETERMINATE",)
        assert reader.execute("SELECT count(*) FROM dataset_snapshot").fetchone() == (4,)
    assert all(sha256_file(path) == digest for path, digest in exports.items())


def test_rolled_back_refresh_can_be_explicitly_retried(database, monkeypatch):
    conn, _ = database
    baseline, _ = seed(database, monkeypatch, "hotels")
    def refuse_lineage(statement):
        if "INSERT INTO standardized_record" in statement:
            raise ValueError("synthetic lineage failure")
    with pytest.raises(ValueError, match="synthetic lineage failure"):
        socrata.refresh_hotels(ProbeConnection(conn, before=refuse_lineage))
    assert active_state(conn, "hotels") == baseline
    retry = socrata.refresh_hotels(conn)
    assert retry["hotels"] == 1
    assert active_state(conn, "hotels") != baseline
    assert conn.execute("SELECT status FROM ingestion_run ORDER BY started_at").fetchall() == [("SUCCEEDED",), ("FAILED",), ("SUCCEEDED",)]
    assert pd.read_parquet(retry["standardized_parquet"])["snapshot_id"].tolist() == [retry["snapshot_id"]]


def test_source_refresh_retains_unrelated_facts_and_prior_evidence(database, monkeypatch):
    conn, _ = database
    baseline, _ = seed(database, monkeypatch, "air")
    conn.execute("""INSERT INTO health_indicator BY NAME SELECT * REPLACE
        ('synthetic-other-id' AS health_indicator_id, 'synthetic-other-source' AS source_id)
        FROM health_indicator LIMIT 1""")
    unrelated = conn.execute("SELECT * FROM health_indicator WHERE source_id='synthetic-other-source'").fetchall()
    result = socrata.refresh_air_quality(conn)
    assert conn.execute("SELECT * FROM health_indicator WHERE source_id='synthetic-other-source'").fetchall() == unrelated
    final = active_state(conn, "air")
    assert all(row in final["lineage"] for row in baseline["lineage"])
    for table in SPECS["air"][2]:
        assert conn.execute(f"SELECT DISTINCT snapshot_id FROM {table} WHERE source_id='air_quality_health'").fetchall() == [(result["snapshot_id"],)]
