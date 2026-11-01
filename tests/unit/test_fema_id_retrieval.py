"""Synthetic service behavior motivated by a retained real HTTP observation.

The real same-ID probe is not a causal latency benchmark. This contract only
requires that an already selected ID set can be fetched without reapplying the
spatial query, while publication still uses every original completeness check.
"""
import json

from nyc_lens.ingest import fema
from nyc_lens.provenance import save_bytes_snapshot
from test_fema_membership import install_response_fixture
from test_multisnapshot_publication import database


def test_exact_id_retrieval_survives_service_rejection_of_combined_filters(database, monkeypatch):
    conn, _ = database
    calls = install_response_fixture(monkeypatch)
    underlying = fema.download_snapshot

    def download(conn, *, params=None, **kwargs):
        if "objectIds" in (params or {}) and any(
            key in params for key in ("where", "geometry", "geometryType", "inSR", "spatialRel")
        ):
            return save_bytes_snapshot(conn, source_id=kwargs["source_id"],
                vintage=kwargs["vintage"], filename=kwargs["filename"],
                content=json.dumps({"error": {"code": 500, "message": "Synthetic service query failure"}}).encode(),
                source_url=kwargs["url"], run_id=kwargs["run_id"],
                query={"synthetic": True, "params": params}, content_type="application/json",
                warnings=["Synthetic service response; not an actual current FEMA failure."], http_status=200)
        return underlying(conn, params=params, **kwargs)

    monkeypatch.setattr(fema, "download_snapshot", download)
    result = fema.refresh_fema_sfha(conn)
    assert result["standardized_sfha_polygons"] == 10_500
    assert result["response_coverage"]["county_envelopes_verified"] == 30
    controls = [call["params"] for call in calls if call["params"].get("returnCountOnly") or call["params"].get("returnIdsOnly")]
    assert len(controls) == 120
    assert all(parameters["where"] == "SFHA_TF='T'" and parameters["geometry"] for parameters in controls)
    assert conn.execute("SELECT status FROM ingestion_run WHERE run_id=?", [result["run_id"]]).fetchone() == ("SUCCEEDED_WITH_WARNINGS",)
