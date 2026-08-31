from __future__ import annotations

import pandas as pd
import pytest

from nyc_lens.adapters.commercial_mobility import (
    AdapterValidationError,
    inspect_commercial_mobility_adapters,
    validate_aggregate_frame,
)


def _gate() -> dict:
    return {
        "provider": "test_provider",
        "contract_or_license_reference": "operator-supplied-test-reference",
        "effective_date": "2026-01-01",
        "minimum_cohort_size": 50,
        "long_term_storage_allowed": True,
        "derived_metrics_allowed": True,
        "paid_client_reporting_allowed": True,
        "historical_retention_allowed": True,
        "map_combination_allowed": True,
        "privacy_review_completed": True,
        "user_authorized_activation": True,
    }


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "provider_record_id": "aggregate-1",
                "geography_type": "census_tract",
                "geography_id": "36061000100",
                "period_start": "2026-01-01T00:00:00Z",
                "period_end": "2026-02-01T00:00:00Z",
                "measure": "visits",
                "value": 250.0,
                "unit": "aggregate_visits",
                "cohort_size": 100,
                "suppression_status": "PUBLISHED",
            }
        ]
    )


def test_adapter_stays_blocked_without_provider_rights() -> None:
    status = inspect_commercial_mobility_adapters()
    assert status["network_contact_attempted"] is False
    assert status["commercial_data_downloaded"] is False
    assert all(item["status"].startswith("BLOCKED") for item in status["providers"].values())


def test_aggregate_validator_accepts_only_licensed_aggregates() -> None:
    result = validate_aggregate_frame(_frame(), _gate())
    assert result["status"] == "VALIDATED_NOT_INGESTED"
    assert result["rows"] == 1


def test_aggregate_validator_rejects_device_identifiers_and_small_unsuppressed_cells() -> None:
    device_frame = _frame().assign(device_id="abc")
    with pytest.raises(AdapterValidationError, match="Prohibited"):
        validate_aggregate_frame(device_frame, _gate())
    small = _frame().assign(cohort_size=20)
    with pytest.raises(AdapterValidationError, match="SUPPRESSED"):
        validate_aggregate_frame(small, _gate())


def test_aggregate_validator_rejects_unapproved_license_gate() -> None:
    gate = _gate()
    gate["paid_client_reporting_allowed"] = False
    with pytest.raises(AdapterValidationError, match="activation blocked"):
        validate_aggregate_frame(_frame(), gate)
