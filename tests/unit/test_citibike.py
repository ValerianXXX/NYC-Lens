from nyc_lens.ingest.citibike import _extract_monthly_report_metrics, _feed_urls, _report_links


def test_gbfs_feed_catalog_requires_station_feeds() -> None:
    root = {
        "data": {
            "en": {
                "feeds": [
                    {"name": "system_information", "url": "https://example/system.json"},
                    {"name": "station_information", "url": "https://example/info.json"},
                    {"name": "station_status", "url": "https://example/status.json"},
                ]
            }
        }
    }
    assert _feed_urls(root)["station_status"] == "https://example/status.json"


def test_latest_monthly_report_link_is_chronological() -> None:
    html = """
    https://example/December-2025-Citi-Bike-Monthly-Report.pdf
    https://example/June-2026-Citi-Bike-Monthly-Report.pdf
    https://example/March-2026-Citi-Bike-Monthly-Report.pdf
    """
    links = _report_links(html)
    assert links[-1][0].isoformat() == "2026-06-01"
    assert links[-1][1].endswith("June-2026-Citi-Bike-Monthly-Report.pdf")


def test_monthly_report_extractor_requires_system_aggregates() -> None:
    text = """
    Total annual membership stands at 138,055. There were 2,400 active stations at the end of the month.
    The average bike fleet last month was 37,453. There were 5,494,600 trips in June, with an average
    183,153 trips per day. The combined distance traveled for all trips was 9,929,073 miles. Annual members
    completed the majority of trips, recording 4,343,957 trips, compared to 1,150,643 trips by casual members.
    Station uptime for the month was 99.79%. Citi Bike staff rebalanced a total of 101,757 bicycles.
    """
    metrics = _extract_monthly_report_metrics(" ".join(text.split()))
    assert metrics["monthly_trips"] == (5_494_600.0, "trips")
    assert metrics["active_stations_end_of_month"] == (2_400.0, "stations")
    assert metrics["station_uptime"] == (99.79, "percent")
