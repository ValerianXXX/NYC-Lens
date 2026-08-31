import json
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from nyc_lens.api.app import app
from nyc_lens.config import metrics_config
from nyc_lens.paths import database_path


def test_health_endpoint_is_local_and_available() -> None:
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mode": "local"}


@pytest.mark.integration
def test_three_public_samples_have_complete_core_outputs() -> None:
    if not database_path().is_file():
        pytest.skip("Run nyc-lens refresh and nyc-lens samples first.")
    conn = duckdb.connect(str(database_path()), read_only=True)
    rows = conn.execute(
        """
        SELECT original_input, result_json
        FROM site_query
        WHERE status='SUCCEEDED'
          AND original_input IN (
            '1501 Broadway, New York, NY 10036',
            '41-17 Main Street, Flushing, NY 11355',
            '1 MetroTech Center, Brooklyn, NY 11201'
          )
        ORDER BY created_at DESC
        """
    ).fetchall()
    conn.close()
    complete_by_address = {}
    expected_catchments = {"250m", "500m", "1km", "5min_walk", "10min_walk", "15min_walk"}
    for address, payload in rows:
        candidate = json.loads(payload) if isinstance(payload, str) else payload
        if expected_catchments <= set(candidate["catchments"]):
            complete_by_address.setdefault(address, candidate)

    assert len(complete_by_address) == 3
    configured_metrics = set(metrics_config()["metrics"])
    for result in complete_by_address.values():
        assert result["parcel"]["bbl"]
        assert expected_catchments <= set(result["catchments"])
        metrics = result["catchments"]["10min_walk"]["metrics"]
        assert set(metrics) <= configured_metrics
        for metric_id in (
            "resident_population",
            "resident_population_density_per_sq_mile",
            "median_household_income",
            "median_age",
            "family_household_share",
            "management_business_science_arts_occupation_share",
            "hispanic_or_latino_share",
            "daytime_jobs",
            "direct_competitor_count",
            "subway_complex_count",
            "subway_entrance_exit_count",
            "subway_entries_2025",
            "bus_stop_count",
            "citibike_station_count",
            "tlc_pickup_trips_average_daily_area_weighted",
            "hurricane_evacuation_overlap_pct",
            "future_2050_floodplain_overlap_pct",
            "visitor_proxy_index",
            "pm25_indicator",
        ):
            assert metrics[metric_id]["value"] is not None
            assert metrics[metric_id]["source_year"]
            assert metrics[metric_id]["confidence"] is not None
        for metric_id in (
            "resident_population",
            "resident_population_density_per_sq_mile",
            "median_household_income",
            "management_business_science_arts_occupation_share",
        ):
            assert metrics[metric_id]["margin_of_error"] is not None


@pytest.mark.integration
def test_evidence_ids_resolve_to_standardized_records() -> None:
    if not database_path().is_file():
        pytest.skip("Run nyc-lens refresh and nyc-lens samples first.")
    conn = duckdb.connect(str(database_path()), read_only=True)
    missing = conn.execute(
        """
        WITH ranked_sites AS (
            SELECT site_id,
                   row_number() OVER (
                       PARTITION BY original_input ORDER BY created_at DESC
                   ) AS recency_rank
            FROM site_query
            WHERE status='SUCCEEDED'
              AND original_input IN (
                '1501 Broadway, New York, NY 10036',
                '41-17 Main Street, Flushing, NY 11355',
                '1 MetroTech Center, Brooklyn, NY 11201'
              )
        ), current_metric_evidence AS (
            SELECT site_metric.evidence_record_ids_json
            FROM ranked_sites sites
            JOIN catchment USING (site_id)
            JOIN site_metric USING (catchment_id)
            WHERE sites.recency_rank=1
        ), sampled_evidence AS (
            SELECT json_extract_string(evidence_record_ids_json, '$[0]') AS entity_id
            FROM current_metric_evidence
            UNION
            SELECT json_extract_string(evidence_record_ids_json, '$[#-1]') AS entity_id
            FROM current_metric_evidence
        )
        SELECT count(*)
        FROM sampled_evidence evidence
        LEFT JOIN standardized_record standardized USING (entity_id)
        WHERE evidence.entity_id IS NOT NULL
          AND standardized.entity_id IS NULL
        """
    ).fetchone()[0]
    conn.close()
    assert missing == 0


@pytest.mark.integration
def test_api_sections_and_evidence_endpoint_expose_current_domains_and_lineage() -> None:
    if not database_path().is_file():
        pytest.skip("Run nyc-lens refresh and nyc-lens samples first.")
    conn = duckdb.connect(str(database_path()), read_only=True)
    row = conn.execute(
        """
        SELECT site_id FROM site_query
            WHERE status='SUCCEEDED'
              AND original_input='1501 Broadway, New York, NY 10036'
              AND json_exists(result_json, '$.catchments.10min_walk')
            ORDER BY created_at DESC LIMIT 1
        """
    ).fetchone()
    conn.close()
    assert row is not None
    site_id = row[0]
    client = TestClient(app)

    demographics = client.get(f"/api/site/{site_id}/demographics")
    mobility = client.get(f"/api/site/{site_id}/mobility")
    assert demographics.status_code == 200
    assert mobility.status_code == 200
    assert "family_household_share" in demographics.json()["10min_walk"]
    assert "management_business_science_arts_occupation_share" in demographics.json()["10min_walk"]
    assert "citibike_station_count" in mobility.json()["10min_walk"]
    assert "tlc_pickup_trips_average_daily_area_weighted" in mobility.json()["10min_walk"]

    evidence = client.get(
        f"/api/site/{site_id}/evidence",
        params={
            "catchment": "10min_walk",
            "metric_id": "resident_population",
            "limit": 2,
        },
    )
    assert evidence.status_code == 200
    payload = evidence.json()
    assert payload["total_evidence_ids"] > 0
    assert payload["evidence"]
    assert payload["evidence"][0]["source_url"].startswith("https://")
    assert payload["evidence"][0]["raw_sha256"]
