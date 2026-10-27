from __future__ import annotations

import json
from typing import Any

import duckdb
import h3
from shapely.geometry import Polygon, mapping

from .config import taxonomy_config
from .db import calculation_version
from .entities.buildings import fetch_building_for_address
from .entities.parcels import fetch_parcel_for_address
from .geocoding.service import geocode_address
from .metrics.engine import (
    _acs_context,
    _decennial_context,
    attach_benchmark_percentiles,
    calculate_metrics,
    derive_market_signals,
    persist_metrics,
)
from .spatial.catchments import catchment_area_m2, fetch_walk_network, metric_buffer, walk_isochrone
from .util import json_dumps, new_id, utc_now


DEFAULT_CATCHMENTS = ["250m", "500m", "1km", "5min_walk", "10min_walk", "15min_walk"]
CATCHMENT_SPECS = {
    "250m": {"kind": "radial", "distance_m": 250.0},
    "500m": {"kind": "radial", "distance_m": 500.0},
    "1km": {"kind": "radial", "distance_m": 1000.0},
    "5min_walk": {"kind": "walk_network", "travel_minutes": 5.0, "distance_m": 400.0},
    "10min_walk": {"kind": "walk_network", "travel_minutes": 10.0, "distance_m": 800.0},
    "15min_walk": {"kind": "walk_network", "travel_minutes": 15.0, "distance_m": 1200.0},
}


def _site_snapshot_set(
    conn: duckdb.DuckDBPyConnection,
    *,
    address_record: dict[str, Any],
    parcel: dict[str, Any] | None,
    building: dict[str, Any] | None,
    metric_values: list[Any],
    network_snapshot_id: str | None,
) -> dict[str, list[str]]:
    referenced: dict[str, set[str]] = {}

    def add(source_id: Any, snapshot_id: Any) -> None:
        if source_id and snapshot_id:
            referenced.setdefault(str(source_id), set()).add(str(snapshot_id))

    for record in (address_record, parcel, building):
        if record:
            add(record.get("source_id"), record.get("snapshot_id"))
    if network_snapshot_id:
        add("osm_walk_network", network_snapshot_id)
    evidence_ids: set[str] = set()
    for metric in metric_values:
        add(metric.source_id, metric.snapshot_id)
        evidence_ids.update(str(value) for value in (metric.evidence_ids or []))
    ordered_evidence = sorted(evidence_ids)
    for start in range(0, len(ordered_evidence), 10_000):
        batch = ordered_evidence[start : start + 10_000]
        placeholders = ",".join("?" for _ in batch)
        if not batch:
            continue
        for source_id, snapshot_id in conn.execute(
            f"SELECT DISTINCT source_id, snapshot_id FROM standardized_record "
            f"WHERE entity_id IN ({placeholders}) AND snapshot_id IS NOT NULL",
            batch,
        ).fetchall():
            add(source_id, snapshot_id)
    county_geoid = address_record.get("county_geoid")
    if county_geoid:
        rows = conn.execute(
            """
            SELECT source_id, snapshot_id FROM region_member WHERE geography_id=?
            UNION ALL
            SELECT source_id, snapshot_id FROM region_definition WHERE region_code='CSA408'
            UNION ALL
            SELECT source_id, snapshot_id FROM geo_boundary
            WHERE source_id='tiger_county_2024'
              AND geography_type IN ('county_equivalent_2024','combined_statistical_area')
              AND geography_id IN (?, '408')
            """,
            [county_geoid, county_geoid],
        ).fetchall()
        for source_id, snapshot_id in rows:
            add(source_id, snapshot_id)
    return {
        source_id: sorted(snapshot_ids)
        for source_id, snapshot_ids in sorted(referenced.items())
    }


def _ensure_h3_cell(conn: duckdb.DuckDBPyConnection, longitude: float, latitude: float) -> str:
    cell = h3.latlng_to_cell(latitude, longitude, 10)
    exists = conn.execute("SELECT 1 FROM geo_cell WHERE cell_id=?", [cell]).fetchone()
    if not exists:
        boundary_latlon = h3.cell_to_boundary(cell)
        polygon = Polygon([(lon, lat) for lat, lon in boundary_latlon])
        conn.execute(
            """
            INSERT INTO geo_cell VALUES (?, 10, ?, ?, ?, 'derived_h3_grid', NULL, ?, NULL, NULL,
                                         'h3', ?, 'model_grid', 1.0, '0.2.0')
            """,
            [cell, polygon.wkt, longitude, latitude, utc_now(), cell],
        )
    return cell


