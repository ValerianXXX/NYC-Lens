from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from ..config import load_yaml
from ..paths import config_dir


class RealEstateAdapterValidationError(ValueError):
    """Raised before any commercial real-estate file can be activated."""


def adapter_config() -> dict[str, Any]:
    return load_yaml(config_dir() / "commercial_real_estate_adapters.yml")


def inspect_commercial_real_estate_adapters() -> dict[str, Any]:
    config = adapter_config()
    return {
        "adapter_type": config["adapter_type"],
        "activation_policy": config["activation_policy"],
        "providers": config["providers"],
        "required_license_flags": config["required_license_flags"],
        "required_gate_metadata": config["required_gate_metadata"],
        "supported_dataset_types": config["privacy"]["supported_dataset_types"],
        "network_contact_attempted": False,
        "account_created": False,
        "commercial_data_downloaded": False,
        "commercial_data_ingested": False,
    }


def _load_gate(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RealEstateAdapterValidationError(f"License gate file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise RealEstateAdapterValidationError("License gate must be a YAML or JSON object")
    return value


def validate_license_gate(gate: dict[str, Any]) -> None:
    config = adapter_config()
    missing = [field for field in config["required_gate_metadata"] if not gate.get(field)]
    false_flags = [
        field for field in config["required_license_flags"] if gate.get(field) is not True
    ]
    dataset_type = gate.get("dataset_type")
    if dataset_type not in config["privacy"]["supported_dataset_types"]:
        missing.append("supported dataset_type")
    if missing or false_flags:
        parts = []
        if missing:
            parts.append("missing or invalid metadata: " + ", ".join(missing))
        if false_flags:
            parts.append("rights not affirmatively approved: " + ", ".join(false_flags))
        raise RealEstateAdapterValidationError(
            "Commercial real-estate activation blocked; " + "; ".join(parts)
        )


def validate_local_file(frame: pd.DataFrame, gate: dict[str, Any]) -> dict[str, Any]:
    validate_license_gate(gate)
    config = adapter_config()
    privacy = config["privacy"]
    normalized = {str(column).strip().lower(): str(column) for column in frame.columns}
    prohibited = [
        original
        for normalized_name, original in normalized.items()
        if any(fragment in normalized_name for fragment in privacy["prohibited_field_fragments"])
    ]
    if prohibited:
        raise RealEstateAdapterValidationError(
            "Prohibited personal, owner, tenant, broker or contact fields detected: "
            + ", ".join(sorted(prohibited))
        )
    dataset_type = gate["dataset_type"]
    spec = privacy["supported_dataset_types"][dataset_type]
    missing = [field for field in spec["required_fields"] if field not in normalized]
    if missing:
        raise RealEstateAdapterValidationError("Missing required fields: " + ", ".join(missing))
    data = frame.rename(columns={original: key for key, original in normalized.items()}).copy()
    if data.empty:
        raise RealEstateAdapterValidationError("Commercial real-estate file is empty")
    if data["provider_record_id"].astype(str).duplicated().any():
        raise RealEstateAdapterValidationError("provider_record_id must be unique")
    if dataset_type == "property_market_aggregate":
        if not data["geography_type"].isin(spec["allowed_geography_types"]).all():
            raise RealEstateAdapterValidationError("Unsupported aggregate geography_type")
        start = pd.to_datetime(data["period_start"], errors="coerce")
        end = pd.to_datetime(data["period_end"], errors="coerce")
        values = pd.to_numeric(data["value"], errors="coerce")
        if start.isna().any() or end.isna().any() or (end < start).any():
            raise RealEstateAdapterValidationError("Aggregate periods must be valid and ordered")
        if values.isna().any() and not data.loc[values.isna(), "suppression_status"].astype(str).str.upper().eq("SUPPRESSED").all():
            raise RealEstateAdapterValidationError("Null aggregate values must be explicitly SUPPRESSED")
        span = {"period_start": start.min().date(), "period_end": end.max().date()}
    else:
        longitude = pd.to_numeric(data["longitude"], errors="coerce")
        latitude = pd.to_numeric(data["latitude"], errors="coerce")
        if longitude.isna().any() or latitude.isna().any():
            raise RealEstateAdapterValidationError("Development projects require valid coordinates")
        if not (longitude.between(-75.6, -71.5) & latitude.between(39.5, 42.5)).all():
            raise RealEstateAdapterValidationError("Development-project coordinates fall outside the Greater NYC screening envelope")
        span = {}
    return {
        "status": "VALIDATED_NOT_INGESTED",
        "dataset_type": dataset_type,
        "provider": gate["provider"],
        "rows": len(data),
        "contract_or_license_reference": gate["contract_or_license_reference"],
        "effective_date": str(gate["effective_date"]),
        **span,
        "warning": "Validation does not ingest, activate, purchase, scrape, contact, download or prove downstream rights; activation remains a separate user-authorized step.",
    }


def read_and_validate_local_file(data_path: Path, gate_path: Path) -> dict[str, Any]:
    gate = _load_gate(gate_path)
    if data_path.suffix.lower() == ".csv":
        frame = pd.read_csv(data_path)
    elif data_path.suffix.lower() in {".parquet", ".pq"}:
        frame = pd.read_parquet(data_path)
    else:
        raise RealEstateAdapterValidationError("Only local CSV or Parquet files are supported")
    return validate_local_file(frame, gate)
