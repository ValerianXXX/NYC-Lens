"""Bounded analysis inputs shared by interactive transports and the worker."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

from .assistant import ConfirmedComparison, validate_confirmed_comparison

Catchment = Literal["250m", "500m", "1km", "5min_walk", "10min_walk", "15min_walk"]
Address = Annotated[str, Field(min_length=5, max_length=200)]


class SiteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    address: Address
    business_type: str = Field(default="full_service_restaurant", min_length=1, max_length=100)
    catchments: list[Catchment] = Field(default_factory=lambda: ["250m", "500m", "1km", "5min_walk", "10min_walk", "15min_walk"], min_length=1, max_length=6)


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    addresses: list[Address] = Field(min_length=2, max_length=20)
    business_type: str = Field(default="full_service_restaurant", min_length=1, max_length=100)
    catchments: list[Catchment] = Field(default_factory=lambda: ["500m", "1km"], min_length=1, max_length=6)
    comparison_catchment: Catchment = "500m"


def normalized_request(operation: str, request: dict) -> tuple[dict, int]:
    if operation == "site":
        parsed = SiteRequest.model_validate(request)
        return parsed.model_dump(), 1
    if operation == "comparison":
        parsed = ComparisonRequest.model_validate(request)
        if len({value.strip().casefold() for value in parsed.addresses}) != len(parsed.addresses):
            raise ValueError("Comparison addresses must be distinct")
        if parsed.comparison_catchment not in parsed.catchments:
            raise ValueError("The comparison catchment must be requested")
        return parsed.model_dump(), len(parsed.addresses)
    if operation == "assistant_comparison":
        parsed = ConfirmedComparison.model_validate(request)
        validate_confirmed_comparison(parsed)
        return parsed.model_dump(), 2
    raise ValueError("Unsupported analysis operation")
