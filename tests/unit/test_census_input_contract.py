"""Frozen synthetic arithmetic and source-identity contracts, not location data."""
import duckdb
import pandas as pd
import pytest
from shapely.geometry import box
from shapely.strtree import STRtree

from nyc_lens.metrics import engine

G0, G1, G2 = '340130001001', '340130002001', '340130001002'
T0, T1 = G0[:11], G1[:11]


@pytest.fixture
def inputs():
    conn = duckdb.connect(':memory:')
    conn.execute('''CREATE TABLE census_estimate(census_estimate_id VARCHAR, geography_id VARCHAR,
        geography_type VARCHAR, variable VARCHAR, estimate DOUBLE, margin_of_error DOUBLE,
        confidence DOUBLE, source_id VARCHAR, snapshot_id VARCHAR)''')
    conn.execute('''CREATE TABLE geo_boundary(boundary_id VARCHAR, geography_id VARCHAR,
        geography_type VARCHAR, geom_wkt VARCHAR, area_m2 DOUBLE, source_id VARCHAR, snapshot_id VARCHAR)''')
    conn.execute('''CREATE TABLE health_indicator(health_indicator_id VARCHAR, geography_id VARCHAR,
        indicator_id VARCHAR, value DOUBLE, margin_of_error DOUBLE, confidence DOUBLE,
        source_id VARCHAR, snapshot_id VARCHAR)''')
    for identifier, geoid in [('b0', G0), ('b1', G1), ('b2', G2)]:
        conn.execute("INSERT INTO geo_boundary VALUES (?, ?, 'block_group', ?, 100, 'tiger_bg_2024', 'synthetic-bg')",
                     [identifier, geoid, box(-74.171, 40.734, -74.169, 40.736).wkt])
    for identifier, geoid in [('t0', T0), ('t1', T1)]:
        conn.execute("INSERT INTO geo_boundary VALUES (?, ?, 'tract_2024', ?, 100, 'tiger_tract_2024', 'synthetic-tract')",
                     [identifier, geoid, box(-74.171, 40.734, -74.169, 40.736).wkt])
    weights = pd.DataFrame([
        {'geography_id': G0, 'fraction': .5, 'intersection_area_m2': 50., 'boundary_area_m2': 100., 'boundary_id': 'b0'},
        {'geography_id': G1, 'fraction': .25, 'intersection_area_m2': 25., 'boundary_area_m2': 100., 'boundary_id': 'b1'},
    ])
    yield conn, weights
    conn.close()


def estimate(conn, identifier, geoid, value, variable='B01003_001', kind='block_group', source='acs_2024_5yr'):
    conn.execute('INSERT INTO census_estimate VALUES (?, ?, ?, ?, ?, 4, .9, ?, ?)',
                 [identifier, geoid, kind, variable, value, source, 'synthetic-' + source])


def population(conn):
    estimate(conn, 'p0', G0, 100)
    estimate(conn, 'p1', G1, 200)


def test_context_and_intersection_keep_exact_boundary_identity(inputs):
    conn, _ = inputs
    context = engine._acs_context(conn)
    assert context is not None and 'boundary_id' in context[0].columns
    target = box(-74.1705, 40.7345, -74.1695, 40.7355)
    weights = engine._acs_weights(target, context)
    assert set(weights['boundary_id']) == {'b0', 'b1', 'b2'}
    assert (weights['fraction'].between(0, 1)).all()
    assert engine._acs_weights(box(-70, 42, -69.9, 42.1), context).empty


def test_area_count_keeps_arithmetic_and_declares_intersecting_geometry(inputs):
    conn, weights = inputs
    population(conn)
    value, margin, evidence, confidence = engine._acs_aggregate(conn, weights, 'B01003_001')
    assert value == 100
    assert margin == pytest.approx(5 ** .5)
    assert confidence == pytest.approx(.9)
    assert set(evidence) == {'p0', 'p1', 'b0', 'b1'}


def test_other_source_with_the_same_geography_and_variable_cannot_change_acs_count(inputs):
    conn, weights = inputs
    population(conn)
    estimate(conn, 'other-source', G0, 900, source='synthetic_other_survey')
    value, _, evidence, _ = engine._acs_aggregate(conn, weights, 'B01003_001')
    assert value == 100
    assert 'other-source' not in evidence


def test_weighted_summary_declares_population_and_boundary_inputs(inputs):
    conn, weights = inputs
    population(conn)
    estimate(conn, 'm0', G0, 20000, 'B19013_001')
    estimate(conn, 'm1', G1, 80000, 'B19013_001')
    value, _, evidence, _ = engine._acs_aggregate(conn, weights, 'B19013_001', median=True)
    assert value == 50000
    assert set(evidence) == {'m0', 'm1', 'p0', 'p1', 'b0', 'b1'}
    estimate(conn, 'other-population', G0, 900, source='synthetic_other_survey')
    second, _, evidence, _ = engine._acs_aggregate(conn, weights, 'B19013_001', median=True)
    assert second == value
    assert 'other-population' not in evidence


