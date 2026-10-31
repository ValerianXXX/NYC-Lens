"""Frozen upstream counterexamples; all geometry and catalog operations are real.

Only public HTTP responses are replaced by explicitly synthetic fixtures.
"""
import copy
import json
from pathlib import Path

import httpx
import pytest

from test_boundary_publication import (
    SOURCES, active_state, database, input_frame, refresh, seed,
)
from nyc_lens.ingest import environment, geographies
from nyc_lens.provenance import save_bytes_snapshot
from nyc_lens.util import sha256_file


def install_source(monkeypatch, kind, variant, calls):
    frame = input_frame(kind, 2)
    payload = json.loads(frame.to_json())
    count = len(frame)
    ids = list(range(1, count + 1))
    count_reads = 0
    id_reads = 0

    def get(url, **kwargs):
        nonlocal count_reads
        count_reads += 1
        calls.append(("count", kwargs["params"]))
        value = count + (1 if variant == "short" or (variant == "drift" and count_reads == 2) else 0)
        if variant == "over_budget":
            value = 100_001
        result = [{"count": str(value)}]
        if variant == "boolean_count":
            result = [{"count": True}]
        if variant == "fractional_count":
            result = [{"count": "250.5"}]
        if variant == "count_error":
            result = {"error": True, "message": "synthetic upstream failure"}
        if variant == "duplicate_count":
            result *= 2
        return httpx.Response(200, json=result, request=httpx.Request("GET", url))

    def mutate(body):
        body = copy.deepcopy(body)
        if variant == "transfer_limit":
            body["exceededTransferLimit"] = True
        if variant == "nested_transfer_limit":
            body["properties"] = {"exceededTransferLimit": True}
        if variant == "malformed_flag":
            body["exceededTransferLimit"] = "false"
        if variant == "false_flag":
            body["exceededTransferLimit"] = False
        if variant == "null_geometry_above_floor":
            extra = copy.deepcopy(body["features"][-1])
            extra["geometry"] = None
            body["features"].append(extra)
        return body

    def download(conn, *, source_id, vintage, filename, run_id, params, **kwargs):
        calls.append(("geometry", params))
        return save_bytes_snapshot(conn, source_id=source_id, vintage=vintage, filename=filename,
            content=json.dumps(mutate(payload)).encode(), source_url="synthetic://upstream",
            run_id=run_id, query=params, content_type="application/geo+json")

    def post(url, *, data, **kwargs):
        nonlocal id_reads
        if data.get("returnIdsOnly") == "true":
            id_reads += 1
            calls.append(("ids", data))
            expected = ids.copy()
            if variant == "missing_id":
                expected.append(count + 1)
            if variant == "id_drift" and id_reads == 2:
                expected[-1] = 999
            if variant == "duplicate_ids":
                expected[-1] = expected[0]
            if variant == "boolean_id":
                expected[-1] = True
            result = {"objectIdFieldName": "OBJECTID", "objectIds": expected}
            if variant == "ids_error":
                result = {"error": {"message": "synthetic upstream failure"}}
        else:
            calls.append(("features", data))
            result = copy.deepcopy(payload)
            if data.get("objectIds"):
                requested = {int(value) for value in data["objectIds"].split(",")}
                result["features"] = [feature for feature in result["features"]
                                      if feature["properties"]["OBJECTID"] in requested]
            result = mutate(result)
            if variant == "substituted_id":
                result["features"][-1]["properties"]["OBJECTID"] = 999
            if variant == "duplicate_feature_id":
                result["features"][-1]["properties"]["OBJECTID"] = 1
            if variant == "features_error":
                result = {"error": {"message": "synthetic upstream failure"}}
            if variant == "reverse_order":
                result["features"].reverse()
        return httpx.Response(200, json=result, request=httpx.Request("POST", url))

    monkeypatch.setattr(geographies.httpx, "get", get)
    monkeypatch.setattr(geographies.httpx, "post", post)
    for module in (geographies, environment):
        monkeypatch.setattr(module, "download_snapshot", download)


SOCRATA_FAILURES = ["short", "drift", "over_budget", "boolean_count", "fractional_count",
                    "count_error", "duplicate_count", "transfer_limit", "malformed_flag",
                    "null_geometry_above_floor"]
ARCGIS_FAILURES = ["transfer_limit", "nested_transfer_limit", "malformed_flag", "missing_id",
                  "substituted_id", "duplicate_feature_id", "duplicate_ids", "boolean_id",
                  "id_drift", "ids_error", "features_error"]


@pytest.mark.parametrize("kind,variant", [
    *[(kind, variant) for kind in ("nta", "hazard") for variant in SOCRATA_FAILURES],
    *[("zcta", variant) for variant in ARCGIS_FAILURES],
])
def test_incomplete_input_keeps_prior_publication_and_raw_evidence(database, monkeypatch, kind, variant):
    conn, root = database
    _, baseline, exports = seed(database, monkeypatch, kind)
    old_checks = set(root.rglob("completeness-check.json"))
    calls = []
    install_source(monkeypatch, kind, variant, calls)
    with pytest.raises(ValueError, match="COMPLETENESS_"):
        refresh(conn, kind)
    assert active_state(conn) == baseline
    assert all(sha256_file(path) == digest for path, digest in exports.items())
    assert conn.execute("SELECT status FROM ingestion_run ORDER BY started_at DESC LIMIT 1").fetchone() == ("FAILED",)
    checks = set(root.rglob("completeness-check.json")) - old_checks
    assert len(checks) == 1
    check = json.loads(checks.pop().read_text(encoding="utf-8"))
    assert check["status"] == "REJECTED" and check["code"].startswith("COMPLETENESS_")
    assert check["source_id"] == SOURCES[kind]
    assert check["responses"]
    for receipt in check["responses"]:
        path = Path(receipt["raw_path"])
        assert path.is_file() and sha256_file(path) == receipt["sha256"]
    if variant == "over_budget":
        assert [name for name, _ in calls] == ["count"]


@pytest.mark.parametrize("kind,variant", [("nta", "false_flag"), ("hazard", "false_flag"),
                                         ("zcta", "reverse_order")])
def test_complete_input_publishes_auditable_bounded_download(database, monkeypatch, kind, variant):
    conn, root = database
    _, baseline, exports = seed(database, monkeypatch, kind)
    calls = []
    install_source(monkeypatch, kind, variant, calls)
    result = refresh(conn, kind)
    assert active_state(conn) != baseline
    assert all(sha256_file(path) == digest for path, digest in exports.items())
    audit = json.loads(Path(result["completeness_check"]).read_text(encoding="utf-8"))
    assert audit["status"] == "VERIFIED_RESPONSE_COVERAGE"
    assert audit["stable_upstream_snapshot"] is False
    assert audit["feature_count"] == len(input_frame(kind, 2))
    if kind == "zcta":
        assert [name for name, _ in calls] == ["ids", "features", "ids"]
        assert len(calls[1][1]["objectIds"].split(",")) <= 100
    else:
        assert [name for name, _ in calls] == ["count", "geometry", "count"]
        assert calls[1][1]["$limit"] == (100_001 if kind == "nta" else 101)
