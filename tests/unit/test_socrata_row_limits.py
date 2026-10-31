"""Frozen bounded-response contracts; synthetic downloads, real snapshots/CSV/DB."""
import json

import pandas as pd
import pytest

from test_socrata_publication import (
    SPECS, active_state, database, input_frame, seed,
)
from nyc_lens.ingest import socrata
from nyc_lens.provenance import save_bytes_snapshot
from nyc_lens.util import sha256_file


REAL_DOWNLOAD = socrata.download_socrata_csv


def install_response(monkeypatch, content, *, occupied_audit=False):
    requests, snapshots = [], []
    def download(conn, *, source_id, vintage, run_id, filename, url, params, **kwargs):
        requests.append(dict(params))
        payload = content(params) if callable(content) else content
        snapshot = save_bytes_snapshot(conn, source_id=source_id, vintage=vintage,
            run_id=run_id, filename=filename, content=payload, source_url='synthetic://rows',
            content_type='text/csv', query=params, **kwargs)
        snapshots.append(snapshot)
        if occupied_audit:
            snapshot.path.with_name('row-limit-check.json').write_text('existing evidence', encoding='utf-8')
        return snapshot
    monkeypatch.setattr(socrata, 'download_snapshot', download)
    return requests, snapshots


def audit(snapshot):
    result = json.loads(snapshot.path.with_name('row-limit-check.json').read_text(encoding='utf-8'))
    assert result['snapshot_id'] == snapshot.snapshot_id
    assert result['snapshot_sha256'] == sha256_file(snapshot.path)
    assert result['full_upstream_snapshot_verified'] is False
    return result


def test_one_extra_record_detects_silent_truncation(database, monkeypatch):
    conn, _ = database
    frame = pd.DataFrame({'id': range(5), 'label': ['synthetic'] * 5})
    requests, snapshots = install_response(monkeypatch,
        lambda params: frame.head(params['$limit']).to_csv(index=False).encode())
    with pytest.raises(ValueError, match='SOCRATA_ROW_LIMIT_EXCEEDED'):
        REAL_DOWNLOAD(conn, 'dohmh_restaurants', 'test', None, limit=4)
    assert requests == [{'$limit': 5}]
    result = audit(snapshots[-1])
    assert result['status'] == 'ROW_LIMIT_EXCEEDED'
    assert result['rows_read'] == 5
    assert result['row_limit'] == 4


def test_exact_limit_accepts_eof_and_preserves_all_query_clauses(database, monkeypatch):
    conn, _ = database
    requests, snapshots = install_response(monkeypatch, b'area,total\nA,10\nB,20\n')
    snapshot = REAL_DOWNLOAD(conn, 'dohmh_restaurants', 'test', None,
        select='area,sum(value) as total', where='year=2026', group='area', order='area', limit=2)
    assert requests == [{'$limit': 3, '$select': 'area,sum(value) as total', '$where': 'year=2026',
                         '$group': 'area', '$order': 'area'}]
    result = audit(snapshot)
    assert result['status'] == 'WITHIN_REQUESTED_LIMIT'
    assert result['rows_read'] == 2
    assert result['csv_eof_observed'] is True
    assert snapshot == snapshots[-1]


@pytest.mark.parametrize('payload,rows', [
    (b'id,label\n', 0),
    (b'id,label\n1,"line one\nline two"\n2,last\n', 2),
    (b'\xef\xbb\xbfid,label\n1,synthetic\n\n', 1),
    (b'count\n9000000\n', 1),
])
def test_counts_csv_records_not_lines_or_aggregate_values(database, monkeypatch, payload, rows):
    conn, _ = database
    install_response(monkeypatch, payload)
    snapshot = REAL_DOWNLOAD(conn, 'dohmh_restaurants', 'test', None, limit=2)
    result = audit(snapshot)
    assert result['rows_read'] == rows
    assert result['csv_eof_observed'] is True
    assert result['status'] == 'WITHIN_REQUESTED_LIMIT'


@pytest.mark.parametrize('limit', [True, False, 0, -1, 1.5, '2', None])
def test_invalid_limit_fails_before_network_or_snapshot(database, monkeypatch, limit):
    conn, _ = database
    requests, snapshots = install_response(monkeypatch, b'id\n1\n')
    with pytest.raises(ValueError, match='SOCRATA_ROW_LIMIT_INVALID'):
        REAL_DOWNLOAD(conn, 'dohmh_restaurants', 'test', None, limit=limit)
    assert requests == snapshots == []
    assert conn.execute('SELECT count(*) FROM dataset_snapshot').fetchone() == (0,)


@pytest.mark.parametrize('payload', [b'', b'id,label\n1,"unterminated\n'])
def test_invalid_csv_is_retained_but_never_accepted_as_empty(database, monkeypatch, payload):
    conn, _ = database
    _, snapshots = install_response(monkeypatch, payload)
    with pytest.raises(ValueError, match='SOCRATA_CSV_UNVERIFIED'):
        REAL_DOWNLOAD(conn, 'dohmh_restaurants', 'test', None, limit=2)
    assert snapshots[-1].path.read_bytes() == payload
    result = audit(snapshots[-1])
    assert result['status'] == 'CSV_UNVERIFIED'
    assert result['csv_eof_observed'] is False


def test_existing_check_record_cannot_be_overwritten(database, monkeypatch):
    conn, _ = database
    _, snapshots = install_response(monkeypatch, b'id\n1\n', occupied_audit=True)
    with pytest.raises(FileExistsError):
        REAL_DOWNLOAD(conn, 'dohmh_restaurants', 'test', None, limit=2)
    assert snapshots[-1].path.with_name('row-limit-check.json').read_text() == 'existing evidence'


@pytest.mark.parametrize('kind', SPECS)
def test_truncated_refresh_preserves_prior_facts_lineage_freshness_and_exports(database, monkeypatch, kind):
    conn, _ = database
    baseline, exports = seed(database, monkeypatch, kind)
    response = pd.concat([input_frame(kind, 2), input_frame(kind, 3)], ignore_index=True)
    requests, snapshots = install_response(monkeypatch,
        lambda params: response.head(params['$limit']).to_csv(index=False).encode())
    def limited(conn, source, vintage, run, **kwargs):
        return REAL_DOWNLOAD(conn, source, vintage, run, **{**kwargs, 'limit': 1})
    monkeypatch.setattr(socrata, 'download_socrata_csv', limited)
    with pytest.raises(ValueError, match='SOCRATA_ROW_LIMIT_EXCEEDED'):
        SPECS[kind][1](conn)
    assert active_state(conn, kind) == baseline
    assert all(sha256_file(path) == digest for path, digest in exports.items())
    assert conn.execute('SELECT status FROM ingestion_run ORDER BY started_at DESC LIMIT 1').fetchone() == ('FAILED',)
    assert audit(snapshots[-1])['status'] == 'ROW_LIMIT_EXCEEDED'


def test_empty_checked_hotel_refresh_is_audited_and_can_publish(database, monkeypatch):
    conn, _ = database
    seed(database, monkeypatch, 'hotels')
    _, snapshots = install_response(monkeypatch, input_frame('hotels', 2).iloc[:0].to_csv(index=False).encode())
    monkeypatch.setattr(socrata, 'download_socrata_csv', REAL_DOWNLOAD)
    socrata.refresh_hotels(conn)
    assert conn.execute("SELECT count(*) FROM hotel WHERE source_id='nyc_hotels'").fetchone() == (0,)
    assert audit(snapshots[-1])['rows_read'] == 0
    assert audit(snapshots[-1])['status'] == 'WITHIN_REQUESTED_LIMIT'
