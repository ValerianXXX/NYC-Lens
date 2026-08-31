import pandas as pd

from nyc_lens.ingest.property import (
    PROHIBITED_FIELD_TOKENS,
    SOURCE_SPECS,
    _mixed_dates,
    _normalize_bbl,
    selected_api_fields,
)


def test_normalize_bbl_uses_official_borough_order() -> None:
    assert _normalize_bbl("MANHATTAN", "1015", "7502") == "1010157502"
    assert _normalize_bbl("BRONX", "123", "4") == "2001230004"
    assert _normalize_bbl("BROOKLYN", "3150", "2") == "3031500002"
    assert _normalize_bbl("QUEENS", "10551", "14") == "4105510014"
    assert _normalize_bbl("STATEN ISLAND", "62", "56") == "5000620056"
    assert _normalize_bbl("1", "00847", "00038") == "1008470038"


def test_normalize_bbl_prefers_valid_published_value() -> None:
    assert _normalize_bbl("QUEENS", "1", "1", "1010157502.0") == "1010157502"
    assert _normalize_bbl("UNKNOWN", "1", "1", "not-a-bbl") is None
    assert _normalize_bbl("MANHATTAN", "0", "1") is None


def test_mixed_dates_handles_compact_and_iso_dates() -> None:
    parsed = _mixed_dates(pd.Series(["20240131", "2025-02-03T00:00:00.000", "03/04/2026", None]))
    assert str(parsed.iloc[0]) == "2024-01-31"
    assert str(parsed.iloc[1]) == "2025-02-03"
    assert str(parsed.iloc[2]) == "2026-03-04"
    assert pd.isna(parsed.iloc[3])


def test_property_source_selects_never_request_prohibited_personal_fields() -> None:
    for spec in SOURCE_SPECS.values():
        assert selected_api_fields(spec).isdisjoint(PROHIBITED_FIELD_TOKENS), spec.source_id
        assert "apartment_number" not in selected_api_fields(spec)
        assert "apt_condo_no_s" not in selected_api_fields(spec)