def _analyze_site_impl(
    conn: duckdb.DuckDBPyConnection,
    *,
    address: str,
    business_type: str = "full_service_restaurant",
    catchments: list[str] | None = None,
) -> dict[str, Any]:
    requested = list(dict.fromkeys(catchments or DEFAULT_CATCHMENTS))
    synthetic = conn.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='synthetic_demo_guard'").fetchone()[0] > 0
    if synthetic and (any(name.endswith('_walk') for name in requested) or not conn.execute(
        "SELECT 1 FROM address WHERE original_input=? AND geocoder='synthetic_demo'", [address]
    ).fetchone()):
        raise ValueError("Synthetic demo supports only the two seeded addresses and radial catchments; no external queries are allowed")
    unknown = sorted(set(requested) - set(CATCHMENT_SPECS))
    if unknown:
        raise ValueError(f"Unknown catchments: {', '.join(unknown)}")
    if business_type not in taxonomy_config().get("business_types", {}):
        raise ValueError(f"Unknown business type: {business_type}")
    address_record = geocode_address(conn, address)
    is_nyc = bool(address_record.get("borough"))
    warnings: list[str] = []
    if synthetic:
        warnings.append("SYNTHETIC DEMO: all records and differences are fictional, not real trade-area conclusions.")
    missing: list[str] = []
    parcel = fetch_parcel_for_address(conn, address_record)
    if parcel:
        address_record["bbl"] = parcel["bbl"]
    building = fetch_building_for_address(conn, address_record, parcel)
    if not is_nyc:
        if parcel is None:
            warnings.append(
                "五区外未提供统一权威地块层；系统不会把 Overture 建筑轮廓冒充 parcel。"
            )
            missing.append("parcel:regional_authoritative_source_unavailable")
        if building and building.get("source_id") == "overture_buildings":
            warnings.append("五区外建筑轮廓来自 Overture 编译层（ODbL），不是当地权威地块或占用记录。")
        warnings.append("纽约五区基准百分位不适用于跨州站点，因此区域站点只显示原始指标，不附加五区百分位或机会画像。")
        missing.append("benchmark:greater_nyc_calibration_not_available")
    longitude = float(address_record["longitude"])
    latitude = float(address_record["latitude"])
    h3_cell = _ensure_h3_cell(conn, longitude, latitude)
    calculation_id = calculation_version(conn)
    site_id = new_id("site")
    now = utc_now()
    snapshot_set: dict[str, list[str]] = {}
    conn.execute(
        """
        INSERT INTO site_query(
            site_id, original_input, standardized_address, business_type, address_id,
            longitude, latitude, parcel_id, building_id, requested_catchments_json, snapshot_set_json,
            calculation_version_id, warnings_json, missing_data_json, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?::JSON, ?::JSON, ?, '[]'::JSON, '[]'::JSON, 'RUNNING', ?)
        """,
        [
            site_id,
            address,
            address_record.get("standardized_address"),
            business_type,
            address_record["address_id"],
            longitude,
            latitude,
            parcel.get("parcel_id") if parcel else None,
            building.get("building_id") if building else None,
            json_dumps(requested),
            json_dumps(snapshot_set),
            calculation_id,
            now,
        ],
    )
    network = None
    if any(CATCHMENT_SPECS[item]["kind"] == "walk_network" for item in requested):
        try:
            network = fetch_walk_network(
                conn,
                site_id=site_id,
                longitude=longitude,
                latitude=latitude,
                radius_m=1750,
            )
        except Exception as exc:
            conn.execute(
                """
                UPDATE site_query SET status='FAILED', completed_at=?,
                       warnings_json=?::JSON, missing_data_json='[]'::JSON
                WHERE site_id=?
                """,
                [utc_now(), json_dumps([f"walking_network_error:{type(exc).__name__}"]), site_id],
            )
            raise
    context = _acs_context(conn)
    decennial_context = _decennial_context(conn)
    results: dict[str, Any] = {}
    all_metric_values: list[Any] = []
    for catchment_type in requested:
        spec = CATCHMENT_SPECS[catchment_type]
        if spec["kind"] == "radial":
            geometry = metric_buffer(longitude, latitude, spec["distance_m"])
            network_source = None
            network_snapshot_id = None
        else:
            if network is None:
                raise RuntimeError("Network catchment requested without a network extract")
            geometry = walk_isochrone(
                network,
                longitude=longitude,
                latitude=latitude,
                cutoff_m=spec["distance_m"],
            )
            network_source = "OpenStreetMap walking network"
            network_snapshot_id = network.snapshot_id
        catchment_id = new_id("catchment")
        area_m2 = catchment_area_m2(geometry)
        conn.execute(
            """
            INSERT INTO catchment VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::JSON)
            """,
            [
                catchment_id,
                site_id,
                catchment_type,
                spec.get("distance_m"),
                spec.get("travel_minutes"),
                network_source,
                network_snapshot_id,
                geometry.wkt,
                area_m2,
                "0.2.0",
                now,
                "[]",
            ],
        )
        metric_values = calculate_metrics(
            conn,
            geometry,
            borough=address_record.get("borough"),
            business_type=business_type,
            acs_context=context,
            decennial_context=decennial_context,
        )
        for metric in metric_values:
            if metric.source_id and metric.source_id != "derived" and not metric.snapshot_id:
                metric.value = None
                metric.text_value = None
                metric.confidence = None
                metric.warnings = [*(metric.warnings or []), "No completed source snapshot; absent records are not observed zero."]
            if synthetic:
                metric.warnings = [*(metric.warnings or []), "SYNTHETIC_DEMO"]
                if metric.snapshot_id:
                    metric.source_year = "SYNTHETIC 2026"
                    metric.measure_type = "synthetic_fixture"
                    metric.confidence = None
        if is_nyc:
            attach_benchmark_percentiles(conn, catchment_type, metric_values)
        metric_values.extend(derive_market_signals(conn, catchment_type, metric_values))
        all_metric_values.extend(metric_values)
        persist_metrics(
            conn,
            site_id=site_id,
            catchment_id=catchment_id,
            calculation_version_id=calculation_id,
            metrics=metric_values,
        )
        metrics_payload = {
            metric.metric_id: {
                "value": metric.value,
                "text_value": metric.text_value,
                "unit": metric.unit,
                "percentile": metric.percentile,
                "source_id": metric.source_id,
                "snapshot_id": metric.snapshot_id,
                "source_year": metric.source_year,
                "measure_type": metric.measure_type,
                "confidence": metric.confidence,
                "margin_of_error": metric.margin_of_error,
                "geography_type": metric.geography_type,
                "geography_id": metric.geography_id,
                "evidence_count": len(metric.evidence_ids or []),
                "warnings": metric.warnings or [],
            }
            for metric in metric_values
        }
        for metric_id, payload in metrics_payload.items():
            if payload["value"] is None and payload["text_value"] is None:
                missing.append(f"{catchment_type}:{metric_id}")
        results[catchment_type] = {
            "catchment_id": catchment_id,
            "kind": spec["kind"],
            "distance_m": spec.get("distance_m"),
            "travel_minutes": spec.get("travel_minutes"),
            "network_source": network_source,
            "network_snapshot_id": network_snapshot_id,
            "area_m2": area_m2,
            "geometry": mapping(geometry),
            "metrics": metrics_payload,
        }
    snapshot_set = _site_snapshot_set(
        conn,
        address_record=address_record,
        parcel=parcel,
        building=building,
        metric_values=all_metric_values,
        network_snapshot_id=network.snapshot_id if network is not None else None,
    )
    result = {
        "site_id": site_id,
        "data_mode": "SYNTHETIC_DEMO" if synthetic else "LOCAL_SOURCE_DATA",
        "input_address": address,
        "standardized_address": address_record.get("standardized_address"),
        "coordinates": {"longitude": longitude, "latitude": latitude},
        "borough": address_record.get("borough"),
        "state_fips": address_record.get("state_fips"),
        "state_abbr": address_record.get("state_abbr"),
        "county_geoid": address_record.get("county_geoid"),
        "county_name": address_record.get("county_name"),
        "region_code": address_record.get("region_code"),
        "region_name": address_record.get("region_name"),
        "subregion": address_record.get("subregion"),
        "census_tract": address_record.get("census_tract"),
        "census_block": address_record.get("census_block"),
        "h3_resolution_10": h3_cell,
        "parcel": parcel,
        "building": building,
        "business_type": business_type,
        "calculation_version_id": calculation_id,
        "snapshot_set": snapshot_set,
        "catchments": results,
        "warnings": sorted(set(warnings)),
        "missing_data": sorted(set(missing)),
    }
    conn.execute(
        """
        UPDATE site_query SET result_json=?::JSON, warnings_json=?::JSON,
               missing_data_json=?::JSON, snapshot_set_json=?::JSON,
               status='SUCCEEDED', completed_at=? WHERE site_id=?
        """,
        [
            json_dumps(result),
            json_dumps(result["warnings"]),
            json_dumps(result["missing_data"]),
            json_dumps(result["snapshot_set"]),
            utc_now(),
            site_id,
        ],
    )
    return result


def analyze_site(
    conn: duckdb.DuckDBPyConnection,
    *,
    address: str,
    business_type: str = "full_service_restaurant",
    catchments: list[str] | None = None,
) -> dict[str, Any]:
    """Run one analysis and close any created site row as FAILED on exceptions."""
    started_at = utc_now()
    try:
        return _analyze_site_impl(
            conn,
            address=address,
            business_type=business_type,
            catchments=catchments,
        )
    except Exception as exc:
        conn.execute(
            """
            UPDATE site_query
            SET status='FAILED', completed_at=?,
                warnings_json=?::JSON
            WHERE status='RUNNING'
              AND original_input=?
              AND business_type=?
              AND created_at>=?
            """,
            [
                utc_now(),
                json_dumps([f"analysis_error:{type(exc).__name__}:{str(exc)[:500]}"]),
                address,
                business_type,
                started_at,
            ],
        )
        raise


def get_site(conn: duckdb.DuckDBPyConnection, site_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT result_json FROM site_query WHERE site_id=?", [site_id]).fetchone()
    if not row or row[0] is None:
        return None
    return json.loads(row[0]) if isinstance(row[0], str) else row[0]
