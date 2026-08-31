import pandas as pd

from nyc_lens.ingest.civic import _facility_category, _prepare_str_registrations


def test_facility_category_uses_published_hierarchy() -> None:
    assert _facility_category(pd.Series({"facdomain": "EDUCATION", "factype": "HIGH SCHOOL"})) == "school"
    assert _facility_category(pd.Series({"facgroup": "HEALTH CARE", "factype": "HOSPITAL"})) == "health_facility"
    assert _facility_category(pd.Series({"facgroup": "PARKS AND PLAZAS", "factype": "PLAYGROUND"})) == "park"
    assert _facility_category(pd.Series({"facdomain": "LIBRARIES AND CULTURAL PROGRAMS", "factype": "MUSEUM"})) == "cultural_facility"
    assert _facility_category(pd.Series({"facdomain": "LIBRARIES AND CULTURAL PROGRAMS", "factype": "PUBLIC LIBRARY"})) == "library"


def test_str_continuation_rows_are_aggregated_without_unit_or_listing_details() -> None:
    frame = pd.DataFrame(
        [
            ["R1", "Registered", "2027-01-01", "1 MAIN ST", "2A", "QUEENS", "11101", "4000001", "A", "L1"],
            [None, None, None, None, None, None, None, None, "B", "L2"],
            ["R2", "Expired", "2025-01-01", "2 MAIN ST", "1", "BRONX", "10451", "2000001", None, None],
        ],
        columns=[f"column_{index}" for index in range(10)],
    )

    prepared = _prepare_str_registrations(frame)

    assert prepared.columns.tolist() == [
        "registration_number",
        "status",
        "expiration_date",
        "street_address",
        "borough",
        "zipcode",
        "bin",
        "listing_count",
        "booking_service_count",
    ]
    first = prepared.loc[prepared["registration_number"] == "R1"].iloc[0]
    assert first["listing_count"] == 2
    assert first["booking_service_count"] == 2
    assert "unit_number" not in prepared.columns
    assert "listing_identifier" not in prepared.columns
