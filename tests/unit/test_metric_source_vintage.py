"""Frozen ordinary-data-path counterexample; all data remains synthetic."""
from nyc_lens.assistant import ComparisonIntent, ConfirmedComparison, execute_comparison
from nyc_lens.demo import DEMO_ADDRESSES
from test_metric_input_lineage import saved_split_source


def test_catalogue_vintage_is_bound_to_actual_input_outside_demo_branch(saved_split_source):
    conn, _, _ = saved_split_source
    # Exercise the ordinary data code path in the disposable synthetic fixture.
    # This does not turn the fictional addresses into observed real locations.
    conn.execute('DROP TABLE synthetic_demo_guard')
    conn.execute("UPDATE dataset_snapshot SET vintage='2024-06' WHERE snapshot_id='synthetic-places-v1'")
    conn.execute("UPDATE dataset_snapshot SET vintage='2026-10' WHERE snapshot_id='synthetic-part-2'")
    result = execute_comparison(conn, ConfirmedComparison(confirmed=True,
        intent=ComparisonIntent(addresses=DEMO_ADDRESSES, business_type='full_service_restaurant', priorities=['competition'])))
    metric = result['metric_comparisons']['direct_competitor_count']
    assert [row['value'] for row in metric['values']] == [1, 3]
    assert [row['source_year'] for row in metric['values']] == ['2024-06', '2024-06 / 2026-10']
    assert metric['comparability']['status'] == 'INCOMPARABLE'
    assert all(row['high_to_low_rank'] is None for row in metric['values'])
