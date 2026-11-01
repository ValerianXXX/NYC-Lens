"""Strict public-response controls, bounded parsing and overlap consistency."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from nyc_lens.ingest import fema_query as query
from nyc_lens.ingest.source_response import CompletenessError, parse_json_response
from test_fema_membership import layer_metadata
from test_multisnapshot_publication import fema_page


@pytest.mark.parametrize("value", [None, True, False, -1, 1.5, "12", [], {}, float("inf")])
def test_count_does_not_coerce_missing_boolean_fractional_or_string_values(value):
    with pytest.raises(CompletenessError, match="COMPLETENESS_INVALID_COUNT"):
        query.checked_count({"count": value})


def test_count_zero_and_limit_are_explicit_and_over_budget_stops():
    assert query.checked_count({"count": 0}) == 0
    assert query.checked_count({"count": query.MAX_ENVELOPE_FEATURES}) == query.MAX_ENVELOPE_FEATURES
    with pytest.raises(CompletenessError, match="COMPLETENESS_ROW_BUDGET_EXCEEDED"):
        query.checked_count({"count": query.MAX_ENVELOPE_FEATURES + 1})
    assert query.checked_ids({"objectIdFieldName": "OBJECTID", "objectIds": []}, 0) == set()


@pytest.mark.parametrize("ids", [[True], ["1"], [-1], [1, 1], [1 << 63], None, {}])
def test_manifest_values_are_distinct_nonnegative_64_bit_integers(ids):
    with pytest.raises(CompletenessError, match="COMPLETENESS_INVALID_OBJECT_IDS"):
        query.checked_ids({"objectIdFieldName": "OBJECTID", "objectIds": ids}, 1)


@pytest.mark.parametrize("change", [
    {"geometryType": "esriGeometryPoint"}, {"maxRecordCount": True}, {"maxRecordCount": 0},
    {"maxRecordCount": "2000"}, {"fields": []}, {"fields": [None]},
    {"supportedQueryFormats": "JSON"}, {"capabilities": "Map"}, {"type": "Table"},
])
def test_layer_contract_requires_explicit_supported_query_and_fields(change):
    with pytest.raises(CompletenessError, match="FEMA_LAYER_CONTRACT_INVALID"):
        query.layer_chunk_size({**layer_metadata(), **change})


def test_small_layer_cap_is_respected_and_ambiguous_id_fields_are_rejected():
    assert query.layer_chunk_size({**layer_metadata(), "maxRecordCount": 3}) == 3
    metadata = layer_metadata()
    metadata["fields"].append({"name": "SECOND_ID", "type": "esriFieldTypeOID"})
    with pytest.raises(CompletenessError, match="FEMA_LAYER_CONTRACT_INVALID"):
        query.layer_chunk_size(metadata)


@pytest.mark.parametrize("value", [True, "false", 0, 1, None])
def test_transfer_flags_are_never_treated_as_false_by_coercion(value):
    with pytest.raises(CompletenessError, match="COMPLETENESS_TRANSFER_LIMIT"):
        query.checked_count({"count": 3, "properties": {"exceededTransferLimit": value}})


@pytest.mark.parametrize("raw", [b'{"count":1,"count":2}', b'{"count":NaN}',
    b'{"count":Infinity}', b'{"count":-Infinity}', b'\xff', b'not json'])
def test_duplicate_keys_nonfinite_tokens_and_invalid_encoding_fail(raw):
    with pytest.raises(CompletenessError, match="COMPLETENESS_INVALID_JSON"):
        parse_json_response(raw)


def test_upstream_error_messages_are_not_copied_into_application_errors():
    with pytest.raises(CompletenessError, match="COMPLETENESS_UPSTREAM_ERROR") as failure:
        query.checked_count({"error": {"message": "synthetic untrusted diagnostic"}})
    assert "untrusted diagnostic" not in str(failure.value)


def test_exact_bytes_hash_and_read_limit_bind_the_parsed_response(tmp_path):
    path = tmp_path / "synthetic.json"
    raw = b'{"count":3}'
    path.write_bytes(raw)
    snapshot = SimpleNamespace(path=path, byte_size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    assert query.read_snapshot_response(snapshot) == {"count": 3}
    with pytest.raises(CompletenessError, match="COMPLETENESS_RESPONSE_BYTE_LIMIT"):
        query.read_snapshot_response(snapshot, max_bytes=3)
    path.write_bytes(b'{"count":4}')
    with pytest.raises(CompletenessError, match="COMPLETENESS_RESPONSE_HASH_MISMATCH"):
        query.read_snapshot_response(snapshot)


def test_id_chunks_bound_record_and_get_parameter_lengths_without_omission():
    ids = set(range(query.MAX_OBJECT_ID - 1000, query.MAX_OBJECT_ID + 1))
    chunks = list(query.id_chunks(ids, 500))
    assert [value for chunk in chunks for value in chunk] == sorted(ids)
    assert all(len(chunk) <= 500 and len(",".join(map(str, chunk))) <= 3200 for chunk in chunks)
    assert len(list(query.id_chunks({1, 2, 3, 4}, 3))) == 2


def sample():
    payload = json.loads(fema_page(0, 1))
    payload["features"] = payload["features"][:3]
    return payload


@pytest.mark.parametrize("defect", ["missing", "duplicate", "unexpected", "boolean", "wrong_feature_id", "wrong_crs"])
def test_returned_geometry_must_belong_to_exact_requested_members(defect):
    payload = sample()
    if defect == "missing":
        payload["features"].pop()
    elif defect == "duplicate":
        payload["features"][-1] = deepcopy(payload["features"][0])
    elif defect == "unexpected":
        payload["features"][0]["properties"]["OBJECTID"] = 100
    elif defect == "boolean":
        payload["features"][0]["properties"]["OBJECTID"] = True
    elif defect == "wrong_feature_id":
        payload["features"][0]["id"] = 100
    else:
        payload["crs"] = {"type": "name", "properties": {"name": "EPSG:3857"}}
    observed, owners = {}, {}
    with pytest.raises(CompletenessError, match="COMPLETENESS_OBJECT_ID_MISMATCH|FEMA_RESPONSE_CRS_MISMATCH"):
        query.checked_page(payload, [1, 2, 3], observed=observed, identity_owners=owners)
    assert observed == owners == {}


def test_overlapping_envelopes_keep_equal_objects_and_reject_changed_content():
    observed, owners = {}, {}
    payload = sample()
    query.checked_page(payload, [1, 2, 3], observed=observed, identity_owners=owners)
    before = deepcopy((observed, owners))
    query.checked_page(payload, [3, 2, 1], observed=observed, identity_owners=owners)
    assert (observed, owners) == before
    payload["features"][0]["properties"]["FLD_ZONE"] = "VE"
    with pytest.raises(CompletenessError, match="FEMA_REPEATED_OBJECT_CHANGED"):
        query.checked_page(payload, [1, 2, 3], observed=observed, identity_owners=owners)
    assert (observed, owners) == before


def test_distinct_objects_cannot_share_one_dedup_identity():
    payload = sample()
    payload["features"][1]["properties"]["GlobalID"] = payload["features"][0]["properties"]["GlobalID"]
    observed, owners = {}, {}
    with pytest.raises(CompletenessError, match="FEMA_SOURCE_ID_COLLISION"):
        query.checked_page(payload, [1, 2, 3], observed=observed, identity_owners=owners)
    assert observed == owners == {}


def test_area_identity_is_scoped_to_community_and_gfid_is_never_a_fallback():
    assert query.source_feature_id({"DFIRM_ID": "first", "FLD_AR_ID": "area"}) != query.source_feature_id({"DFIRM_ID": "second", "FLD_AR_ID": "area"})
    assert query.source_feature_id({"OBJECTID": 3, "GFID": "group"}) == "OBJECTID:3"
    with pytest.raises(CompletenessError, match="FEMA_SOURCE_IDENTIFIER_MISSING"):
        query.source_feature_id({"GFID": "group"})
