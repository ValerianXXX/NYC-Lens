from datetime import UTC, datetime

import geopandas as gpd
from shapely.geometry import Polygon

from nyc_lens.ingest.tlc import _latest_common_period, _taxi_zone_boundary_rows, _trip_links


def test_tlc_link_discovery_selects_latest_common_yellow_green_month() -> None:
    page = """
    <a href="https://example.test/yellow_tripdata_2026-04.parquet">yellow</a>
    <a href="https://example.test/green_tripdata_2026-04.parquet">green</a>
    <a href="https://example.test/yellow_tripdata_2026-05.parquet">yellow</a>
    """
    links = _trip_links(page)

    assert _latest_common_period(links) == "2026-04"


def test_taxi_zone_boundary_rows_preserve_location_id_and_bounds() -> None:
    frame = gpd.GeoDataFrame(
        {"LocationID": [7], "zone": ["Astoria"], "borough": ["Queens"]},
        geometry=[
            Polygon([(-73.93, 40.76), (-73.91, 40.76), (-73.91, 40.78), (-73.93, 40.76)])
        ],
        crs="EPSG:4326",
    )

    rows = _taxi_zone_boundary_rows(
        frame,
        snapshot_id="snap_test",
        retrieved_at=datetime(2026, 8, 29, tzinfo=UTC),
    )

    assert rows.iloc[0]["geography_id"] == "7"
    assert rows.iloc[0]["parent_id"] == "Queens"
    assert rows.iloc[0]["min_longitude"] == -73.93
    assert rows.iloc[0]["area_m2"] > 0
