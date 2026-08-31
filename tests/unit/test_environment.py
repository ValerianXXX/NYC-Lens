from datetime import UTC, datetime

import geopandas as gpd
from shapely.geometry import Polygon

from nyc_lens.ingest.environment import _feature_identity, _hazard_boundary_rows


def test_hurricane_feature_identity_preserves_numbered_and_x_zones() -> None:
    frame = gpd.GeoDataFrame(
        {"hurricane_": ["2", "X"]},
        geometry=[
            Polygon([(-74.0, 40.7), (-73.99, 40.7), (-73.99, 40.71), (-74.0, 40.7)]),
            Polygon([(-74.1, 40.6), (-74.09, 40.6), (-74.09, 40.61), (-74.1, 40.6)]),
        ],
        crs="EPSG:4326",
    )

    assert _feature_identity("nyc_hurricane_evacuation_zones", frame.iloc[0])[0] == "2"
    assert _feature_identity("nyc_hurricane_evacuation_zones", frame.iloc[1])[0] == "X"


def test_hazard_boundary_rows_include_query_bounds_and_projected_area() -> None:
    frame = gpd.GeoDataFrame(
        {"hurricane_": ["1"]},
        geometry=[
            Polygon(
                [
                    (-74.01, 40.70),
                    (-74.00, 40.70),
                    (-74.00, 40.71),
                    (-74.01, 40.70),
                ]
            )
        ],
        crs="EPSG:4326",
    )

    rows = _hazard_boundary_rows(
        frame,
        source_id="nyc_hurricane_evacuation_zones",
        snapshot_id="snap_test",
        retrieved_at=datetime(2026, 8, 29, tzinfo=UTC),
    )

    assert len(rows) == 1
    assert rows.iloc[0]["min_longitude"] == -74.01
    assert rows.iloc[0]["max_latitude"] == 40.71
    assert rows.iloc[0]["area_m2"] > 0


def test_hazard_boundary_rows_remove_exact_duplicate_source_features() -> None:
    polygon = Polygon(
        [(-74.01, 40.70), (-74.00, 40.70), (-74.00, 40.71), (-74.01, 40.70)]
    )
    frame = gpd.GeoDataFrame(
        {
            "gridcode": [1300, 1300],
            "join_count": [1, 1],
            "fld_zone": ["AE", "AE"],
            "static_bfe": [13, 13],
            "abfe_0_2pc": [None, None],
            "shape_leng": [1.0, 1.0],
            "shape_area": [2.0, 2.0],
        },
        geometry=[polygon, polygon],
        crs="EPSG:4326",
    )

    rows = _hazard_boundary_rows(
        frame,
        source_id="nyc_future_floodplain_2050s",
        snapshot_id="snap_test",
        retrieved_at=datetime(2026, 8, 29, tzinfo=UTC),
    )

    assert len(rows) == 1
