"""Frozen response-membership contracts, using only synthetic source records.

These exercise the original 10,000-polygon publication floor. A response can
lose or repeat records and still exceed that floor; the floor is not a manifest.
"""
import json
from pathlib import Path

import pytest

from nyc_lens.ingest import fema
from nyc_lens.provenance import save_bytes_snapshot
from test_multisnapshot_publication import COUNTIES, SOURCES, active_state, database, fema_page


def layer_metadata():
    return {"name": "Synthetic flood layer", "type": "Feature Layer",
            "geometryType": "esriGeometryPolygon", "maxRecordCount": 500,
            "capabilities": "Map,Query,Data", "supportedQueryFormats": "JSON, geoJSON",
            "fields": [{"name": name, "type": "esriFieldTypeOID" if name == "OBJECTID" else "esriFieldTypeString"}
                       for name in ("OBJECTID", "DFIRM_ID", "FLD_AR_ID", "FLD_ZONE", "ZONE_SUBTY", "SFHA_TF", "GFID", "GlobalID")]}


def install_response_fixture(monkeypatch, *, defect=None, total=10_500):
    calls, id_calls = [], {}
    first_envelope = "-74.0,40.0,-73.8,40.2"

    def download(conn, *, source_id, vintage, url, filename, run_id, params=None, **_kwargs):
        params = dict(params or {})
        calls.append({"filename": filename, "params": params})
        relevant = params.get("geometry") == first_envelope
        ids = list(range(1, total + 1)) if relevant else []
        if filename == "nfhl_layer_28_metadata.json":
            payload = layer_metadata()
        elif params.get("returnCountOnly") == "true":
            payload = {"count": len(ids)}
        elif params.get("returnIdsOnly") == "true":
            envelope = params["geometry"]
            id_calls[envelope] = id_calls.get(envelope, 0) + 1
            if defect == "changed_membership" and relevant and id_calls[envelope] > 1:
                ids = [1_000_001] + ids[1:]
            payload = {"objectIdFieldName": "OBJECTID", "objectIds": list(reversed(ids))}
        else:
            if "objectIds" in params:
                requested = [int(value) for value in params["objectIds"].split(",")]
            else:
                start = params["resultOffset"]
                requested = ids[start:start + params["resultRecordCount"]]
            if defect == "repeated_page" and requested and requested[0] == 501:
                requested = list(range(1, 501))
            features, pages = [], {}
            for object_id in requested:
                offset = (object_id - 1) // 500 * 500
                if offset not in pages:
                    pages[offset] = json.loads(fema_page(offset, 1))
                features.append(pages[offset]["features"][(object_id - 1) % 500])
            if defect == "dropped_member" and requested and requested[0] == 1:
                features.pop(0)
            if defect == "wrong_filter" and requested and requested[0] == 1:
                features[0]["properties"]["SFHA_TF"] = "F"
            payload = {"type": "FeatureCollection", "features": list(reversed(features))}
            if defect == "transfer_limit" and requested and requested[0] == 1:
                payload["exceededTransferLimit"] = True
        return save_bytes_snapshot(conn, source_id=source_id, vintage=vintage,
            filename=filename, content=json.dumps(payload).encode(), source_url=url,
            run_id=run_id, query={"synthetic": True, "params": params},
            content_type="application/geo+json" if params.get("f") == "geojson" else "application/json",
            warnings=["Synthetic fixture, not FEMA or real flood evidence."], http_status=200)

    monkeypatch.setattr(fema, "download_snapshot", download)
    return calls


@pytest.mark.parametrize("defect,total,code", [
    ("dropped_member", 10_500, "COMPLETENESS_OBJECT_ID_MISMATCH"),
    ("repeated_page", 11_000, "COMPLETENESS_OBJECT_ID_MISMATCH"),
    ("changed_membership", 10_500, "COMPLETENESS_UPSTREAM_OBJECT_IDS_CHANGED"),
    ("wrong_filter", 10_500, "FEMA_SFHA_FILTER_MISMATCH"),
    ("transfer_limit", 10_500, "COMPLETENESS_TRANSFER_LIMIT"),
])
def test_incomplete_or_changed_source_cannot_publish_above_the_floor(database, monkeypatch, defect, total, code):
    conn, root = database
    baseline = active_state(conn)
    install_response_fixture(monkeypatch, defect=defect, total=total)
    with pytest.raises(ValueError, match=code):
        fema.refresh_fema_sfha(conn)
    assert active_state(conn) == baseline
    assert conn.execute("SELECT status FROM ingestion_run ORDER BY started_at DESC LIMIT 1").fetchone() == ("FAILED",)
    assert conn.execute("SELECT count(*) FROM dataset_snapshot WHERE source_id=?", [SOURCES["fema"]]).fetchone()[0] > 0
    assert not list((root / "data/standardized").rglob("*.parquet"))
    assert conn.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name='_fema_sfha_staging'").fetchone() == (0,)


def test_exact_membership_accepts_shuffled_responses_and_binds_every_control(database, monkeypatch):
    conn, _ = database
    calls = install_response_fixture(monkeypatch)
    result = fema.refresh_fema_sfha(conn)
    assert result["standardized_sfha_polygons"] == 10_500
    coverage = result["response_coverage"]
    assert coverage["method"] == "count-and-ids-before-exact-chunks-ids-and-count-after"
    assert coverage["stable_upstream_snapshot"] is False
    assert coverage["county_envelopes_verified"] == 30
    assert coverage["envelope_memberships_verified"] == 10_500
    assert coverage["unique_object_ids_observed"] == 10_500
    geometry = [call for call in calls if call["params"].get("f") == "geojson"]
    assert geometry and all("objectIds" in call["params"] and "resultOffset" not in call["params"] for call in geometry)
    assert all(len(call["params"]["objectIds"].split(",")) <= 500 for call in geometry)
    ids = [call for call in calls if call["params"].get("returnIdsOnly") == "true"]
    counts = [call for call in calls if call["params"].get("returnCountOnly") == "true"]
    assert len(ids) == len(counts) == 60
    final_geometry_index = max(index for index, call in enumerate(calls) if call in geometry)
    assert all(index > final_geometry_index for index, call in enumerate(calls) if "ids-after" in call["filename"])
    manifest = json.loads(Path(result["publication_manifest"]).read_text(encoding="utf-8"))
    assert manifest["details"]["response_coverage"] == coverage
    assert len(manifest["inputs"]) == len(calls)
    assert conn.execute("SELECT request_count FROM ingestion_run WHERE run_id=?", [result["run_id"]]).fetchone() == (len(calls),)
