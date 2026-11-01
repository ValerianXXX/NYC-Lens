"""Shared response-shape checks; no transport, database or publication effects."""
from __future__ import annotations

import json
import re
from typing import Any


class CompletenessError(ValueError):
    """A stable error category; never includes an untrusted response body."""


def parse_json_response(content: bytes | str) -> Any:
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
        text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
        return json.loads(text, parse_constant=invalid_constant, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError) as error:
        raise CompletenessError("COMPLETENESS_INVALID_JSON") from error


def no_error_or_limit(payload: Any) -> None:
    if not isinstance(payload, dict) or "error" in payload:
        raise CompletenessError("COMPLETENESS_UPSTREAM_ERROR")
    containers = [payload]
    if isinstance(payload.get("properties"), dict):
        containers.append(payload["properties"])
    for container in containers:
        if "exceededTransferLimit" in container and container["exceededTransferLimit"] is not False:
            raise CompletenessError("COMPLETENESS_TRANSFER_LIMIT")


def polygon_features(payload: Any) -> list[dict[str, Any]]:
    no_error_or_limit(payload)
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


def object_ids(payload: Any, max_features: int) -> tuple[str, set[int]]:
    no_error_or_limit(payload)
    field, values = payload.get("objectIdFieldName"), payload.get("objectIds")
    if (not isinstance(field, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", field)
            or not isinstance(values, list) or any(type(value) is not int or value < 0 for value in values)
            or len(set(values)) != len(values)):
        raise CompletenessError("COMPLETENESS_INVALID_OBJECT_IDS")
    if len(values) > max_features:
        raise CompletenessError("COMPLETENESS_ROW_BUDGET_EXCEEDED")
    return field, set(values)


def exact_feature_membership(payload: Any, field: str, requested: list[int]) -> list[dict[str, Any]]:
    features = polygon_features(payload)
    returned = [feature["properties"].get(field) for feature in features]
    if (any(type(value) is not int for value in returned)
            or len(returned) != len(set(returned)) or set(returned) != set(requested)):
        raise CompletenessError("COMPLETENESS_OBJECT_ID_MISMATCH")
    return features
