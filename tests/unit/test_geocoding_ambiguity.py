"""Synthetic Census-shaped responses; no network or real addresses."""
import json
from pathlib import Path

import httpx
import pytest

from nyc_lens.db import db_session
from nyc_lens.demo import seed_demo
from nyc_lens.geocoding.service import geocode_address
from nyc_lens.util import sha256_file


ADDRESS = '100 Fictional Road, Newark, NJ'
MATCH = {'matchedAddress': ADDRESS, 'coordinates': {'x': -74.17, 'y': 40.735},
         'geographies': {'Counties': [{'GEOID': '34013'}]}}


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setenv('NYC_LENS_HOME', str(tmp_path))
    monkeypatch.delenv('NYC_LENS_DATABASE', raising=False)
    seed_demo(tmp_path)
    with db_session() as conn:
        yield conn


def transport(matches, calls):
    def handle(request):
        calls.append(request)
        return httpx.Response(200, request=request, json={'result': {'addressMatches': matches}})
    return httpx.MockTransport(handle)


@pytest.mark.parametrize('legacy', [False, True])
def test_unique_current_or_verified_legacy_cache_needs_no_new_lookup(catalog, legacy):
    calls = []
    with httpx.Client(transport=transport([MATCH], calls)) as client:
        first = geocode_address(catalog, ADDRESS, client=client, max_attempts=1)
        if legacy:
            catalog.execute("UPDATE address SET method_version='0.2.0' WHERE address_id=?", [first['address_id']])
        second = geocode_address(catalog, ADDRESS, client=client, max_attempts=1)
    assert len(calls) == 1 and second['address_id'] == first['address_id']
    assert second['source_id'] == 'census_geocoder'


def test_ambiguous_legacy_cache_preserves_history_but_cannot_reuse_first_match(catalog):
    calls = []
    with httpx.Client(transport=transport([MATCH], calls)) as client:
        first = geocode_address(catalog, ADDRESS, client=client, max_attempts=1)
        catalog.execute("UPDATE address SET method_version='0.2.0' WHERE address_id=?", [first['address_id']])
        path = Path(catalog.execute('SELECT raw_path FROM dataset_snapshot WHERE snapshot_id=?', [first['snapshot_id']]).fetchone()[0])
        path.write_text(json.dumps({'result': {'addressMatches': [MATCH, {**MATCH, 'matchedAddress': '200 Fictional Road'}]}}), encoding='utf-8')
        catalog.execute('UPDATE dataset_snapshot SET sha256=? WHERE snapshot_id=?', [sha256_file(path), first['snapshot_id']])
        with pytest.raises(ValueError, match='ADDRESS_MATCH_AMBIGUOUS'):
            geocode_address(catalog, ADDRESS, client=client, max_attempts=1)
    assert len(calls) == 1
    assert catalog.execute('SELECT count(*) FROM address WHERE address_id=?', [first['address_id']]).fetchone()[0] == 1


@pytest.mark.parametrize('invalid', [None, {}, ['invalid'], []])
def test_invalid_or_empty_response_cannot_publish_address(catalog, invalid):
    with httpx.Client(transport=transport(invalid, [])) as client:
        with pytest.raises(ValueError):
            geocode_address(catalog, ADDRESS, client=client, max_attempts=1)
    assert catalog.execute("SELECT count(*) FROM address WHERE source_id='census_geocoder'").fetchone()[0] == 0
    assert catalog.execute("SELECT count(*) FROM dataset_snapshot WHERE source_id='census_geocoder'").fetchone()[0] == 1
    assert catalog.execute("SELECT status FROM ingestion_run WHERE source_id='census_geocoder'").fetchone()[0] == 'FAILED'


def test_unverifiable_legacy_snapshot_requires_a_new_response_not_old_first_match(catalog):
    calls = []
    with httpx.Client(transport=transport([MATCH], calls)) as client:
        first = geocode_address(catalog, ADDRESS, client=client, max_attempts=1)
    catalog.execute("UPDATE address SET method_version='0.2.0' WHERE address_id=?", [first['address_id']])
    catalog.execute("UPDATE dataset_snapshot SET sha256='different' WHERE snapshot_id=?", [first['snapshot_id']])
    with httpx.Client(transport=transport([MATCH, MATCH], calls)) as client:
        with pytest.raises(ValueError, match='ADDRESS_MATCH_AMBIGUOUS'):
            geocode_address(catalog, ADDRESS, client=client, max_attempts=1)
    assert len(calls) == 2
