"""GFID can identify a source group, not an individual flood polygon."""
import json

import geopandas as gpd
from shapely.geometry import box

from nyc_lens.ingest import fema
from test_multisnapshot_publication import fema_page


def test_missing_global_id_does_not_collapse_distinct_flood_areas_with_shared_gfid():
    features = json.loads(fema_page(0, 1))["features"][:3]
    for index, feature in enumerate(features):
        feature["properties"].update(GlobalID=None, GFID="synthetic-shared-source-group",
                                     DFIRM_ID="synthetic-community", FLD_AR_ID=f"synthetic-area-{index}")
    frame = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    result, invalid, removed, outside = fema._page_boundaries(frame,
        csa_union=box(-74, 40, -73.8, 40.2), snapshot_id="synthetic-response",
        retrieved_at="2026-10-31T00:00:00Z", seen=set())
    assert len(result) == 3
    assert result["boundary_id"].nunique() == result["geography_id"].nunique() == 3
    assert not result["geography_id"].str.contains("synthetic-shared-source-group").any()
    assert (invalid, removed, outside) == (0, 0, 0)
