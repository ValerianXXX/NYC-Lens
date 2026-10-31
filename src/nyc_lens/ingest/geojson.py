"""Response coverage checks for the full Socrata and bounded TIGERweb extracts.

These checks do not create an atomic snapshot of a changing upstream service.
Raw responses, including rejected HTTP-200 error bodies, remain append-only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re
from typing import Any, Callable

import duckdb
import httpx

from ..config import get_source
from ..paths import data_dir
from ..provenance import Snapshot, save_bytes_snapshot
from ..util import utc_iso, utc_now


class CompletenessError(ValueError):
    """A stable error category; never includes an untrusted response body."""


@dataclass(frozen=True)
class CheckedGeoJSON:
    snapshot: Snapshot
    feature_count: int
    check_path: Path
    request_count: int
    downloaded_bytes: int


class _Check:
    def __init__(self, conn: duckdb.DuckDBPyConnection, source_id: str, vintage: str,
                 run_id: str, method: str):
        self.conn, self.source_id, self.vintage, self.run_id = conn, source_id, vintage, run_id
        self.method = method
        self.responses: list[dict[str, Any]] = []
        self.details: dict[str, Any] = {}
        self.request_count = 0
        self.audit_started = False

    def remember(self, snapshot: Snapshot, role: str) -> Snapshot:
        self.responses.append({"role": role, "snapshot_id": snapshot.snapshot_id,
            "raw_path": str(snapshot.path), "sha256": snapshot.sha256,
            "byte_size": snapshot.byte_size})
        return snapshot

    def request_json(self, url: str, parameters: dict[str, Any], role: str, *, post=False):
        self.request_count += 1
        kwargs = {"follow_redirects": True, "timeout": 180,
                  "headers": {"User-Agent": "NYC-Lens/0.2 local-research"}}
        response = (httpx.post(url, data=parameters, **kwargs) if post
                    else httpx.get(url, params=parameters, **kwargs))
        snapshot = save_bytes_snapshot(self.conn, source_id=self.source_id, vintage=self.vintage,
            filename=role + (".geojson" if parameters.get("f") == "geojson" else ".json"),
            content=response.content, source_url=url, run_id=self.run_id,
            query=parameters, http_status=response.status_code,
            content_type="application/geo+json" if parameters.get("f") == "geojson" else "application/json")
        self.remember(snapshot, role)
        if not response.is_success:
            raise CompletenessError("COMPLETENESS_HTTP_ERROR")
        return _read_json(snapshot)

    def audit(self, status: str, code: str) -> Path:
        # Exclusive creation: never replace a previous validation receipt.
        self.audit_started = True
        directory = (Path(self.responses[-1]["raw_path"]).parent if self.responses else
                     data_dir() / "raw" / self.source_id / "checks" / self.run_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "completeness-check.json"
        with path.open("x", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "source_id": self.source_id, "run_id": self.run_id,
                "method": self.method, "status": status, "code": code,
                "checked_at": utc_iso(utc_now()), "stable_upstream_snapshot": False,
                "request_count": self.request_count, "responses": self.responses,
                **self.details}, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        return path

    def accepted(self, snapshot: Snapshot, count: int) -> CheckedGeoJSON:
        self.details.update(feature_count=count, published_snapshot_id=snapshot.snapshot_id)
        return CheckedGeoJSON(snapshot, count,
            self.audit("VERIFIED_RESPONSE_COVERAGE", "COMPLETENESS_CHECKED"),
            self.request_count, sum(item["byte_size"] for item in self.responses))

    def rejected(self, error: Exception) -> None:
        error.geojson_request_count = self.request_count
        error.geojson_downloaded_bytes = sum(item["byte_size"] for item in self.responses)
        self.details["error_type"] = type(error).__name__
        code = str(error) if isinstance(error, CompletenessError) else "COMPLETENESS_UNVERIFIED"
        if not self.audit_started:
            try:
                self.audit("REJECTED", code)
            except Exception as audit_error:
                # Publication still fails when the diagnostic cannot be saved.
                # Keep the original attempt totals on the outward storage error.
                audit_error.geojson_request_count = error.geojson_request_count
                audit_error.geojson_downloaded_bytes = error.geojson_downloaded_bytes
                raise


def _read_json(snapshot: Snapshot) -> Any:
    def invalid_constant(value):
        raise ValueError("Non-finite JSON value")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    try:
        return json.loads(snapshot.path.read_text(encoding="utf-8-sig"),
                          parse_constant=invalid_constant, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError) as error:
        raise CompletenessError("COMPLETENESS_INVALID_JSON") from error


def failed_download_progress(error: Exception) -> tuple[int, int]:
    """Additional helper requests/receipts when no CheckedGeoJSON was returned."""
    return (getattr(error, "geojson_request_count", 0),
            getattr(error, "geojson_downloaded_bytes", 0))


def _no_error_or_limit(payload: Any) -> None:
    if not isinstance(payload, dict) or "error" in payload:
        raise CompletenessError("COMPLETENESS_UPSTREAM_ERROR")
    containers = [payload]
    if isinstance(payload.get("properties"), dict):
        containers.append(payload["properties"])
    for container in containers:
        if "exceededTransferLimit" in container and container["exceededTransferLimit"] is not False:
            raise CompletenessError("COMPLETENESS_TRANSFER_LIMIT")


def _features(payload: Any) -> list[dict[str, Any]]:
    _no_error_or_limit(payload)
    if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        raise CompletenessError("COMPLETENESS_INVALID_FEATURE_COLLECTION")
    for feature in payload["features"]:
        if (not isinstance(feature, dict) or feature.get("type") != "Feature"
                or not isinstance(feature.get("properties"), dict)
                or not isinstance(feature.get("geometry"), dict)
                or feature["geometry"].get("type") not in {"Polygon", "MultiPolygon"}
                or not feature["geometry"].get("coordinates")):
            raise CompletenessError("COMPLETENESS_INVALID_GEOMETRY_FEATURE")
    return payload["features"]


def _count(payload: Any) -> int:
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise CompletenessError("COMPLETENESS_INVALID_COUNT")
    value = payload[0].get("count")
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value):
        return int(value)
    raise CompletenessError("COMPLETENESS_INVALID_COUNT")


def download_socrata_geojson(conn: duckdb.DuckDBPyConnection, *, source_id: str,
        vintage: str, run_id: str, limit: int, timeout_seconds: float,
        download: Callable[..., Snapshot]) -> CheckedGeoJSON:
    if type(limit) is not int or limit < 1:
        raise CompletenessError("COMPLETENESS_INVALID_LIMIT")
    source = get_source(source_id)
    url = source["api_url"]
    if not url.endswith(".geojson"):
        raise CompletenessError("COMPLETENESS_UNSUPPORTED_SOCRATA_URL")
    check = _Check(conn, source_id, vintage, run_id, "socrata-count-before-response-count-after")
    count_url = url.removesuffix(".geojson") + ".json"
    parameters = {"$select": "count(*) AS count"}
    try:
        before = _count(check.request_json(count_url, parameters, "count-before"))
        check.details.update(expected_count=before, configured_limit=limit)
        if before > limit:
            raise CompletenessError("COMPLETENESS_ROW_BUDGET_EXCEEDED")
        check.request_count += 1
        snapshot = check.remember(download(conn, source_id=source_id, vintage=vintage,
            url=url, filename=f"{source['dataset_id']}.geojson", run_id=run_id,
            params={"$limit": limit + 1}, timeout_seconds=timeout_seconds), "features")
        features = _features(_read_json(snapshot))
        check.details["feature_count"] = len(features)
        if len(features) != before:
            raise CompletenessError("COMPLETENESS_COUNT_MISMATCH")
        after = _count(check.request_json(count_url, parameters, "count-after"))
        check.details["final_count"] = after
        if before != after:
            raise CompletenessError("COMPLETENESS_UPSTREAM_COUNT_CHANGED")
        return check.accepted(snapshot, len(features))
    except Exception as error:
        check.rejected(error)
        raise


def _object_ids(payload: Any, max_features: int) -> tuple[str, set[int]]:
    _no_error_or_limit(payload)
    field, values = payload.get("objectIdFieldName"), payload.get("objectIds")
    if (not isinstance(field, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", field)
            or not isinstance(values, list) or any(type(value) is not int or value < 0 for value in values)
            or len(set(values)) != len(values)):
        raise CompletenessError("COMPLETENESS_INVALID_OBJECT_IDS")
    if len(values) > max_features:
        raise CompletenessError("COMPLETENESS_ROW_BUDGET_EXCEEDED")
    return field, set(values)


def download_arcgis_geojson(conn: duckdb.DuckDBPyConnection, *, source_id: str,
        vintage: str, run_id: str, spatial_query: dict[str, str],
        valid_from: date, max_features: int = 1000, chunk_size: int = 100) -> CheckedGeoJSON:
    if (type(max_features) is not int or not 1 <= max_features <= 1000
            or type(chunk_size) is not int or not 1 <= chunk_size <= 100):
        raise CompletenessError("COMPLETENESS_INVALID_LIMIT")
    url = get_source(source_id)["api_url"]
    check = _Check(conn, source_id, vintage, run_id, "arcgis-id-set-before-chunks-id-set-after")
    id_query = {**spatial_query, "returnIdsOnly": "true", "f": "json"}
    try:
        field, ids = _object_ids(check.request_json(url, id_query, "ids-before", post=True), max_features)
        check.details.update(expected_count=len(ids), configured_limit=max_features,
                             object_id_field=field, chunk_size=chunk_size)
        if not ids:
            raise CompletenessError("COMPLETENESS_EMPTY_OBJECT_IDS")
        ordered_ids = sorted(ids)
        features = []
        for start in range(0, len(ordered_ids), chunk_size):
            requested = ordered_ids[start:start + chunk_size]
            query = {"objectIds": ",".join(map(str, requested)), "outFields": "*",
                     "returnGeometry": "true", "outSR": "4326", "f": "geojson"}
            page = _features(check.request_json(url, query, f"features-{start // chunk_size + 1:03d}", post=True))
            returned = [feature["properties"].get(field) for feature in page]
            if (any(type(value) is not int for value in returned)
                    or len(returned) != len(set(returned)) or set(returned) != set(requested)):
                raise CompletenessError("COMPLETENESS_OBJECT_ID_MISMATCH")
            features.extend(page)
        final_field, final_ids = _object_ids(check.request_json(url, id_query, "ids-after", post=True), max_features)
        if final_field != field or ids != final_ids:
            raise CompletenessError("COMPLETENESS_UPSTREAM_OBJECT_IDS_CHANGED")
        # The derived GeoJSON links every raw manifest/page receipt. It is not a
        # second network download and is never used to overwrite raw responses.
        snapshot = save_bytes_snapshot(conn, source_id=source_id, vintage=vintage,
            filename="nyc_bbox_zcta.geojson", run_id=run_id, source_url=url,
            content=json.dumps({"type": "FeatureCollection", "features": features}).encode(),
            content_type="application/geo+json", valid_from=valid_from,
            query={"kind": "assembled verified object-ID chunks", "spatial_query": spatial_query,
                   "upstream_snapshot_ids": [item["snapshot_id"] for item in check.responses]},
            warnings=["Bounded bbox extract, not the full national dataset or a transactional upstream snapshot."])
        return check.accepted(snapshot, len(features))
    except Exception as error:
        check.rejected(error)
        raise
