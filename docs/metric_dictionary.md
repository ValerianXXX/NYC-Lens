# Metric dictionary

This table is generated from `config/metrics.yml`. Metric values, years, confidence, margins of error, and evidence snapshots are determined by each query result.

| metric_id | English name | Primary source | Fact/measure type | Aggregation | Primary limitation |
|---|---|---|---|---|---|
| resident_population | Resident Population | acs_2024_5yr | survey_estimate | area_weighted | See source metadata and methodology. |
| decennial_population_2020 | Decennial Population 2020 | census_decennial_2020_pl | area_weighted_decennial_enumeration_count | area_weighted_census_block | See source metadata and methodology. |
| median_household_income | Median Household Income | acs_2024_5yr | survey_estimate | population_weighted | See source metadata and methodology. |
| median_age | Median Age | acs_2024_5yr | survey_estimate | population_weighted | See source metadata and methodology. |
| daytime_jobs | Daytime Jobs | lodes_2023 | administrative_estimate | point_sum | See source metadata and methodology. |
| direct_competitor_count | Direct Competitor Count | dohmh_restaurants | classified_administrative_location_count | business_taxonomy_filtered_distinct_location_count | See source metadata and methodology. |
| subway_entries_2025 | Subway Entries 2025 | mta_hourly_2025 | administrative_estimate | annual_entries_sum_for_station_complexes_in_catchment | See source metadata and methodology. |
| subway_entrance_exit_count | Subway Entrance Exit Count | mta_subway_entrances_2024 | deduplicated_administrative_point_count | coordinate_type_permission_deduplicated_points_in_catchment | See source metadata and methodology. |
| subway_entry_allowed_count | Subway Entry Allowed Count | mta_subway_entrances_2024 | deduplicated_administrative_point_count | entry_allowed_points_after_physical_deduplication | See source metadata and methodology. |
| visitor_proxy_index | Visitor Proxy Index | derived | model_proxy | — | See source metadata and methodology. |
| alcohol_license_count | Alcohol License Count | nysla_active_licenses | administrative_record_count | licensed_point_count | See source metadata and methodology. |
| legal_short_term_rental_count | Legal Short Term Rental Count | nyc_str_registration | administrative_record_count | registered_building_point_count | See source metadata and methodology. |
| public_facility_count | Public Facility Count | nyc_facilities | compiled_administrative_record_count | deduplicated_facility_point_count | See source metadata and methodology. |
| school_count | School Count | nyc_facilities | compiled_administrative_record_count | deduplicated_facility_point_count | See source metadata and methodology. |
| health_facility_count | Health Facility Count | nyc_facilities | compiled_administrative_record_count | deduplicated_facility_point_count | See source metadata and methodology. |
| park_facility_count | Park Facility Count | nyc_facilities | compiled_administrative_record_count | deduplicated_facility_point_count | See source metadata and methodology. |
| culture_and_library_count | Culture And Library Count | nyc_facilities | compiled_administrative_record_count | deduplicated_facility_point_count | See source metadata and methodology. |
| parcel_count | Parcel Count | mappluto_26v2 | intersecting_administrative_record_count | exact_geometry_intersection_count | See source metadata and methodology. |
| intersecting_lot_area_sqft | Intersecting Lot Area Sqft | mappluto_26v2 | parcel_overlap_weighted_administrative_sum | parcel_overlap_weighted_sum | See source metadata and methodology. |
| building_floor_area_sqft | Building Floor Area Sqft | mappluto_26v2 | parcel_overlap_weighted_administrative_sum | parcel_overlap_weighted_sum | See source metadata and methodology. |
| residential_floor_area_sqft | Residential Floor Area Sqft | mappluto_26v2 | parcel_overlap_weighted_administrative_sum | parcel_overlap_weighted_sum | See source metadata and methodology. |
| office_floor_area_sqft | Office Floor Area Sqft | mappluto_26v2 | parcel_overlap_weighted_administrative_sum | parcel_overlap_weighted_sum | See source metadata and methodology. |
| retail_floor_area_sqft | Retail Floor Area Sqft | mappluto_26v2 | parcel_overlap_weighted_administrative_sum | parcel_overlap_weighted_sum | See source metadata and methodology. |
| residential_units | Residential Units | mappluto_26v2 | parcel_overlap_weighted_administrative_sum | parcel_overlap_weighted_sum | See source metadata and methodology. |
| total_units | Total Units | mappluto_26v2 | parcel_overlap_weighted_administrative_sum | parcel_overlap_weighted_sum | See source metadata and methodology. |
| building_floor_area_density | Building Floor Area Density | derived | derived_spatial_density | gross_floor_area_divided_by_total_catchment_area | See source metadata and methodology. |
| residential_floor_area_density | Residential Floor Area Density | derived | derived_spatial_density | residential_floor_area_divided_by_total_catchment_area | See source metadata and methodology. |
| office_floor_area_density | Office Floor Area Density | derived | derived_spatial_density | office_floor_area_divided_by_total_catchment_area | See source metadata and methodology. |
| retail_floor_area_density | Retail Floor Area Density | derived | derived_spatial_density | retail_floor_area_divided_by_total_catchment_area | See source metadata and methodology. |
| building_footprint_count | Building Footprint Count | nyc_building_footprints | intersecting_administrative_record_count | exact_geometry_intersection_count | See source metadata and methodology. |
| rolling_sale_record_count | Rolling Sale Record Count | nyc_rolling_sales | administrative_transaction_record_count | records_linked_to_intersecting_bbls | See source metadata and methodology. |
| rolling_sale_price_median | Rolling Sale Price Median | nyc_rolling_sales | median_positive_recorded_sale_price | median_positive_amount | See source metadata and methodology. |
| rolling_sales_per_100_parcels | Rolling Sales Per 100 Parcels | derived | derived_transaction_density | sale_records_divided_by_intersecting_parcel_count | See source metadata and methodology. |
| acris_document_record_count_24m | Acris Document Record Count 24M | acris_real_property_master_recent | administrative_property_document_count | selected_documents_locally_joined_to_intersecting_bbls | See source metadata and methodology. |
| acris_deed_document_count_24m | Acris Deed Document Count 24M | acris_real_property_master_recent | administrative_property_document_count | deed_documents_locally_joined_to_intersecting_bbls | See source metadata and methodology. |
| acris_mortgage_document_count_24m | Acris Mortgage Document Count 24M | acris_real_property_master_recent | administrative_property_document_count | mortgage_documents_locally_joined_to_intersecting_bbls | See source metadata and methodology. |
| acris_document_records_per_100_parcels_24m | Acris Document Records Per 100 Parcels 24M | derived | derived_property_document_density | document_lot_records_divided_by_intersecting_parcel_count | See source metadata and methodology. |
| historical_transaction_case_count | Historical Transaction Case Count | derived_public_transaction_cases | privacy_minimized_public_case_count | deduplicated_cases_linked_to_intersecting_tax_lots | See source metadata and methodology. |
| historical_transaction_amount_median | Historical Transaction Amount Median | derived_public_transaction_cases | median_positive_public_case_amount | median_positive_amount_of_cases_linked_to_intersecting_tax_lots | See source metadata and methodology. |
| building_permit_issued_24m_count | Building Permit Issued 24M Count | derived | combined_administrative_record_count | bis_and_dob_now_records_linked_to_intersecting_bbls | See source metadata and methodology. |
| active_dob_violation_count | Active Dob Violation Count | derived | deduplicated_administrative_record_count | legacy_and_dob_now_active_records_by_bbl_and_violation_number | See source metadata and methodology. |
| active_dob_violations_per_100_parcels | Active Dob Violations Per 100 Parcels | derived | deduplicated_administrative_record_density | active_records_divided_by_intersecting_parcel_count | See source metadata and methodology. |
| active_ecb_violation_count | Active Ecb Violation Count | dob_ecb_violations_active | deduplicated_administrative_record_count | active_records_by_bbl_and_ecb_number | See source metadata and methodology. |
| active_ecb_violations_per_100_parcels | Active Ecb Violations Per 100 Parcels | derived | deduplicated_administrative_record_density | active_records_divided_by_intersecting_parcel_count | See source metadata and methodology. |
| regulatory_risk_signal | Regulatory Risk Signal | derived_citywide_benchmark | benchmark_classification | median_component_percentile_with_33_67_bands | See source metadata and methodology. |
| certificate_of_occupancy_issued_24m_count | Certificate Of Occupancy Issued 24M Count | derived | combined_administrative_record_count | bis_and_dob_now_records_linked_to_intersecting_bbls | See source metadata and methodology. |
| 311_request_count_24m | 311 Request Count 24M | nyc_311_recent_aggregates | server_aggregated_administrative_request_sum | bbl_problem_agency_aggregate_sum | See source metadata and methodology. |
| 311_noise_request_count_24m | 311 Noise Request Count 24M | nyc_311_recent_aggregates | server_aggregated_administrative_request_sum | aggregate_sum_where_problem_contains_noise | See source metadata and methodology. |
| 311_requests_per_100_parcels_24m | 311 Requests Per 100 Parcels 24M | derived | derived_request_density | aggregate_requests_divided_by_intersecting_parcel_count | See source metadata and methodology. |
| 311_noise_requests_per_100_parcels_24m | 311 Noise Requests Per 100 Parcels 24M | derived | derived_request_density | aggregate_noise_requests_divided_by_intersecting_parcel_count | See source metadata and methodology. |
| vehicle_volume_monitor_count | Vehicle Volume Monitor Count | nyc_dot_traffic_counts | observed_site_count | distinct_monitored_segment_count | See source metadata and methodology. |
| bus_stop_count | Bus Stop Count | mta_bus_gtfs_static | scheduled_transit_record_count | exact_name_and_rounded_coordinate_deduplicated_points_in_catchment | See source metadata and methodology. |
| citibike_station_count | Citibike Station Count | citibike_gbfs_current | published_bikeshare_station_count | station_points_in_catchment | See source metadata and methodology. |
| citibike_bikes_available_current | Citibike Bikes Available Current | citibike_gbfs_current | real_time_station_status_sum | station_status_sum_in_catchment | See source metadata and methodology. |
| citibike_docks_available_current | Citibike Docks Available Current | citibike_gbfs_current | real_time_station_status_sum | station_status_sum_in_catchment | See source metadata and methodology. |
| hurricane_evacuation_overlap_pct | Hurricane Evacuation Overlap Pct | nyc_hurricane_evacuation_zones | exact_projected_area_overlap | numbered_evacuation_zone_union_intersection_divided_by_catchment_area | See source metadata and methodology. |
| future_2050_floodplain_overlap_pct | Future 2050 Floodplain Overlap Pct | nyc_future_floodplain_2050s | exact_projected_area_overlap | projected_floodplain_union_intersection_divided_by_catchment_area | See source metadata and methodology. |
| sea_level_2100_floodplain_overlap_pct | Sea Level 2100 Floodplain Overlap Pct | nyc_sea_level_rise_2100_floodplain | exact_projected_area_overlap | projected_floodplain_union_intersection_divided_by_catchment_area | See source metadata and methodology. |
| environmental_exposure_profile | Environmental Exposure Profile | derived | transparent_threshold_classification | maximum_catchment_overlap_across_hurricane_and_planning_flood_layers | See source metadata and methodology. |
| tlc_taxi_zone_count | Tlc Taxi Zone Count | tlc_taxi_zones | exact_geometry_intersection_count | taxi_zone_polygon_intersection_count | See source metadata and methodology. |
| tlc_pickup_trips_monthly_area_weighted | Tlc Pickup Trips Monthly Area Weighted | tlc_trip_record_monthly_aggregates | privacy_guarded_area_weighted_mobility_proxy | yellow_green_zone_hour_counts_weighted_by_catchment_share_of_taxi_zone | See source metadata and methodology. |
| tlc_dropoff_trips_monthly_area_weighted | Tlc Dropoff Trips Monthly Area Weighted | tlc_trip_record_monthly_aggregates | privacy_guarded_area_weighted_mobility_proxy | yellow_green_zone_hour_counts_weighted_by_catchment_share_of_taxi_zone | See source metadata and methodology. |
| tlc_pickup_trips_average_daily_area_weighted | Tlc Pickup Trips Average Daily Area Weighted | tlc_trip_record_monthly_aggregates | derived_daily_area_weighted_mobility_proxy | area_weighted_monthly_pickups_divided_by_calendar_days | See source metadata and methodology. |
| tlc_dropoff_trips_average_daily_area_weighted | Tlc Dropoff Trips Average Daily Area Weighted | tlc_trip_record_monthly_aggregates | derived_daily_area_weighted_mobility_proxy | area_weighted_monthly_dropoffs_divided_by_calendar_days | See source metadata and methodology. |
| vehicle_volume_median_15min | Vehicle Volume Median 15Min | nyc_dot_traffic_counts | median_of_road_segment_time_medians | median | See source metadata and methodology. |
| pedestrian_monitor_count | Pedestrian Monitor Count | nyc_dot_pedestrian_counts | observed_site_count | distinct_fixed_site_count | See source metadata and methodology. |
| pedestrian_count_latest_median | Pedestrian Count Latest Median | nyc_dot_pedestrian_counts | median_published_count | median | See source metadata and methodology. |
| traffic_speed_link_count | Traffic Speed Link Count | nyc_dot_traffic_speed_current | sensor_link_count | distinct_monitored_link_count | See source metadata and methodology. |
| traffic_speed_current_median_mph | Traffic Speed Current Median Mph | nyc_dot_traffic_speed_current | median_real_time_sensor_estimate | median | See source metadata and methodology. |
| resident_population_density_per_sq_mile | Resident Population Density Per Sq Mile | acs_2024_5yr | derived_area_weighted_survey_density | area_weighted_population_divided_by_projected_catchment_area | See source metadata and methodology. |
| per_capita_income | Per Capita Income | acs_2024_5yr | survey_estimate | population_weighted | See source metadata and methodology. |
| age_under_25 | Age Under 25 | acs_2024_5yr | survey_estimate | selected_age_lines_area_weighted_sum | See source metadata and methodology. |
| age_25_44 | Age 25 44 | acs_2024_5yr | survey_estimate | selected_age_lines_area_weighted_sum | See source metadata and methodology. |
| age_45_64 | Age 45 64 | acs_2024_5yr | survey_estimate | selected_age_lines_area_weighted_sum | See source metadata and methodology. |
| age_65_plus | Age 65 Plus | acs_2024_5yr | survey_estimate | selected_age_lines_area_weighted_sum | See source metadata and methodology. |
| bachelors_or_higher_share | Bachelors Or Higher Share | acs_2024_5yr | derived_survey_estimate | component_sum_divided_by_population_25_plus | See source metadata and methodology. |
| foreign_born_share | Foreign Born Share | acs_2024_5yr | derived_survey_estimate | numerator_divided_by_total | See source metadata and methodology. |
| family_household_share | Family Household Share | acs_2024_5yr | derived_survey_estimate | numerator_divided_by_total_households | See source metadata and methodology. |
| nonfamily_household_share | Nonfamily Household Share | acs_2024_5yr | derived_survey_estimate | numerator_divided_by_total_households | See source metadata and methodology. |
| civilian_labor_force_employment_rate | Civilian Labor Force Employment Rate | acs_2024_5yr | derived_survey_estimate | employed_divided_by_civilian_labor_force | See source metadata and methodology. |
| civilian_labor_force_unemployment_rate | Civilian Labor Force Unemployment Rate | acs_2024_5yr | derived_survey_estimate | unemployed_divided_by_civilian_labor_force | See source metadata and methodology. |
| non_hispanic_white_share | Non Hispanic White Share | acs_2024_5yr | derived_survey_estimate | numerator_divided_by_total_population | See source metadata and methodology. |
| non_hispanic_black_share | Non Hispanic Black Share | acs_2024_5yr | derived_survey_estimate | numerator_divided_by_total_population | See source metadata and methodology. |
| non_hispanic_asian_share | Non Hispanic Asian Share | acs_2024_5yr | derived_survey_estimate | numerator_divided_by_total_population | See source metadata and methodology. |
| hispanic_or_latino_share | Hispanic Or Latino Share | acs_2024_5yr | derived_survey_estimate | numerator_divided_by_total_population | See source metadata and methodology. |
| english_only_language_share | English Only Language Share | acs_2024_5yr | derived_survey_estimate | numerator_divided_by_population_age_5_plus | See source metadata and methodology. |
| spanish_language_share | Spanish Language Share | acs_2024_5yr | derived_survey_estimate | numerator_divided_by_population_age_5_plus | See source metadata and methodology. |
| other_indo_european_language_share | Other Indo European Language Share | acs_2024_5yr | derived_survey_estimate | numerator_divided_by_population_age_5_plus | See source metadata and methodology. |
| asian_pacific_language_share | Asian Pacific Language Share | acs_2024_5yr | derived_survey_estimate | numerator_divided_by_population_age_5_plus | See source metadata and methodology. |
| drove_alone_commute_share | Drove Alone Commute Share | acs_2024_5yr | derived_survey_estimate | numerator_divided_by_workers_commute_total | See source metadata and methodology. |
| public_transit_commute_share | Public Transit Commute Share | acs_2024_5yr | derived_survey_estimate | numerator_divided_by_workers_commute_total | See source metadata and methodology. |
| walked_commute_share | Walked Commute Share | acs_2024_5yr | derived_survey_estimate | numerator_divided_by_workers_commute_total | See source metadata and methodology. |
| worked_from_home_share | Worked From Home Share | acs_2024_5yr | derived_survey_estimate | numerator_divided_by_workers_commute_total | See source metadata and methodology. |
| no_vehicle_household_share | No Vehicle Household Share | acs_2024_5yr | derived_survey_estimate | numerator_divided_by_households_vehicle_total | See source metadata and methodology. |
| owner_occupied_share | Owner Occupied Share | acs_2024_5yr | derived_survey_estimate | numerator_divided_by_occupied_housing_units | See source metadata and methodology. |
| renter_occupied_share | Renter Occupied Share | acs_2024_5yr | derived_survey_estimate | numerator_divided_by_occupied_housing_units | See source metadata and methodology. |
| management_business_science_arts_occupation_share | Management Business Science Arts Occupation Share | acs_2024_5yr | derived_survey_estimate | male_and_female_broad_occupation_sum_divided_by_employed_total | See source metadata and methodology. |
| service_occupation_share | Service Occupation Share | acs_2024_5yr | derived_survey_estimate | male_and_female_broad_occupation_sum_divided_by_employed_total | See source metadata and methodology. |
| sales_office_occupation_share | Sales Office Occupation Share | acs_2024_5yr | derived_survey_estimate | male_and_female_broad_occupation_sum_divided_by_employed_total | See source metadata and methodology. |
| natural_resources_construction_maintenance_occupation_share | Natural Resources Construction Maintenance Occupation Share | acs_2024_5yr | derived_survey_estimate | male_and_female_broad_occupation_sum_divided_by_employed_total | See source metadata and methodology. |
| production_transport_material_moving_occupation_share | Production Transport Material Moving Occupation Share | acs_2024_5yr | derived_survey_estimate | male_and_female_broad_occupation_sum_divided_by_employed_total | See source metadata and methodology. |
| top_workplace_industries | Top Workplace Industries | lodes_2023 | administrative_estimate | top_five_industry_job_counts | See source metadata and methodology. |
| competitors_per_1000_residents | Competitors Per 1000 Residents | derived | derived_ratio | direct_competitors_divided_by_acs_population | See source metadata and methodology. |
| competitors_per_1000_jobs | Competitors Per 1000 Jobs | derived | derived_ratio | direct_competitors_divided_by_lodes_jobs | See source metadata and methodology. |
| substitute_count | Substitute Count | overture_places | classified_location_count | business_taxonomy_filtered_point_count | See source metadata and methodology. |
| demand_complement_count | Demand Complement Count | overture_places | classified_location_count | business_taxonomy_filtered_point_count | See source metadata and methodology. |
| attraction_count | Attraction Count | overture_places | classified_location_count | attraction_point_count | See source metadata and methodology. |
| hotel_count | Hotel Count | nyc_hotels | administrative_record_count | hotel_points_in_catchment | See source metadata and methodology. |
| nightlife_location_count | Nightlife Location Count | derived | classified_location_count | nightlife_category_points_in_catchment | See source metadata and methodology. |
| closed_competitor_count | Closed Competitor Count | dohmh_restaurants | classified_administrative_location_count | source_status_closed_direct_competitors | See source metadata and methodology. |
| subway_complex_count | Subway Complex Count | mta_subway_stations | administrative_record_count | distinct_station_complexes_in_catchment | See source metadata and methodology. |
| regional_rail_station_count | Regional Rail Station Count | regional_mta_rail_gtfs_static | scheduled_transit_record_count | distinct_parent_station_points_in_catchment | See source metadata and methodology. |
| regional_transit_poi_count | Regional Transit Poi Count | overture_places | compiled_place_proxy_count | transit_category_place_points_in_catchment | See source metadata and methodology. |
| regional_hotel_poi_count | Regional Hotel Poi Count | overture_places | compiled_place_proxy_count | selected_lodging_category_points_in_catchment | See source metadata and methodology. |
| regional_school_poi_count | Regional School Poi Count | overture_places | compiled_place_proxy_count | selected_school_category_points_in_catchment | See source metadata and methodology. |
| regional_healthcare_poi_count | Regional Healthcare Poi Count | overture_places | compiled_place_proxy_count | selected_healthcare_category_points_in_catchment | See source metadata and methodology. |
| regional_park_poi_count | Regional Park Poi Count | overture_places | compiled_place_proxy_count | selected_park_category_points_in_catchment | See source metadata and methodology. |
| regional_library_poi_count | Regional Library Poi Count | overture_places | compiled_place_proxy_count | library_category_points_in_catchment | See source metadata and methodology. |
| pm25_indicator | Pm25 Indicator | air_quality_health | model_or_observation_as_published | latest_borough_published_value | See source metadata and methodology. |
| asthma_health_indicator | Asthma Health Indicator | air_quality_health | area_health_estimate | latest_borough_published_value | See source metadata and methodology. |
| cdc_places_current_asthma_pct | Cdc Places Current Asthma Pct | cdc_places_2025_tract | population_weighted_small_area_model_estimate | tract_overlap_and_acs_total_population_weighted_mean | See source metadata and methodology. |
| cdc_places_diagnosed_diabetes_pct | Cdc Places Diagnosed Diabetes Pct | cdc_places_2025_tract | population_weighted_small_area_model_estimate | tract_overlap_and_acs_total_population_weighted_mean | See source metadata and methodology. |
| cdc_places_obesity_pct | Cdc Places Obesity Pct | cdc_places_2025_tract | population_weighted_small_area_model_estimate | tract_overlap_and_acs_total_population_weighted_mean | See source metadata and methodology. |
| cdc_places_uninsured_pct | Cdc Places Uninsured Pct | cdc_places_2025_tract | population_weighted_small_area_model_estimate | tract_overlap_and_acs_total_population_weighted_mean | See source metadata and methodology. |
| cdc_places_food_insecurity_pct | Cdc Places Food Insecurity Pct | cdc_places_2025_tract | population_weighted_small_area_model_estimate | tract_overlap_and_acs_total_population_weighted_mean | See source metadata and methodology. |
| resident_index | Resident Index | derived | model_proxy | transparent_uncalibrated_proxy | See source metadata and methodology. |
| worker_index | Worker Index | derived | model_proxy | transparent_uncalibrated_proxy | See source metadata and methodology. |
| nightlife_index | Nightlife Index | derived | model_proxy | transparent_uncalibrated_proxy | See source metadata and methodology. |
| transit_hub_index | Transit Hub Index | derived | model_proxy | transparent_uncalibrated_proxy | See source metadata and methodology. |
| fema_special_flood_hazard_overlap_pct | Fema Special Flood Hazard Overlap Pct | fema_nfhl_special_flood_hazard_areas | projected_area_overlap_from_service_simplified_geometry | unioned_sfha_intersection_area_divided_by_catchment_area | See source metadata and methodology. |
| audience_type | Audience Type | derived | model_classification | highest_transparent_proxy_with_evidence_threshold | See source metadata and methodology. |
| demand_support_signal | Demand Support Signal | derived_citywide_benchmark | benchmark_classification | median_component_percentile_with_33_67_bands | See source metadata and methodology. |
| competition_pressure_signal | Competition Pressure Signal | derived_citywide_benchmark | benchmark_classification | median_component_percentile_with_33_67_bands | See source metadata and methodology. |
| market_opportunity_profile | Market Opportunity Profile | derived_citywide_benchmark | benchmark_classification | demand_support_and_competition_pressure_profile | See source metadata and methodology. |

Survey estimates retain the published estimate and margin of error. Derived survey ratios use conservative first-order error propagation. Area-level health, income, language, and ethnicity statistics do not describe a specific person, household, building, or address.
