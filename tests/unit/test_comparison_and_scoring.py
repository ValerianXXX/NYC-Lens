from __future__ import annotations

import duckdb
import pytest

from nyc_lens.comparison import _metric_comparisons
from nyc_lens.scoring import ScoringNotCalibratedError, score_site, scoring_status


def test_batch_rank_is_raw_high_to_low_not_a_preference_score() -> None:
    results = [
        {
            "site_id": "a",
            "standardized_address": "A",
            "catchments": {"500m": {"metrics": {"resident_population": {"value": 100.0, "unit": "people"}}}},
        },
        {
            "site_id": "b",
            "standardized_address": "B",
            "catchments": {"500m": {"metrics": {"resident_population": {"value": 200.0, "unit": "people"}}}},
        },
    ]
    compared = _metric_comparisons(results, "500m")["resident_population"]
    assert compared["rank_semantics"] == "raw_numeric_high_to_low_not_better_or_worse"
    assert [item["high_to_low_rank"] for item in compared["values"]] == [2, 1]


def test_scoring_is_blocked_without_real_calibration() -> None:
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE scoring_model(
            model_id VARCHAR, business_type VARCHAR, status VARCHAR,
            training_case_count BIGINT, holdout_case_count BIGINT,
            feature_ids_json JSON, validation_metrics_json JSON,
            artifact_path VARCHAR, artifact_sha256 VARCHAR,
            created_at TIMESTAMP, activated_at TIMESTAMP,
            method_version VARCHAR, notes_json JSON
        )
        """
    )
    conn.execute("CREATE TABLE site_query(site_id VARCHAR)")
    status = scoring_status(conn)
    assert status["status"] == "BLOCKED_CALIBRATION"
    with pytest.raises(ScoringNotCalibratedError, match="will not fabricate"):
        score_site(conn, "site_any")