def test_tract_fallback_declares_all_denominator_boundaries_without_other_sources(inputs):
    conn, weights = inputs
    estimate(conn, 'ct0', T0, 400, 'B15003_001', 'tract')
    estimate(conn, 'ct1', T1, 300, 'B15003_001', 'tract')
    conn.execute("INSERT INTO geo_boundary VALUES ('other-boundary', ?, 'block_group', ?, 900, 'synthetic_other_boundary', 'synthetic-other')",
                 [G0, box(-74.171, 40.734, -74.169, 40.736).wkt])
    value, _, evidence, _ = engine._acs_aggregate(conn, weights, 'B15003_001')
    # First tract denominator includes b0+b2; b2 need not intersect the catchment.
    assert value == 175
    assert set(evidence) == {'ct0', 'ct1', 'b0', 'b1', 'b2'}


def test_tract_summary_records_population_outside_the_intersection(inputs):
    conn, weights = inputs
    population(conn)
    estimate(conn, 'p2', G2, 900)
    estimate(conn, 'mt0', T0, 20000, 'B19013_001', 'tract')
    estimate(conn, 'mt1', T1, 80000, 'B19013_001', 'tract')
    value, _, evidence, _ = engine._acs_aggregate(conn, weights, 'B19013_001', median=True)
    assert value == 30000
    assert set(evidence) == {'mt0', 'mt1', 'p0', 'p1', 'p2', 'b0', 'b1', 'b2'}


def test_missing_selected_geography_withholds_full_count_but_retains_known_inputs(inputs):
    conn, weights = inputs
    estimate(conn, 'p0', G0, 100)
    value, margin, evidence, confidence = engine._acs_aggregate(conn, weights, 'B01003_001')
    assert value is None and margin is None and confidence is None
    assert set(evidence) == {'p0', 'b0', 'b1'}


def test_duplicate_active_geography_variable_is_not_double_counted(inputs):
    conn, weights = inputs
    population(conn)
    estimate(conn, 'p0-duplicate', G0, 100)
    value, _, evidence, _ = engine._acs_aggregate(conn, weights, 'B01003_001')
    assert value is None
    assert {'p0', 'p0-duplicate', 'b0', 'b1'} <= set(evidence)


def test_population_weighting_does_not_silently_drop_missing_or_zero_weights(inputs):
    conn, weights = inputs
    estimate(conn, 'm0', G0, 20000, 'B19013_001')
    estimate(conn, 'm1', G1, 80000, 'B19013_001')
    estimate(conn, 'p0', G0, 100)
    assert engine._acs_aggregate(conn, weights, 'B19013_001', median=True)[0] is None
    estimate(conn, 'p1', G1, 0)
    # An explicitly observed zero is different from a missing population.
    assert engine._acs_aggregate(conn, weights, 'B19013_001', median=True)[0] == 20000
    conn.execute("UPDATE census_estimate SET estimate=0 WHERE variable='B01003_001'")
    assert engine._acs_aggregate(conn, weights, 'B19013_001', median=True)[0] is None


def test_age_total_requires_every_requested_component(inputs):
    conn, weights = inputs
    estimate(conn, 'age0', G0, 20, 'B01001_003')
    estimate(conn, 'age1', G1, 20, 'B01001_003')
    value, _, evidence, _ = engine._sum_age_variables(conn, weights, [3, 4])
    assert value is None
    assert {'age0', 'age1', 'b0', 'b1'} <= set(evidence)
    estimate(conn, 'age2', G0, 40, 'B01001_004')
    estimate(conn, 'age3', G1, 40, 'B01001_004')
    assert engine._sum_age_variables(conn, weights, [3, 4])[0] == 45


def test_decennial_count_includes_boundary_evidence_and_missing_values_do_not_become_zero(inputs):
    conn, weights = inputs
    estimate(conn, 'dc0', G0, 100, 'P0010001', 'census_block', 'census_decennial_2020_pl')
    estimate(conn, 'dc1', G1, 200, 'P0010001', 'census_block', 'census_decennial_2020_pl')
    value, evidence = engine._decennial_aggregate(conn, weights, 'P0010001')
    assert value == 100
    assert set(evidence) == {'dc0', 'dc1', 'b0', 'b1'}
    conn.execute("UPDATE census_estimate SET estimate=NULL WHERE census_estimate_id='dc1'")
    assert engine._decennial_aggregate(conn, weights, 'P0010001')[0] is None


def test_health_summary_declares_both_geometry_layers_and_population(inputs):
    conn, weights = inputs
    for identifier, geoid, value in [('h0', T0, 20), ('h1', T1, 40)]:
        conn.execute("INSERT INTO health_indicator VALUES (?, ?, 'SYNTHETIC', ?, 2, .7, 'cdc_places_2025_tract', 'synthetic-health')", [identifier, geoid, value])
    estimate(conn, 'pt0', T0, 100, kind='tract')
    estimate(conn, 'pt1', T1, 200, kind='tract')
    value, _, evidence, _ = engine._tract_health_aggregate(conn, weights, 'SYNTHETIC')
    assert value == 30
    assert set(evidence) == {'h0', 'h1', 'pt0', 'pt1', 'b0', 'b1', 't0', 't1'}
    conn.execute("DELETE FROM census_estimate WHERE census_estimate_id='pt1'")
    assert engine._tract_health_aggregate(conn, weights, 'SYNTHETIC')[0] is None
