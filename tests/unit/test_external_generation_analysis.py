"""Real synthetic engine/worker through the in-process HTTP transport; no LLM."""
from importlib import import_module

from fastapi.testclient import TestClient

from nyc_lens.analysis_jobs import stop_active_jobs
from nyc_lens.db import db_session
from nyc_lens.demo import DEMO_ADDRESSES, seed_demo
from nyc_lens.publication import publication_transaction
from nyc_lens.storage import refresh_external_view, replace_source_from_relation
from nyc_lens.util import sha256_file


def test_two_address_reports_and_evidence_survive_external_source_replacement(tmp_path, monkeypatch):
    monkeypatch.setenv("NYC_LENS_HOME", str(tmp_path))
    monkeypatch.delenv("NYC_LENS_DATABASE", raising=False)
    monkeypatch.setenv("NYC_LENS_AI_PROVIDER", "disabled")
    seed_demo(tmp_path)
    with db_session() as conn:
        # Explicit conversion of this tiny synthetic table only, not production.
        conn.execute("CREATE TEMP TABLE _initial_poi AS SELECT * FROM poi")
        conn.execute("DROP TABLE poi")
        refresh_external_view(conn, "poi")
        replace_source_from_relation(conn, table_name="poi", source_id="overture_places", relation="_initial_poi")
        retained = {tmp_path / path: sha256_file(tmp_path / path) for path, in conn.execute(
            "SELECT relative_path FROM external_partition_catalog WHERE table_name='poi'").fetchall()}
    client = TestClient(import_module("nyc_lens.api.app").app)
    payload = {"confirmed": True, "intent": {"addresses": DEMO_ADDRESSES,
               "business_type": "full_service_restaurant", "catchment": "500m", "priorities": ["competition", "rent"]}}
    try:
        response = client.post("/api/assistant/compare", json=payload)
        assert response.status_code == 200, response.text
        old = response.json()
        assert old["data_mode"] == "SYNTHETIC_DEMO"
        assert [row["value"] for row in old["metric_comparisons"]["direct_competitor_count"]["values"]] == [1.0, 3.0]
        assert old["assistant"]["brief"]["composite_score"] is None
        evidence_url = old["assistant"]["brief"]["differences"][0]["evidence_urls"][0]
        old_evidence = client.get(evidence_url).json()
        report_url = f"/api/comparison/{old['comparison_id']}/report?format=json"
        old_report = client.get(report_url).json()
        with db_session() as conn, publication_transaction(conn):
            conn.execute("""CREATE TEMP TABLE _revised_poi AS SELECT * REPLACE
                ('synthetic-places-v2' AS snapshot_id) FROM poi WHERE poi_id='synthetic-poi-0-0'""")
            conn.execute("""INSERT INTO dataset_snapshot(snapshot_id, source_id, run_id, vintage, retrieved_at, source_url, license, status)
                VALUES ('synthetic-places-v2', 'overture_places', 'synthetic-run-v2', 'SYNTHETIC 2027', now(),
                        'synthetic://nyc-lens/generation-v2', 'Synthetic only', 'SUCCEEDED')""")
            replace_source_from_relation(conn, table_name="poi", source_id="overture_places", relation="_revised_poi")
            conn.execute("""INSERT INTO standardized_record VALUES ('new-lineage', 'poi', 'synthetic-poi-0-0',
                'overture_places', 'synthetic-places-v2', 'synthetic-poi-0-0', 'synthetic-v2', now())""")
        # The original failure is retained as an incomplete-publication scenario:
        # do not return new counts labeled as the earlier completed source.
        pending = client.post("/api/assistant/compare", json=payload)
        assert pending.status_code == 422, pending.text
        assert client.get(report_url).json() == old_report
        with db_session() as conn:
            conn.execute("""INSERT INTO ingestion_run(run_id, source_id, command, started_at, completed_at, status)
                VALUES ('synthetic-run-v2', 'overture_places', 'synthetic complete generation', now(), now(), 'SUCCEEDED')""")
        response = client.post("/api/assistant/compare", json=payload)
        assert response.status_code == 200, response.text
        new = response.json()
        assert new["comparison_id"] != old["comparison_id"]
        assert [row["value"] for row in new["metric_comparisons"]["direct_competitor_count"]["values"]] == [1.0, 0.0]
        assert {row["snapshot_id"] for row in new["metric_comparisons"]["direct_competitor_count"]["values"]} == {"synthetic-places-v2"}
        assert client.get(report_url).json() == old_report
        assert client.get(evidence_url).json() == old_evidence
        assert {row["snapshot_id"] for row in old_evidence["evidence"]} == {"synthetic-places-v1"}
        assert all(sha256_file(path) == digest for path, digest in retained.items())
    finally:
        client.close()
        stop_active_jobs()
