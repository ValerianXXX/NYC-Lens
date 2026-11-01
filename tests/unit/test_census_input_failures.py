"""Additional invalid-input counterexamples, frozen before semantic changes."""
import pytest

from nyc_lens.metrics import engine
from test_census_input_contract import inputs, population, estimate, G0, G1, T0, T1


def test_missing_uncertainty_is_not_reported_as_zero(inputs):
    conn, weights = inputs
    population(conn)
    conn.execute("UPDATE census_estimate SET margin_of_error=NULL WHERE census_estimate_id='p1'")
    value, margin, _, _ = engine._acs_aggregate(conn, weights, 'B01003_001')
    assert value == 100
    assert margin is None


@pytest.mark.parametrize('value', [float('inf'), float('nan'), -1])
def test_unusable_count_is_not_a_complete_population(inputs, value):
    conn, weights = inputs
    population(conn)
    conn.execute("UPDATE census_estimate SET estimate=? WHERE census_estimate_id='p1'", [value])
    assert engine._acs_aggregate(conn, weights, 'B01003_001')[0] is None


def test_duplicate_intersecting_geography_does_not_double_count(inputs):
    conn, weights = inputs
    population(conn)
    import pandas as pd
    duplicate = pd.concat([weights, weights.iloc[[0]]], ignore_index=True)
    assert engine._acs_aggregate(conn, duplicate, 'B01003_001')[0] is None


def test_tract_denominator_requires_every_intersecting_boundary(inputs):
    conn, weights = inputs
    estimate(conn, 'ct0', T0, 400, 'B15003_001', 'tract')
    estimate(conn, 'ct1', T1, 300, 'B15003_001', 'tract')
    conn.execute("DELETE FROM geo_boundary WHERE boundary_id='b1'")
    assert engine._acs_aggregate(conn, weights, 'B15003_001')[0] is None
