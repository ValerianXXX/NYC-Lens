from datetime import datetime, timezone
from pathlib import PureWindowsPath

import pandas as pd

from nyc_lens.ingest.acris import LEGALS_SELECT, MASTER_SELECT, _legals_where, _master_where
from nyc_lens.ingest.regulatory import (
    PROHIBITED_311_FIELDS,
    _build_aggregates,
    _month_windows,
    selected_311_fields,
)
from nyc_lens.provenance import _is_windows_ephemeral_port_exhaustion, _windows_path_to_wsl


def test_acris_queries_exclude_party_and_property_address_fields() -> None:
    selected = f"{MASTER_SELECT},{LEGALS_SELECT}".lower()
    prohibited_tokens = {
        "party",
        "name",
        "address",
        "street_name",
        "street_number",
        "unit",
    }
    assert not any(token in selected for token in prohibited_tokens)
    assert "recorded_datetime >= '2021-01-01" in _master_where()
    assert "document_id >= '2021010100000000'" == _legals_where()


def test_311_query_is_aggregate_only() -> None:
    assert not (selected_311_fields() & PROHIBITED_311_FIELDS)
    assert selected_311_fields() == {
        "bbl",
        "complaint_type",
        "agency",
        "created_date",
    }


def test_311_builder_keeps_counts_at_bbl_problem_level() -> None:
    frame = pd.DataFrame(
        {
            "bbl": ["1012340056", "0"],
            "complaint_type": ["Noise - Residential", "Noise - Street/Sidewalk"],
            "agency": ["DEP", "NYPD"],
            "request_count": ["12", "3"],
            "first_created_date": ["2025-01-02T00:00:00.000", "2025-02-03T00:00:00.000"],
            "last_created_date": ["2026-01-02T00:00:00.000", "2026-02-03T00:00:00.000"],
        }
    )
    records = _build_aggregates(
        frame,
        snapshot_id="snap_test",
        retrieved_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
    )
    assert records.loc[0, "geography_id"] == "1012340056"
    assert records.loc[0, "_request_count"] == 12
    assert records.loc[0, "record_type"] == "311_request_aggregate_24m"
    assert pd.isna(records.loc[1, "geography_id"])
    assert "incident_address" not in records.loc[0, "attributes_json"]


def test_311_month_windows_cover_exact_rolling_interval() -> None:
    windows = _month_windows(pd.Timestamp("2024-08-29").date(), pd.Timestamp("2026-08-29").date())
    assert windows[0] == (pd.Timestamp("2024-08-29").date(), pd.Timestamp("2024-09-01").date())
    assert windows[-1] == (pd.Timestamp("2026-08-01").date(), pd.Timestamp("2026-08-30").date())
    assert all(left[1] == right[0] for left, right in zip(windows, windows[1:]))


def test_wsl_fallback_is_narrow_and_preserves_space_in_path() -> None:
    class WindowsPathStub:
        def resolve(self) -> PureWindowsPath:
            return PureWindowsPath(r"C:\Temp\file with space.csv")

    converted = _windows_path_to_wsl(WindowsPathStub())  # type: ignore[arg-type]
    assert converted.startswith("/mnt/")
    assert converted.endswith("file with space.csv")
    assert _is_windows_ephemeral_port_exhaustion(OSError("[WinError 10048] socket exhausted"))
    assert not _is_windows_ephemeral_port_exhaustion(OSError("HTTP 404"))
