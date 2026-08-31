from __future__ import annotations

import pandas as pd
import pytest

from nyc_lens.adapters.commercial_real_estate import (
    RealEstateAdapterValidationError,
    inspect_commercial_real_estate_adapters,
    validate_license_gate,
    validate_local_file,
)


def _gate(dataset_type: str = "development_project") -> dict:
    return {
        "provider": "user supplied official file",
        "dataset_type": dataset_type,
        "contract_or_license_reference": "public-data-license-review-1",
        "effective_date": "2026-08-30",
        "long_term_storage_allowed": True,
        "derived_metrics_allowed": True,
        "paid_client_reporting_allowed": True,
        "historical_retention_allowed": True,
        "location_level_use_allowed": True,
        "privacy_review_completed": True,
        "user_authorized_activation": True,
    }


def test_real_estate_adapters_do_not_claim_vendor_access() -> None:
    status = inspect_commercial_real_estate_adapters()
    assert status["commercial_data_downloaded"] is False
    assert status["account_created"] is False
    assert status["providers"]["costar"]["status"] == "BLOCKED_LICENSE"


def test_real_estate_license_gate_requires_every_right() -> None:
    gate = _gate()
    gate["paid_client_reporting_allowed"] = False
    with pytest.raises(RealEstateAdapterValidationError, match="paid_client_reporting_allowed"):
        validate_license_gate(gate)


def test_development_project_validator_rejects_personal_contact_fields() -> None:
    frame = pd.DataFrame(
        {
            "provider_record_id": ["p1"],
            "project_type": ["mixed_use"],
            "project_status": ["approved"],
            "longitude": [-74.0],
            "latitude": [40.7],
            "source_reference": ["official-record-1"],
            "owner_email": ["not-allowed@example.invalid"],
        }
    )
    with pytest.raises(RealEstateAdapterValidationError, match="contact fields"):
        validate_local_file(frame, _gate())


def test_development_project_validator_only_validates() -> None:
    frame = pd.DataFrame(
        {
            "provider_record_id": ["p1"],
            "project_type": ["mixed_use"],
            "project_status": ["approved"],
            "longitude": [-74.0],
            "latitude": [40.7],
            "source_reference": ["official-record-1"],
        }
    )
    result = validate_local_file(frame, _gate())
    assert result["status"] == "VALIDATED_NOT_INGESTED"
