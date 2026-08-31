from datetime import UTC, date, datetime
import zipfile

import pandas as pd

from nyc_lens.ingest.mta import _aggregate_ridership_partitions, _bounded_date_windows, _bus_feed_frame
from nyc_lens.provenance import Snapshot


def test_weekly_windows_cover_2025_without_gaps() -> None:
    windows = _bounded_date_windows(2025)
    assert len(windows) == 53
    assert windows[0] == (date(2025, 1, 1), date(2025, 1, 8))
    assert windows[-1] == (date(2025, 12, 31), date(2026, 1, 1))
    assert all(left[1] == right[0] for left, right in zip(windows, windows[1:]))


def test_partition_aggregation_sums_repeated_station_hours(tmp_path) -> None:
    station_ids = [str(index) for index in range(1, 451)]
    base = pd.DataFrame(
        {
            "station_complex_id": station_ids,
            "station_complex": [f"Station {value}" for value in station_ids],
            "borough": "Manhattan",
            "latitude": 40.75,
            "longitude": -73.98,
            "day_of_week": 3,
            "hour_of_day": 8,
            "ridership": 10,
            "transfers": 2,
        }
    )
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    base.to_csv(first, index=False)
    base.assign(ridership=20, transfers=3).to_csv(second, index=False)

    result = _aggregate_ridership_partitions([first, second])

    assert len(result) == 450
    assert result["ridership"].sum() == 450 * 30
    assert result["transfers"].sum() == 450 * 5


def test_bus_gtfs_parser_links_routes_and_filters_non_nyc_stops(tmp_path) -> None:
    archive_path = tmp_path / "bus.zip"
    files = {
        "stops.txt": (
            "stop_id,stop_name,stop_lat,stop_lon,location_type,wheelchair_boarding\n"
            "S1,Main St,40.75,-73.98,0,1\n"
            "S2,Outside,41.50,-73.98,0,0\n"
        ),
        "routes.txt": "route_id,route_short_name,route_long_name\nR1,M1,Route One\n",
        "trips.txt": "route_id,service_id,trip_id\nR1,WKD,T1\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\nT1,08:00:00,08:00:00,S1,1\n",
        "feed_info.txt": "feed_publisher_name,feed_start_date,feed_end_date\nMTA,20260801,20261031\n",
    }
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    snapshot = Snapshot(
        snapshot_id="snap_bus",
        source_id="mta_bus_gtfs_static",
        path=archive_path,
        metadata_path=tmp_path / "manifest.json",
        sha256="fixture",
        byte_size=archive_path.stat().st_size,
        retrieved_at=datetime(2026, 8, 29, tzinfo=UTC),
        vintage="fixture",
    )

    frame, feed_info, stats = _bus_feed_frame(snapshot, feed_key="manhattan", borough="Manhattan")

    assert len(frame) == 1
    assert frame.iloc[0]["source_stop_id"] == "manhattan:S1"
    assert frame.iloc[0]["routes_json"] == '["M1"]'
    assert frame.iloc[0]["valid_from"] == date(2026, 8, 1)
    assert stats["raw_stops"] == 2 and stats["nyc_bus_stops"] == 1
    assert feed_info.iloc[0]["feed_key"] == "manhattan"
