from __future__ import annotations

import pandas as pd

from nyc_lens.qa.address_validation import (
    BOROUGHS,
    _coordinate_error_m,
    _deterministic_public_sample,
)


def test_public_sample_is_deterministic_and_stratified(tmp_path) -> None:
    rows = []
    for borough_index, borough in enumerate(BOROUGHS, start=1):
        for address_index in range(25):
            rows.append(
                {
                    "camis": f"{borough_index}{address_index:07d}",
                    "building": str(100 + address_index),
                    "street": f"TEST STREET {borough_index}",
                    "zipcode": f"10{borough_index:03d}",
                    "boro": borough.upper(),
                    "bbl": f"{borough_index}{address_index + 1:09d}",
                    "longitude": str(-74.0 + borough_index / 1000),
                    "latitude": str(40.7 + borough_index / 1000),
                }
            )
    source = tmp_path / "dohmh.csv"
    pd.DataFrame(rows).to_csv(source, index=False)

    first = _deterministic_public_sample(source, 100)
    second = _deterministic_public_sample(source, 100)

    assert first["camis"].tolist() == second["camis"].tolist()
    assert len(first) == 100
    assert first.groupby("boro").size().to_dict() == {borough: 20 for borough in BOROUGHS}
    assert first["input_address"].str.contains(", NY 10").all()


def test_coordinate_error_is_zero_for_identical_points() -> None:
    assert _coordinate_error_m(-73.9855, 40.7580, -73.9855, 40.7580) == 0.0


def test_coordinate_error_is_geodesic_meters() -> None:
    distance = _coordinate_error_m(-73.9855, 40.7580, -73.9855, 40.7590)
    assert 110 <= distance <= 112
