"""FEMA query contracts and identity checks, independent of publication."""
from __future__ import annotations

import hashlib
import json
from numbers import Integral
from typing import Any, Iterator

from .source_response import (CompletenessError, exact_feature_membership,
                              no_error_or_limit, object_ids, parse_json_response)


MAX_ENVELOPE_FEATURES = 250_000
MAX_TOTAL_MEMBERSHIPS = 1_000_000
MAX_OBJECT_ID = (1 << 63) - 1
CONTROL_BYTES = 8_000_000
PAGE_BYTES = 128_000_000
OUT_FIELDS = "OBJECTID,DFIRM_ID,FLD_AR_ID,FLD_ZONE,ZONE_SUBTY,SFHA_TF,GFID,GlobalID"


def read_snapshot_response(snapshot, *, max_bytes: int = CONTROL_BYTES) -> Any:
    # Parse and later construct geometry from these same verified bytes. Do not
    # validate one read and then let a second path-based parser read a new file.
    with snapshot.path.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise CompletenessError("COMPLETENESS_RESPONSE_BYTE_LIMIT")
    if len(raw) != snapshot.byte_size or hashlib.sha256(raw).hexdigest() != snapshot.sha256:
        raise CompletenessError("COMPLETENESS_RESPONSE_HASH_MISMATCH")
    return parse_json_response(raw)


def layer_chunk_size(payload: Any, maximum: int = 500) -> int:
    no_error_or_limit(payload)
    fields, formats, capabilities = payload.get("fields"), payload.get("supportedQueryFormats"), payload.get("capabilities")
    limit = payload.get("maxRecordCount")
    if (payload.get("type") != "Feature Layer" or payload.get("geometryType") != "esriGeometryPolygon"
            or type(limit) is not int or not 1 <= limit <= MAX_OBJECT_ID
            or not isinstance(formats, str) or "geojson" not in {part.strip().lower() for part in formats.split(",")}
            or not isinstance(capabilities, str) or not {"Query", "Data"} <= {part.strip() for part in capabilities.split(",")}
            or not isinstance(fields, list) or any(not isinstance(field, dict) for field in fields)):
        raise CompletenessError("FEMA_LAYER_CONTRACT_INVALID")
    names = [field.get("name") for field in fields]
    if (any(not isinstance(name, str) for name in names) or len(names) != len(set(names))
            or not set(OUT_FIELDS.split(",")) <= set(names)
            or [field.get("name") for field in fields if field.get("type") == "esriFieldTypeOID"] != ["OBJECTID"]):
        raise CompletenessError("FEMA_LAYER_CONTRACT_INVALID")
    return min(maximum, limit)


def checked_count(payload: Any) -> int:
    no_error_or_limit(payload)
    count = payload.get("count")
    if type(count) is not int or count < 0:
        raise CompletenessError("COMPLETENESS_INVALID_COUNT")
    if count > MAX_ENVELOPE_FEATURES:
        raise CompletenessError("COMPLETENESS_ROW_BUDGET_EXCEEDED")
    return count


def checked_ids(payload: Any, count: int) -> set[int]:
    field, ids = object_ids(payload, MAX_ENVELOPE_FEATURES)
    if field != "OBJECTID" or any(value > MAX_OBJECT_ID for value in ids):
        raise CompletenessError("COMPLETENESS_INVALID_OBJECT_IDS")
    if len(ids) != count:
        raise CompletenessError("COMPLETENESS_COUNT_MISMATCH")
    return ids


def id_chunks(ids: set[int], chunk_size: int) -> Iterator[list[int]]:
    # Bound both server record count and GET parameter size, including 64-bit
    # IDs. The fixed remaining query parameters keep encoded URLs below 8 KiB.
    chunk, characters = [], 0
    for value in sorted(ids):
        width = len(str(value)) + (1 if chunk else 0)
        if chunk and (len(chunk) == chunk_size or characters + width > 3200):
            yield chunk
            chunk, characters, width = [], 0, len(str(value))
        chunk.append(value)
        characters += width
    if chunk:
        yield chunk


def source_feature_id(properties) -> str:
    def text(name):
        value = properties.get(name)
        return value.strip() if isinstance(value, str) else ""
    global_id, area_id, community = text("GlobalID"), text("FLD_AR_ID"), text("DFIRM_ID")
    if global_id:
        return global_id
    if area_id:
        return f"{community}:{area_id}" if community else area_id
    # GFID can repeat across different flood areas and is never a dedup key.
    oid = properties.get("OBJECTID")
    if isinstance(oid, bool) or not isinstance(oid, Integral) or not 0 <= oid <= MAX_OBJECT_ID:
        raise CompletenessError("FEMA_SOURCE_IDENTIFIER_MISSING")
    return f"{community}:{oid}" if community else f"OBJECTID:{oid}"


def checked_page(payload: Any, requested: list[int], *, observed: dict[int, str],
                 identity_owners: dict[str, int]) -> list[dict[str, Any]]:
    features = exact_feature_membership(payload, "OBJECTID", requested)
    crs = payload.get("crs")
    if crs is not None and (not isinstance(crs, dict) or crs.get("type") != "name"
            or not isinstance(crs.get("properties"), dict)
            or crs["properties"].get("name") not in {"EPSG:4326", "OGC:CRS84",
                "urn:ogc:def:crs:OGC::CRS84", "urn:ogc:def:crs:OGC:1.3:CRS84"}):
        raise CompletenessError("FEMA_RESPONSE_CRS_MISMATCH")
    updates, identities = {}, {}
    for feature in features:
        properties = feature["properties"]
        oid = properties["OBJECTID"]
        if properties.get("SFHA_TF") != "T":
            raise CompletenessError("FEMA_SFHA_FILTER_MISMATCH")
        if "id" in feature and (type(feature["id"]) is not int or feature["id"] != oid):
            raise CompletenessError("COMPLETENESS_OBJECT_ID_MISMATCH")
        fingerprint = hashlib.sha256(json.dumps({"properties": properties, "geometry": feature["geometry"]},
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        if oid in observed and observed[oid] != fingerprint:
            raise CompletenessError("FEMA_REPEATED_OBJECT_CHANGED")
        identity = source_feature_id(properties)
        owner = identities.get(identity, identity_owners.get(identity))
        if owner is not None and owner != oid:
            raise CompletenessError("FEMA_SOURCE_ID_COLLISION")
        updates[oid], identities[identity] = fingerprint, oid
    observed.update(updates)
    identity_owners.update(identities)
    return features
