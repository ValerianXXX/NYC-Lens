"""Original fictional inputs for a complete site and an incomplete second site."""
from shapely.geometry import box
from shapely.ops import transform

from nyc_lens.spatial.catchments import metric_transform_for_geometry


def seed_census_scenario(conn):
    assert conn.execute('SELECT label FROM synthetic_demo_guard').fetchall() == [('SYNTHETIC_DEMO',)]
    assert conn.execute('SELECT count(*) FROM census_estimate').fetchone()[0] == 0
    assert conn.execute('SELECT count(*) FROM geo_boundary').fetchone()[0] == 0
    sources = ('tiger_bg_2024', 'tiger_tract_2024', 'acs_2024_5yr', 'cdc_places_2025_tract')
    for source in sources:
        run, snapshot = 'synthetic-inputs-run-' + source, 'synthetic-inputs-' + source
        conn.execute("""INSERT INTO ingestion_run(run_id,source_id,command,started_at,status)
            VALUES (?,?,'original synthetic census fixture',now(),'SUCCEEDED')""", [run, source])
        conn.execute("""INSERT INTO dataset_snapshot(snapshot_id,source_id,run_id,vintage,retrieved_at,
            source_url,license,status,warnings_json) VALUES (?,?,?,'SYNTHETIC INPUTS',now(),
            'synthetic://nyc-lens/census-inputs','Original synthetic fixture','SUCCEEDED','["SYNTHETIC_DEMO"]')""",
                     [snapshot, source, run])

    def lineage(table, identifier, source):
        conn.execute("INSERT INTO standardized_record VALUES (?,?,?,?,?,?,'synthetic-inputs-v1',now())",
                     [identifier + '-lineage', table, identifier, source, 'synthetic-inputs-' + source, identifier])

    def boundary(identifier, geoid, kind, geometry, source):
        area = transform(metric_transform_for_geometry(geometry), geometry).area
        conn.execute("""INSERT INTO geo_boundary(boundary_id,geography_id,geography_type,geom_wkt,
            area_m2,source_id,snapshot_id,retrieved_at,method_version) VALUES (?,?,?,?,?,?,?,now(),'synthetic-inputs-v1')""",
                     [identifier, geoid, kind, geometry.wkt, area, source, 'synthetic-inputs-' + source])
        lineage('geo_boundary', identifier, source)

    def estimate(identifier, geoid, value, variable, kind='block_group'):
        source = 'acs_2024_5yr'
        conn.execute("""INSERT INTO census_estimate(census_estimate_id,geography_id,geography_type,variable,
            estimate,margin_of_error,confidence,survey_period,source_id,snapshot_id,retrieved_at,method_version)
            VALUES (?,?,?,?,?,4,.9,'SYNTHETIC INPUTS',?,?,now(),'synthetic-inputs-v1')""",
                     [identifier, geoid, kind, variable, value, source, 'synthetic-inputs-' + source])
        lineage('census_estimate', identifier, source)

    for site, longitude in enumerate((-74.170, -74.150)):
        tract = f'34013000{site + 1}00'
        boundary(f'synthetic-tract-{site}', tract, 'tract_2024',
                 box(longitude-.001, 40.7345, longitude+.001, 40.7355), 'tiger_tract_2024')
        for part, offset in enumerate((-.0005, .0005)):
            geoid = tract + str(part + 1)
            identifier = f'synthetic-bg-{site}-{part}'
            boundary(identifier, geoid, 'block_group',
                     box(longitude+offset-.0002, 40.7348, longitude+offset+.0002, 40.7352), 'tiger_bg_2024')
            if (site, part) != (1, 1):
                estimate(f'synthetic-pop-{site}-{part}', geoid, (part + 1) * 100, 'B01003_001')
            estimate(f'synthetic-income-{site}-{part}', geoid, (20000, 80000)[part], 'B19013_001')
            estimate(f'synthetic-education-total-{site}-{part}', geoid, 100, 'B15003_001')
            components = [('022', 10), ('023', 20), ('024', 5), ('025', 5)] if site == 0 else [('022', 10)]
            for suffix, value in components:
                estimate(f'synthetic-education-{site}-{part}-{suffix}', geoid, value, 'B15003_' + suffix)
        estimate(f'synthetic-tract-pop-{site}', tract, 300, 'B01003_001', 'tract')
        if site == 0:
            identifier, source = 'synthetic-health-0', 'cdc_places_2025_tract'
            conn.execute("""INSERT INTO health_indicator(health_indicator_id,indicator_id,name,value,unit,
                margin_of_error,source_id,snapshot_id,retrieved_at,geography_type,geography_id,measure_type,
                confidence,method_version) VALUES (?,'CASTHMA','Fictional health percentage',20,'percent',
                2,?,?,now(),'tract',?,'synthetic_record',.7,'synthetic-inputs-v1')""",
                         [identifier, source, 'synthetic-inputs-' + source, tract])
            lineage('health_indicator', identifier, source)
    conn.execute("UPDATE ingestion_run SET completed_at=now() WHERE run_id LIKE 'synthetic-inputs-run-%'")
