CREATE TABLE IF NOT EXISTS source_registry (
    source_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    steward VARCHAR NOT NULL,
    source_url VARCHAR NOT NULL,
    api_url VARCHAR,
    dataset_id VARCHAR,
    update_frequency VARCHAR,
    geographic_precision VARCHAR,
    temporal_precision VARCHAR,
    license VARCHAR,
    estimated_size VARCHAR,
    incremental_method VARCHAR,
    last_successful_update TIMESTAMP,
    known_limitations VARCHAR,
    config_json JSON NOT NULL,
    registered_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_run (
    run_id VARCHAR PRIMARY KEY,
    source_id VARCHAR,
    command VARCHAR NOT NULL,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    status VARCHAR NOT NULL,
    request_count BIGINT DEFAULT 0,
    downloaded_bytes BIGINT DEFAULT 0,
    inserted_records BIGINT DEFAULT 0,
    updated_records BIGINT DEFAULT 0,
    deleted_records BIGINT DEFAULT 0,
    retry_count INTEGER DEFAULT 0,
    http_statuses_json JSON,
    schema_changes_json JSON,
    error_json JSON,
    warnings_json JSON
);

CREATE TABLE IF NOT EXISTS dataset_snapshot (
    snapshot_id VARCHAR PRIMARY KEY,
    source_id VARCHAR NOT NULL,
    run_id VARCHAR,
    dataset_id VARCHAR,
    vintage VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    source_url VARCHAR NOT NULL,
    query_json JSON,
    content_type VARCHAR,
    byte_size BIGINT,
    sha256 VARCHAR,
    license VARCHAR,
    status VARCHAR NOT NULL,
    raw_path VARCHAR,
    metadata_path VARCHAR,
    row_count BIGINT,
    schema_json JSON,
    warnings_json JSON,
    http_status INTEGER
);

-- Additive migrations for databases created by earlier NYC Lens builds.
ALTER TABLE ingestion_run ADD COLUMN IF NOT EXISTS http_statuses_json JSON;
ALTER TABLE dataset_snapshot ADD COLUMN IF NOT EXISTS http_status INTEGER;

CREATE TABLE IF NOT EXISTS raw_file (
    raw_file_id VARCHAR PRIMARY KEY,
    snapshot_id VARCHAR NOT NULL,
    original_filename VARCHAR NOT NULL,
    local_path VARCHAR NOT NULL,
    byte_size BIGINT NOT NULL,
    sha256 VARCHAR NOT NULL,
    content_type VARCHAR,
    retrieved_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS external_table_catalog (
    table_name VARCHAR PRIMARY KEY,
    storage_format VARCHAR NOT NULL,
    storage_root VARCHAR NOT NULL,
    partitioning_json JSON NOT NULL,
    row_count BIGINT NOT NULL,
    byte_size BIGINT NOT NULL,
    file_count BIGINT NOT NULL,
    manifest_sha256 VARCHAR,
    last_verified_at TIMESTAMP NOT NULL,
    layout_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS external_partition_catalog (
    partition_id VARCHAR PRIMARY KEY,
    table_name VARCHAR NOT NULL,
    region VARCHAR NOT NULL,
    source_id VARCHAR NOT NULL,
    record_year VARCHAR NOT NULL,
    relative_path VARCHAR NOT NULL,
    row_count BIGINT NOT NULL,
    distinct_id_count BIGINT NOT NULL,
    byte_size BIGINT NOT NULL,
    sha256 VARCHAR NOT NULL,
    min_record_date DATE,
    max_record_date DATE,
    written_at TIMESTAMP NOT NULL,
    layout_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS standardized_record (
    -- Deliberately not a physical PRIMARY KEY: tens of millions of lineage rows
    -- make DuckDB's ART index larger than the catalogue itself. Ingestion uses
    -- deterministic ids plus anti-joins, and QA verifies global uniqueness.
    standardized_record_id VARCHAR NOT NULL,
    entity_type VARCHAR NOT NULL,
    entity_id VARCHAR NOT NULL,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    source_record_id VARCHAR,
    method_version VARCHAR NOT NULL,
    standardized_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS calculation_version (
    calculation_version_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    version VARCHAR NOT NULL,
    code_hash VARCHAR,
    config_hash VARCHAR,
    created_at TIMESTAMP NOT NULL,
    notes VARCHAR
);

CREATE TABLE IF NOT EXISTS address (
    address_id VARCHAR PRIMARY KEY,
    original_input VARCHAR NOT NULL,
    standardized_address VARCHAR,
    borough VARCHAR,
    state_fips VARCHAR,
    state_abbr VARCHAR,
    county_geoid VARCHAR,
    county_name VARCHAR,
    region_code VARCHAR,
    region_name VARCHAR,
    subregion VARCHAR,
    zipcode VARCHAR,
    longitude DOUBLE,
    latitude DOUBLE,
    bbl VARCHAR,
    bin VARCHAR,
    census_tract VARCHAR,
    census_block VARCHAR,
    geocoder VARCHAR NOT NULL,
    match_status VARCHAR NOT NULL,
    confidence DOUBLE,
    raw_response_json JSON,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR DEFAULT 'address',
    geography_id VARCHAR,
    measure_type VARCHAR DEFAULT 'administrative_record',
    method_version VARCHAR NOT NULL
);

ALTER TABLE address ADD COLUMN IF NOT EXISTS state_fips VARCHAR;
ALTER TABLE address ADD COLUMN IF NOT EXISTS state_abbr VARCHAR;
ALTER TABLE address ADD COLUMN IF NOT EXISTS county_geoid VARCHAR;
ALTER TABLE address ADD COLUMN IF NOT EXISTS county_name VARCHAR;
ALTER TABLE address ADD COLUMN IF NOT EXISTS region_code VARCHAR;
ALTER TABLE address ADD COLUMN IF NOT EXISTS region_name VARCHAR;
ALTER TABLE address ADD COLUMN IF NOT EXISTS subregion VARCHAR;

CREATE TABLE IF NOT EXISTS region_definition (
    region_definition_id VARCHAR PRIMARY KEY,
    region_code VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    definition_vintage VARCHAR NOT NULL,
    member_count INTEGER NOT NULL,
    definition_json JSON NOT NULL,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR DEFAULT 'combined_statistical_area',
    geography_id VARCHAR,
    measure_type VARCHAR DEFAULT 'official_definition',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS region_member (
    region_member_id VARCHAR PRIMARY KEY,
    region_code VARCHAR NOT NULL,
    region_name VARCHAR NOT NULL,
    geography_type VARCHAR NOT NULL,
    geography_id VARCHAR NOT NULL,
    geography_name VARCHAR NOT NULL,
    state_fips VARCHAR NOT NULL,
    state_abbr VARCHAR NOT NULL,
    subregion VARCHAR,
    member_role VARCHAR,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    measure_type VARCHAR DEFAULT 'official_definition',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS address_validation_case (
    validation_case_id VARCHAR PRIMARY KEY,
    validation_batch_id VARCHAR NOT NULL,
    source_business_id VARCHAR,
    input_address VARCHAR NOT NULL,
    expected_borough VARCHAR,
    expected_bbl VARCHAR,
    expected_longitude DOUBLE,
    expected_latitude DOUBLE,
    matched BOOLEAN NOT NULL,
    standardized_address VARCHAR,
    observed_borough VARCHAR,
    observed_longitude DOUBLE,
    observed_latitude DOUBLE,
    coordinate_error_m DOUBLE,
    borough_match BOOLEAN,
    success BOOLEAN NOT NULL,
    error_type VARCHAR,
    error_message VARCHAR,
    source_id VARCHAR NOT NULL,
    source_snapshot_id VARCHAR NOT NULL,
    geocoder_snapshot_id VARCHAR,
    checked_at TIMESTAMP NOT NULL,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS parcel (
    -- Deterministic BBL ids are validated on staged MapPLUTO partitions and by QA.
    parcel_id VARCHAR NOT NULL,
    bbl VARCHAR,
    borough VARCHAR,
    block VARCHAR,
    lot VARCHAR,
    address VARCHAR,
    land_use VARCHAR,
    zoning VARCHAR,
    building_class VARCHAR,
    lot_area_sqft DOUBLE,
    building_area_sqft DOUBLE,
    residential_area_sqft DOUBLE,
    office_area_sqft DOUBLE,
    retail_area_sqft DOUBLE,
    units_residential DOUBLE,
    units_total DOUBLE,
    year_built INTEGER,
    min_longitude DOUBLE,
    min_latitude DOUBLE,
    max_longitude DOUBLE,
    max_latitude DOUBLE,
    geom_wkt VARCHAR,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR DEFAULT 'tax_lot',
    geography_id VARCHAR,
    measure_type VARCHAR DEFAULT 'administrative_record',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

ALTER TABLE parcel ADD COLUMN IF NOT EXISTS min_longitude DOUBLE;
ALTER TABLE parcel ADD COLUMN IF NOT EXISTS min_latitude DOUBLE;
ALTER TABLE parcel ADD COLUMN IF NOT EXISTS max_longitude DOUBLE;
ALTER TABLE parcel ADD COLUMN IF NOT EXISTS max_latitude DOUBLE;

CREATE TABLE IF NOT EXISTS building (
    building_id VARCHAR PRIMARY KEY,
    bin VARCHAR,
    bbl VARCHAR,
    name VARCHAR,
    address VARCHAR,
    min_longitude DOUBLE,
    min_latitude DOUBLE,
    max_longitude DOUBLE,
    max_latitude DOUBLE,
    geom_wkt VARCHAR,
    attributes_json JSON,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR DEFAULT 'building',
    geography_id VARCHAR,
    measure_type VARCHAR DEFAULT 'administrative_record',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

ALTER TABLE building ADD COLUMN IF NOT EXISTS min_longitude DOUBLE;
ALTER TABLE building ADD COLUMN IF NOT EXISTS min_latitude DOUBLE;
ALTER TABLE building ADD COLUMN IF NOT EXISTS max_longitude DOUBLE;
ALTER TABLE building ADD COLUMN IF NOT EXISTS max_latitude DOUBLE;

CREATE TABLE IF NOT EXISTS geo_boundary (
    boundary_id VARCHAR PRIMARY KEY,
    geography_type VARCHAR NOT NULL,
    geography_id VARCHAR NOT NULL,
    name VARCHAR,
    parent_id VARCHAR,
    geom_wkt VARCHAR NOT NULL,
    area_m2 DOUBLE,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    measure_type VARCHAR DEFAULT 'administrative_record',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

ALTER TABLE geo_boundary ADD COLUMN IF NOT EXISTS min_longitude DOUBLE;
ALTER TABLE geo_boundary ADD COLUMN IF NOT EXISTS min_latitude DOUBLE;
ALTER TABLE geo_boundary ADD COLUMN IF NOT EXISTS max_longitude DOUBLE;
ALTER TABLE geo_boundary ADD COLUMN IF NOT EXISTS max_latitude DOUBLE;

CREATE TABLE IF NOT EXISTS geo_cell (
    cell_id VARCHAR PRIMARY KEY,
    h3_resolution INTEGER NOT NULL,
    geom_wkt VARCHAR NOT NULL,
    centroid_lon DOUBLE,
    centroid_lat DOUBLE,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR DEFAULT 'h3',
    geography_id VARCHAR,
    measure_type VARCHAR DEFAULT 'model_grid',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS census_estimate (
    -- Deterministic geography-variable ids are validated at refresh and by QA.
    census_estimate_id VARCHAR NOT NULL,
    geography_id VARCHAR NOT NULL,
    geography_type VARCHAR NOT NULL,
    variable VARCHAR NOT NULL,
    label VARCHAR,
    estimate DOUBLE,
    margin_of_error DOUBLE,
    unit VARCHAR,
    survey_period VARCHAR NOT NULL,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    measure_type VARCHAR DEFAULT 'survey_estimate',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS workforce_estimate (
    -- LODES refreshes validate deterministic ids before replacing the source;
    -- omitting the physical ART primary-key index keeps the local catalogue
    -- below the project-wide 5 GB single-file safety cap.
    workforce_estimate_id VARCHAR NOT NULL,
    geography_id VARCHAR NOT NULL,
    geography_type VARCHAR NOT NULL,
    segment VARCHAR NOT NULL,
    job_type VARCHAR NOT NULL,
    measure VARCHAR NOT NULL,
    value DOUBLE,
    longitude DOUBLE,
    latitude DOUBLE,
    current_county_geoid VARCHAR,
    state_abbr VARCHAR,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    measure_type VARCHAR DEFAULT 'administrative_estimate',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

ALTER TABLE workforce_estimate ADD COLUMN IF NOT EXISTS current_county_geoid VARCHAR;
ALTER TABLE workforce_estimate ADD COLUMN IF NOT EXISTS state_abbr VARCHAR;

CREATE TABLE IF NOT EXISTS business_location (
    business_location_id VARCHAR PRIMARY KEY,
    source_business_id VARCHAR,
    name VARCHAR NOT NULL,
    normalized_name VARCHAR,
    address VARCHAR,
    borough VARCHAR,
    zipcode VARCHAR,
    longitude DOUBLE,
    latitude DOUBLE,
    primary_category VARCHAR,
    secondary_categories_json JSON,
    cuisine VARCHAR,
    status VARCHAR,
    opened_at DATE,
    closed_at DATE,
    dedupe_key VARCHAR,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR DEFAULT 'point',
    geography_id VARCHAR,
    measure_type VARCHAR DEFAULT 'administrative_record',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS poi (
    poi_id VARCHAR PRIMARY KEY,
    source_poi_id VARCHAR,
    name VARCHAR,
    basic_category VARCHAR,
    taxonomy_json JSON,
    address VARCHAR,
    longitude DOUBLE,
    latitude DOUBLE,
    source_items_json JSON,
    status VARCHAR,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR DEFAULT 'point',
    geography_id VARCHAR,
    measure_type VARCHAR DEFAULT 'compiled_record',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS business_category (
    category_id VARCHAR PRIMARY KEY,
    business_type VARCHAR NOT NULL,
    category VARCHAR NOT NULL,
    relation VARCHAR NOT NULL,
    weight DOUBLE DEFAULT 1,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS license (
    license_id VARCHAR PRIMARY KEY,
    source_license_id VARCHAR,
    business_location_id VARCHAR,
    license_type VARCHAR,
    status VARCHAR,
    issued_at DATE,
    expires_at DATE,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR DEFAULT 'point',
    geography_id VARCHAR,
    measure_type VARCHAR DEFAULT 'administrative_record',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS inspection (
    -- Source-row-derived ids are validated after source replacement and by QA.
    inspection_id VARCHAR NOT NULL,
    business_location_id VARCHAR,
    source_inspection_id VARCHAR,
    inspection_date DATE,
    score DOUBLE,
    grade VARCHAR,
    action VARCHAR,
    violation_code VARCHAR,
    violation_description VARCHAR,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR DEFAULT 'point',
    geography_id VARCHAR,
    measure_type VARCHAR DEFAULT 'administrative_record',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS transit_stop (
    transit_stop_id VARCHAR PRIMARY KEY,
    source_stop_id VARCHAR,
    complex_id VARCHAR,
    name VARCHAR NOT NULL,
    borough VARCHAR,
    routes_json JSON,
    ada_status VARCHAR,
    stop_kind VARCHAR,
    entrance_type VARCHAR,
    entry_allowed BOOLEAN,
    exit_allowed BOOLEAN,
    longitude DOUBLE,
    latitude DOUBLE,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR DEFAULT 'point',
    geography_id VARCHAR,
    measure_type VARCHAR DEFAULT 'administrative_record',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

ALTER TABLE transit_stop ADD COLUMN IF NOT EXISTS stop_kind VARCHAR;
ALTER TABLE transit_stop ADD COLUMN IF NOT EXISTS entrance_type VARCHAR;
ALTER TABLE transit_stop ADD COLUMN IF NOT EXISTS entry_allowed BOOLEAN;
ALTER TABLE transit_stop ADD COLUMN IF NOT EXISTS exit_allowed BOOLEAN;

CREATE TABLE IF NOT EXISTS transit_ridership (
    transit_ridership_id VARCHAR PRIMARY KEY,
    complex_id VARCHAR,
    period_start TIMESTAMP,
    period_end TIMESTAMP,
    hour_of_day INTEGER,
    day_type VARCHAR,
    ridership DOUBLE,
    transfers DOUBLE,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR DEFAULT 'station_complex',
    geography_id VARCHAR,
    measure_type VARCHAR DEFAULT 'administrative_estimate',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS traffic_observation (
    traffic_observation_id VARCHAR PRIMARY KEY,
    observation_type VARCHAR,
    observed_at TIMESTAMP,
    value DOUBLE,
    unit VARCHAR,
    longitude DOUBLE,
    latitude DOUBLE,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR,
    geography_id VARCHAR,
    measure_type VARCHAR,
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS mobility_proxy (
    mobility_proxy_id VARCHAR PRIMARY KEY,
    proxy_type VARCHAR NOT NULL,
    value DOUBLE,
    unit VARCHAR,
    period VARCHAR,
    longitude DOUBLE,
    latitude DOUBLE,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR,
    geography_id VARCHAR,
    measure_type VARCHAR DEFAULT 'proxy',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

ALTER TABLE mobility_proxy ADD COLUMN IF NOT EXISTS longitude DOUBLE;
ALTER TABLE mobility_proxy ADD COLUMN IF NOT EXISTS latitude DOUBLE;

CREATE TABLE IF NOT EXISTS hotel (
    hotel_id VARCHAR PRIMARY KEY,
    source_hotel_id VARCHAR,
    name VARCHAR,
    address VARCHAR,
    borough VARCHAR,
    rooms DOUBLE,
    status VARCHAR,
    longitude DOUBLE,
    latitude DOUBLE,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR DEFAULT 'point',
    geography_id VARCHAR,
    measure_type VARCHAR DEFAULT 'administrative_record',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS short_term_rental (
    short_term_rental_id VARCHAR PRIMARY KEY,
    source_listing_id VARCHAR,
    status VARCHAR,
    longitude DOUBLE,
    latitude DOUBLE,
    attributes_json JSON,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR DEFAULT 'point',
    geography_id VARCHAR,
    measure_type VARCHAR DEFAULT 'administrative_record',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS health_indicator (
    health_indicator_id VARCHAR PRIMARY KEY,
    indicator_id VARCHAR,
    name VARCHAR NOT NULL,
    measure VARCHAR,
    value DOUBLE,
    unit VARCHAR,
    margin_of_error DOUBLE,
    message VARCHAR,
    period VARCHAR,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR NOT NULL,
    geography_id VARCHAR NOT NULL,
    measure_type VARCHAR NOT NULL,
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS environment_indicator (
    environment_indicator_id VARCHAR PRIMARY KEY,
    indicator_id VARCHAR,
    name VARCHAR NOT NULL,
    measure VARCHAR,
    value DOUBLE,
    unit VARCHAR,
    message VARCHAR,
    period VARCHAR,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR NOT NULL,
    geography_id VARCHAR NOT NULL,
    measure_type VARCHAR NOT NULL,
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS property_record (
    property_record_id VARCHAR PRIMARY KEY,
    parcel_id VARCHAR,
    record_type VARCHAR,
    record_date DATE,
    amount DOUBLE,
    attributes_json JSON,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR DEFAULT 'tax_lot',
    geography_id VARCHAR,
    measure_type VARCHAR DEFAULT 'administrative_record',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS regulatory_record (
    regulatory_record_id VARCHAR PRIMARY KEY,
    parcel_id VARCHAR,
    record_type VARCHAR,
    status VARCHAR,
    issued_at DATE,
    closed_at DATE,
    attributes_json JSON,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    valid_from DATE,
    valid_to DATE,
    geography_type VARCHAR,
    geography_id VARCHAR,
    measure_type VARCHAR DEFAULT 'administrative_record',
    confidence DOUBLE,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS transaction_case (
    -- Ranked public-record candidates create deterministic ids; QA verifies uniqueness.
    transaction_case_id VARCHAR NOT NULL,
    case_type VARCHAR,
    business_type VARCHAR,
    transaction_date DATE,
    amount DOUBLE,
    geography_type VARCHAR,
    geography_id VARCHAR,
    attributes_json JSON,
    source_id VARCHAR,
    snapshot_id VARCHAR,
    retrieved_at TIMESTAMP,
    valid_from DATE,
    valid_to DATE,
    measure_type VARCHAR,
    confidence DOUBLE,
    method_version VARCHAR
);

CREATE TABLE IF NOT EXISTS site_query (
    site_id VARCHAR PRIMARY KEY,
    original_input VARCHAR NOT NULL,
    standardized_address VARCHAR,
    business_type VARCHAR NOT NULL,
    address_id VARCHAR,
    longitude DOUBLE,
    latitude DOUBLE,
    parcel_id VARCHAR,
    building_id VARCHAR,
    requested_catchments_json JSON NOT NULL,
    snapshot_set_json JSON NOT NULL,
    calculation_version_id VARCHAR NOT NULL,
    result_json JSON,
    warnings_json JSON,
    missing_data_json JSON,
    status VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP
);

ALTER TABLE site_query ADD COLUMN IF NOT EXISTS building_id VARCHAR;

CREATE TABLE IF NOT EXISTS catchment (
    catchment_id VARCHAR PRIMARY KEY,
    site_id VARCHAR NOT NULL,
    catchment_type VARCHAR NOT NULL,
    distance_m DOUBLE,
    travel_minutes DOUBLE,
    network_source VARCHAR,
    network_snapshot_id VARCHAR,
    geom_wkt VARCHAR NOT NULL,
    area_m2 DOUBLE NOT NULL,
    method_version VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL,
    warnings_json JSON
);

CREATE TABLE IF NOT EXISTS site_metric (
    site_metric_id VARCHAR PRIMARY KEY,
    site_id VARCHAR NOT NULL,
    catchment_id VARCHAR NOT NULL,
    metric_id VARCHAR NOT NULL,
    value DOUBLE,
    text_value VARCHAR,
    unit VARCHAR,
    percentile DOUBLE,
    source_id VARCHAR,
    snapshot_id VARCHAR,
    source_year VARCHAR,
    measure_type VARCHAR NOT NULL,
    confidence DOUBLE,
    margin_of_error DOUBLE,
    geography_type VARCHAR,
    geography_id VARCHAR,
    calculation_version_id VARCHAR NOT NULL,
    evidence_record_ids_json JSON,
    warnings_json JSON,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS benchmark_observation (
    benchmark_observation_id VARCHAR PRIMARY KEY,
    benchmark_run_id VARCHAR NOT NULL,
    benchmark_scope VARCHAR NOT NULL,
    sample_cell_id VARCHAR NOT NULL,
    borough VARCHAR NOT NULL,
    catchment_type VARCHAR NOT NULL,
    metric_id VARCHAR NOT NULL,
    value DOUBLE NOT NULL,
    unit VARCHAR,
    percentile DOUBLE,
    source_id VARCHAR,
    snapshot_id VARCHAR,
    source_year VARCHAR,
    measure_type VARCHAR NOT NULL,
    confidence DOUBLE,
    snapshot_set_json JSON NOT NULL,
    created_at TIMESTAMP NOT NULL,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS benchmark_quantile (
    benchmark_quantile_id VARCHAR PRIMARY KEY,
    benchmark_run_id VARCHAR NOT NULL,
    benchmark_scope VARCHAR NOT NULL,
    catchment_type VARCHAR NOT NULL,
    metric_id VARCHAR NOT NULL,
    quantile DOUBLE NOT NULL,
    value DOUBLE NOT NULL,
    sample_size BIGINT NOT NULL,
    geography_basis VARCHAR NOT NULL,
    source_ids_json JSON NOT NULL,
    snapshot_set_json JSON NOT NULL,
    source_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    measure_type VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL,
    method_version VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS site_report (
    report_id VARCHAR PRIMARY KEY,
    site_id VARCHAR NOT NULL,
    format VARCHAR NOT NULL,
    local_path VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    generated_at TIMESTAMP NOT NULL,
    calculation_version_id VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS site_comparison (
    comparison_id VARCHAR PRIMARY KEY,
    original_inputs_json JSON NOT NULL,
    business_type VARCHAR NOT NULL,
    requested_catchments_json JSON NOT NULL,
    comparison_catchment VARCHAR NOT NULL,
    site_ids_json JSON NOT NULL,
    snapshot_set_json JSON NOT NULL,
    calculation_version_id VARCHAR NOT NULL,
    result_json JSON,
    warnings_json JSON,
    status VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS comparison_report (
    comparison_report_id VARCHAR PRIMARY KEY,
    comparison_id VARCHAR NOT NULL,
    format VARCHAR NOT NULL,
    local_path VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    generated_at TIMESTAMP NOT NULL,
    calculation_version_id VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS scoring_model (
    model_id VARCHAR PRIMARY KEY,
    business_type VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    training_case_count BIGINT NOT NULL,
    holdout_case_count BIGINT NOT NULL,
    feature_ids_json JSON NOT NULL,
    validation_metrics_json JSON NOT NULL,
    artifact_path VARCHAR,
    artifact_sha256 VARCHAR,
    created_at TIMESTAMP NOT NULL,
    activated_at TIMESTAMP,
    method_version VARCHAR NOT NULL,
    notes_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_result (
    qa_result_id VARCHAR PRIMARY KEY,
    run_id VARCHAR,
    check_id VARCHAR NOT NULL,
    scope VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    observed_value VARCHAR,
    expected_value VARCHAR,
    details_json JSON,
    checked_at TIMESTAMP NOT NULL
);
