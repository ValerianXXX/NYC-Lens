"""Replay retained public FEMA bytes offline; no queries or database writes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd

from nyc_lens.ingest.fema_query import checked_count, checked_ids, checked_page, layer_chunk_size, read_snapshot_response


def main() -> None:
    public = Path(__file__).resolve().parents[1] / 'reports/evaluations/2026-10-31/fema-membership/public'
    index = json.loads((public / 'response-index.json').read_text(encoding='utf-8'))

    def read(name):
        entry = index['files'][name]
        path = public / name
        raw = path.read_bytes()
        if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError(f'Retained public response hash mismatch: {name}')
        return read_snapshot_response(SimpleNamespace(path=path, byte_size=entry['bytes'], sha256=entry['sha256']), max_bytes=2_000_000)

    chunk_size = layer_chunk_size(read('layer-metadata.response'))
    count = checked_count(read('tiny-count-before.response'))
    ids = checked_ids(read('tiny-ids-before.response'), count)
    features = checked_page(read('tiny-features.response'), sorted(ids), observed={}, identity_owners={})
    if checked_ids(read('tiny-ids-after.response'), count) != ids or checked_count(read('tiny-count-after.response')) != count:
        raise ValueError('Retained before/after membership differs')
    frame = gpd.GeoDataFrame.from_features(features, crs='EPSG:4326')
    if len(frame) != count or not bool(frame.geometry.is_valid.all()):
        raise ValueError('Retained tiny geometry is invalid')
    observation = json.loads((public / 'same-ids-observation.json').read_text(encoding='utf-8'))
    requested = list(map(int, observation['parameters']['objectIds'].split(',')))
    diagnostic = checked_page(read('same-ids-features.response'), requested, observed={}, identity_owners={})
    print(json.dumps({'status': 'RETAINED_PUBLIC_BYTES_OFFLINE_VALIDATION_PASS',
        'tinyExactMembers': len(features), 'singleQueryExactMembers': len(diagnostic),
        'candidateChunkSize': chunk_size, 'networkCalls': 0, 'databaseWrites': 0,
        'fullRegionalAcceptance': False, 'locationConclusion': False}, indent=2))


if __name__ == '__main__':
    main()
