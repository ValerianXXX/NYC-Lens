"""A real engine ratio must not silently sum only available education levels."""
import duckdb
from shapely.geometry import box

from nyc_lens.db import SCHEMA_PATH
from nyc_lens.metrics.engine import _acs_context, calculate_metrics


def test_education_share_requires_all_four_published_components():
    with duckdb.connect() as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding='utf-8'))
        geometry = box(-74.171, 40.734, -74.169, 40.736)
        conn.execute("""INSERT INTO geo_boundary(boundary_id,geography_id,geography_type,geom_wkt,
            area_m2,source_id,snapshot_id,retrieved_at,method_version) VALUES
            ('synthetic-bg','340130001001','block_group',?,100,'tiger_bg_2024','synthetic-bg-v1',now(),'synthetic')""",
                     [geometry.wkt])
        for variable, value in [('B15003_001', 100), ('B15003_022', 10)]:
            conn.execute("""INSERT INTO census_estimate(census_estimate_id,geography_id,geography_type,
                variable,estimate,margin_of_error,confidence,survey_period,source_id,snapshot_id,
                retrieved_at,method_version) VALUES (?, '340130001001','block_group',?,?,2,.9,
                'SYNTHETIC','acs_2024_5yr','synthetic-census-v1',now(),'synthetic')""",
                         ['synthetic-' + variable, variable, value])
        metrics = calculate_metrics(conn, geometry, borough=None, business_type='full_service_restaurant',
                                    acs_context=_acs_context(conn))
        metric = next(m for m in metrics if m.metric_id == 'bachelors_or_higher_share')
        assert metric.value is None
        assert {'synthetic-B15003_001', 'synthetic-B15003_022', 'synthetic-bg'} <= set(metric.evidence_ids)
        assert any('component' in warning.lower() for warning in metric.warnings)
