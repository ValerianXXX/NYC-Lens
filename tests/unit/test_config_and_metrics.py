import pandas as pd
import duckdb

from nyc_lens.analyze import DEFAULT_CATCHMENTS
from nyc_lens.config import sources_config, taxonomy_config
from nyc_lens.db import _content_hash
from nyc_lens.ingest.common import as_number, stable_id
from nyc_lens.ingest.census import _confidence
from nyc_lens.metrics.engine import (
    MetricValue,
    attach_benchmark_percentiles,
    derive_market_signals,
)
from nyc_lens.provenance import referenced_snapshot_set
from nyc_lens.maintenance import _clamp_percentiles


def test_required_default_catchments_are_available() -> None:
    assert DEFAULT_CATCHMENTS == [
        "250m",
        "500m",
        "1km",
        "5min_walk",
        "10min_walk",
        "15min_walk",
    ]


def test_source_registry_config_has_audit_fields() -> None:
    required = {
        "name",
        "steward",
        "url",
        "dataset_id",
        "update_frequency",
        "geographic_precision",
        "temporal_precision",
        "license",
        "estimated_size",
        "incremental_method",
        "known_limitations",
    }
    sources = sources_config()["sources"]
    assert len(sources) >= 10
    assert all(required <= set(source) for source in sources.values())
    assert all(source["license"] for source in sources.values())


def test_competition_taxonomy_separates_relations() -> None:
    restaurant = taxonomy_config()["business_types"]["full_service_restaurant"]
    assert restaurant["direct"]["overture_basic_categories"] == ["restaurant"]
    assert "cafe" in restaurant["substitutes"]["overture_basic_categories"]
    assert "hotel" in restaurant["complements"]["overture_basic_categories"]


def test_census_sentinels_and_confidence() -> None:
    values = as_number(pd.Series(["10", "-555555555", "bad"]))
    assert values.iloc[0] == 10
    assert pd.isna(values.iloc[1]) and pd.isna(values.iloc[2])
    confidence = _confidence(pd.Series([100, 100, 100]), pd.Series([10, 60, 120]))
    assert confidence.tolist() == [0.9, 0.6, 0.4]


def test_stable_ids_are_deterministic() -> None:
    assert stable_id("poi", "abc", 1) == stable_id("poi", "abc", 1)
    assert stable_id("poi", "abc", 1) != stable_id("poi", "abc", 2)


def test_calculation_content_hash_tracks_file_bytes(tmp_path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("alpha", encoding="utf-8")
    second.write_text("beta", encoding="utf-8")
    before = _content_hash([second, first], root=tmp_path)
    assert before == _content_hash([first, second], root=tmp_path)
    second.write_text("changed", encoding="utf-8")
    assert _content_hash([first, second], root=tmp_path) != before


def test_empirical_benchmark_percentile_uses_average_rank_for_ties() -> None:
    conn = duckdb.connect()
    conn.execute(
        """
        CREATE TABLE benchmark_observation(
            benchmark_scope VARCHAR, catchment_type VARCHAR, metric_id VARCHAR, value DOUBLE
        )
        """
    )
    conn.execute(
        """
        INSERT INTO benchmark_observation VALUES
        ('nyc_citywide_h3_r10_stratified','500m','resident_population',1),
        ('nyc_citywide_h3_r10_stratified','500m','resident_population',2),
        ('nyc_citywide_h3_r10_stratified','500m','resident_population',3),
        ('nyc_citywide_h3_r10_stratified','500m','resident_population',3),
        ('nyc_citywide_h3_r10_stratified','500m','resident_population',5)
        """
    )
    metric = MetricValue("resident_population", 3.0)

    attach_benchmark_percentiles(conn, "500m", [metric])

    assert metric.percentile == 70.0
    conn.close()


def test_benchmark_snapshot_set_keeps_all_referenced_snapshots() -> None:
    conn = duckdb.connect()
    conn.execute("CREATE TABLE census_estimate(source_id VARCHAR, snapshot_id VARCHAR)")
    conn.execute("CREATE TABLE transit_stop(source_id VARCHAR, snapshot_id VARCHAR)")
    conn.execute(
        "INSERT INTO census_estimate VALUES ('acs_2024_5yr','acs_a'), ('acs_2024_5yr','acs_b')"
    )
    conn.execute(
        "INSERT INTO transit_stop VALUES ('mta_bus_gtfs_static','bus_1'), "
        "('mta_bus_gtfs_static','bus_2')"
    )

    assert referenced_snapshot_set(conn) == {
        "acs_2024_5yr": ["acs_a", "acs_b"],
        "mta_bus_gtfs_static": ["bus_1", "bus_2"],
    }
    conn.close()


def test_market_signals_keep_environmental_exposure_separate_from_market_score() -> None:
    conn = duckdb.connect()
    conn.execute(
        """
        CREATE TABLE benchmark_quantile(
            benchmark_run_id VARCHAR, benchmark_scope VARCHAR, catchment_type VARCHAR,
            snapshot_id VARCHAR, created_at TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE benchmark_observation(
            benchmark_run_id VARCHAR, benchmark_scope VARCHAR, catchment_type VARCHAR,
            sample_cell_id VARCHAR
        )
        """
    )
    conn.execute(
        "INSERT INTO benchmark_quantile VALUES "
        "('run_1','nyc_citywide_h3_r10_stratified','500m','snap_bench',CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "INSERT INTO benchmark_observation VALUES "
        "('run_1','nyc_citywide_h3_r10_stratified','500m','cell_1')"
    )
    metrics = [
        MetricValue("resident_population", 100, percentile=80),
        MetricValue("daytime_jobs", 100, percentile=70),
        MetricValue("direct_competitor_count", 10, percentile=60),
        MetricValue("substitute_count", 12, percentile=55),
        MetricValue(
            "hurricane_evacuation_overlap_pct",
            4.25,
            confidence=0.9,
            evidence_ids=["evac_zone_1"],
        ),
        MetricValue("future_2050_floodplain_overlap_pct", 0, confidence=0.85),
        MetricValue("sea_level_2100_floodplain_overlap_pct", 0, confidence=0.85),
    ]

    signals = {metric.metric_id: metric for metric in derive_market_signals(conn, "500m", metrics)}

    assert signals["market_opportunity_profile"].text_value == "需求支撑高、竞争压力中"
    assert signals["environmental_exposure_profile"].text_value == "局部重叠（最大覆盖 4.2%）"
    assert signals["environmental_exposure_profile"].source_id == "derived"
    assert signals["environmental_exposure_profile"].evidence_ids == ["evac_zone_1"]
    walking_signals = {
        metric.metric_id: metric
        for metric in derive_market_signals(conn, "10min_walk", metrics)
    }
    assert set(walking_signals) == {"environmental_exposure_profile"}
    conn.close()


def test_nested_legacy_percentiles_are_clamped_without_changing_other_values() -> None:
    payload = {
        "catchments": {
            "500m": {
                "metrics": {
                    "high": {"percentile": 100.2, "value": 12},
                    "low": {"percentile": -0.5, "value": 3},
                    "valid": {"percentile": 55.0, "value": 7},
                }
            }
        }
    }

    assert _clamp_percentiles(payload) == 2
    metrics = payload["catchments"]["500m"]["metrics"]
    assert metrics["high"]["percentile"] == 100.0
    assert metrics["low"]["percentile"] == 0.0
    assert metrics["valid"] == {"percentile": 55.0, "value": 7}
