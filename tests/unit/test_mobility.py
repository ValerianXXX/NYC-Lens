from nyc_lens.ingest.mobility import _latest_pedestrian_fields, _link_centroid


def test_latest_pedestrian_fields_select_complete_latest_season() -> None:
    year, month, fields = _latest_pedestrian_fields(
        ["may25_am", "may25_md", "may25_pm", "may26_am", "may26_md", "may26_pm"]
    )
    assert (year, month) == (2026, 5)
    assert fields == {"am": "may26_am", "md": "may26_md", "pm": "may26_pm"}


def test_link_centroid_parses_latitude_longitude_pairs() -> None:
    longitude, latitude = _link_centroid("40.70,-73.90 40.72,-73.88")
    assert longitude == -73.89
    assert latitude == 40.71
