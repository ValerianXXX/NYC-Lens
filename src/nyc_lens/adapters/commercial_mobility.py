from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from ..config import load_yaml
from ..paths import config_dir


class AdapterValidationError(ValueError):
    """Raised before any commercial aggregate can be activated or normalized."""


def adapter_config() -> dict[str, Any]:
    return load_yaml(config_dir() / "commercial_adapters.yml")


def inspect_commercial_mobility_adapters() -> dict[str, Any]:
    config = adapter_config()
    return {
        "adapter_type": config["adapter_type"],
        "activation_policy": config["activation_policy"],
        "providers": config["providers"],
        "required_license_flags": config["required_license_flags"],
        "required_gate_metadata": config["required_gate_metadata"],
        "privacy": config["privacy"],
        "network_contact_attempted": False,
        "account_created": False,
        "commercial_data_downloaded": False,
    }


def load_license_gate(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AdapterValidationError(f"License gate file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        gate = yaml.safe_load(handle) or {}
    if not isinstance(gate, dict):
        raise AdapterValidationError("License gate must be a YAML or JSON object")
    validate_license_gate(gate)
    return gate


def validate_license_gate(gate: dict[str, Any]) -> None:
    config = adapter_config()
    missing_metadata = [
        field for field in config["required_gate_metadata"] if not gate.get(field)
    ]
    false_flags = [
        field for field in config["required_license_flags"] if gate.get(field) is not True
    ]
    if missing_metadata or false_flags:
        parts = []
        if missing_metadata:
            parts.append(f"missing metadata: {', '.join(missing_metadata)}")
        if false_flags:
            parts.append(f"rights not affirmatively approved: {', '.join(false_flags)}")
        raise AdapterValidationError("Commercial mobility activation blocked; " + "; ".join(parts))
    minimum = gate.get("minimum_cohort_size")
    if not isinstance(minimum, int) or minimum < config["privacy"]["default_minimum_cohort_size"]:
        raise AdapterValidationError(
            "minimum_cohort_size must be an integer at or above the local privacy floor"
        )


def validate_aggregate_frame(
    frame: pd.DataFrame,
    gate: dict[str, Any],
) -> dict[str, Any]:
    validate_license_gate(gate)
    config = adapter_config()
    privacy = config["privacy"]
    normalized_columns = {str(column).strip().lower(): str(column) for column in frame.columns}
    prohibited = [
        original
        for normalized, original in normalized_columns.items()
        if any(fragment in normalized for fragment in privacy["prohibited_field_fragments"])
    ]
    if prohibited:
        raise AdapterValidationError(
            "Prohibited individual-level or precise-event fields detected: "
            + ", ".join(sorted(prohibited))
        )
    missing = [
        field for field in privacy["required_aggregate_fields"] if field not in normalized_columns
    ]
    if missing:
        raise AdapterValidationError("Missing aggregate fields: " + ", ".join(missing))
    data = frame.rename(columns={original: normalized for normalized, original in normalized_columns.items()}).copy()
    if data.empty:
        raise AdapterValidationError("Commercial aggregate file is empty")
    if data["provider_record_id"].astype(str).duplicated().any():
        raise AdapterValidationError("provider_record_id must be unique")
    if not data["geography_type"].isin(privacy["allowed_geography_types"]).all():
        bad = sorted(set(data.loc[~data["geography_type"].isin(privacy["allowed_geography_types"]), "geography_type"].astype(str)))
        raise AdapterValidationError("Unsupported aggregate geography_type: " + ", ".join(bad))
    start = pd.to_datetime(data["period_start"], errors="coerce", utc=True)
    end = pd.to_datetime(data["period_end"], errors="coerce", utc=True)
    if start.isna().any() or end.isna().any():
        raise AdapterValidationError("period_start and period_end must be valid timestamps")
    if ((end - start).dt.total_seconds() < privacy["minimum_period_hours"] * 3600).any():
        raise AdapterValidationError("Every aggregate period must meet the minimum duration")
    cohort = pd.to_numeric(data["cohort_size"], errors="coerce")
    values = pd.to_numeric(data["value"], errors="coerce")
    if cohort.isna().any() or (cohort < 0).any():
        raise AdapterValidationError("cohort_size must be a non-negative number")
    minimum = int(gate["minimum_cohort_size"])
    below = cohort < minimum
    suppressed = data["suppression_status"].astype(str).str.upper().eq("SUPPRESSED")
    if (below & (~suppressed | values.notna())).any():
        raise AdapterValidationError(
            "Rows below minimum_cohort_size must be SUPPRESSED and have a null value"
        )
    if ((~below) & values.isna() & ~suppressed).any():
        raise AdapterValidationError("Unsuppressed rows at the cohort threshold require a value")
    return {
        "status": "VALIDATED_NOT_INGESTED",
        "rows": len(data),
        "suppressed_rows": int(suppressed.sum()),
        "minimum_cohort_size": minimum,
        "period_start": start.min().isoformat(),
        "period_end": end.max().isoformat(),
        "geography_types": sorted(set(data["geography_type"].astype(str))),
        "measures": sorted(set(data["measure"].astype(str))),
        "provider": gate["provider"],
        "contract_or_license_reference": gate["contract_or_license_reference"],
        "effective_date": str(gate["effective_date"]),
        "warning": "Validation does not ingest, activate, purchase, contact, download or prove downstream report rights; the operator remains responsible for the supplied license gate.",
    }


def read_and_validate_aggregate_file(
    data_path: Path,
    gate_path: Path,
) -> dict[str, Any]:
    gate = load_license_gate(gate_path)
    suffix = data_path.suffix.lower()
    if suffix == ".csv":
        frame = pd.read_csv(data_path)
    elif suffix in {".parquet", ".pq"}:
        frame = pd.read_parquet(data_path)
    else:
        raise AdapterValidationError("Only local CSV or Parquet aggregate files are supported")
    return validate_aggregate_frame(frame, gate)
