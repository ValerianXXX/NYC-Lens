from datetime import UTC, datetime, timedelta, timezone

import duckdb
import pytest

from nyc_lens.source_observation import SourceObservationCapture, observation_columns


@pytest.fixture
def catalog():
    with duckdb.connect(':memory:') as conn:
        conn.execute('CREATE TABLE dataset_snapshot(snapshot_id VARCHAR, source_id VARCHAR, retrieved_at TIMESTAMP, valid_from DATE, valid_to DATE, vintage VARCHAR, status VARCHAR)')
        conn.execute("INSERT INTO dataset_snapshot VALUES ('one', 'source', '2020-01-02 00:00:00', '2018-01-01', '2018-12-31', '2018', 'SUCCEEDED')")
        yield conn


def test_observation_freezes_dates_at_the_analysis_time_and_never_claims_expiry(catalog):
    capture = SourceObservationCapture(catalog, observed_at=datetime(2020, 1, 3, tzinfo=UTC))
    observation = capture.capture('source', 'one')
    assert observation['retrieved_at'] == '2020-01-02T00:00:00+00:00'
    assert observation['retrieval_age_days'] == 1
    assert observation['reference_end'] == '2018-12-31'
    assert observation['currentness'] == 'NOT_ASSESSED'
    catalog.execute("UPDATE dataset_snapshot SET retrieved_at=now(), valid_to=NULL")
    assert capture.capture('source', 'one') == observation


@pytest.mark.parametrize('source,snapshot,state', [('source', None, 'MISSING'), ('source', 'absent', 'MISSING'),
    ('different', 'one', 'SOURCE_MISMATCH'), ('derived', None, 'DERIVED')])
def test_absent_mismatched_and_derived_sources_do_not_borrow_dates(catalog, source, snapshot, state):
    observation = SourceObservationCapture(catalog).capture(source, snapshot)
    assert observation['capture_state'] == state
    assert observation['retrieved_at'] is None and observation['reference_start'] is None
    assert observation['currentness'] == 'NOT_ASSESSED'


def test_future_timestamp_has_no_fabricated_zero_age(catalog):
    observation = SourceObservationCapture(catalog, observed_at=datetime(2019, 1, 1)).capture('source', 'one')
    assert observation['retrieval_age_days'] is None
    assert 'SOURCE_RETRIEVAL_IN_FUTURE' in observation['limitations']


def test_utc_conversion_and_legacy_report_columns_are_explicit(catalog):
    capture = SourceObservationCapture(catalog, observed_at=datetime(2020, 1, 3, 3, tzinfo=timezone(timedelta(hours=3))))
    assert capture.capture('source', 'one')['retrieval_age_days'] == 1
    columns = observation_columns({'source_year': '2020'})
    assert columns['source_observation_state'] == 'NOT_RECORDED'
    assert columns['source_retrieved_at'] is None and columns['source_currentness'] == 'NOT_ASSESSED'
