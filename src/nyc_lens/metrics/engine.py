from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from shapely import contains_xy, from_wkt, union_all
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform
from shapely.strtree import STRtree

from ..config import taxonomy_config
from ..spatial.catchments import metric_transform_for_geometry
from ..util import json_dumps, new_id, utc_now


@dataclass
class MetricValue:
    metric_id: str
    value: float | None = None
    text_value: str | None = None
    unit: str | None = None
    percentile: float | None = None
    source_id: str | None = None
    snapshot_id: str | None = None
    source_year: str | None = None
    measure_type: str = "unknown"
    confidence: float | None = None
    margin_of_error: float | None = None
    geography_type: str | None = None
    geography_id: str | None = None
    evidence_ids: list[str] | None = None
    warnings: list[str] | None = None


def attach_benchmark_percentiles(
    conn: duckdb.DuckDBPyConnection,
    catchment_type: str,
    metrics: list[MetricValue],
) -> None:
    if catchment_type not in {"250m", "500m", "1km"}:
        return
    for metric in metrics:
        if metric.value is None:
            continue
        row = conn.execute(
            """
            SELECT count(*) FILTER (WHERE value < ?) AS below,
                   count(*) FILTER (WHERE value = ?) AS equal,
                   count(*) AS total
            FROM benchmark_observation
            WHERE benchmark_scope='nyc_citywide_h3_r10_stratified'
              AND catchment_type=? AND metric_id=?
            """,
            [metric.value, metric.value, catchment_type, metric.metric_id],
        ).fetchone()
        below, equal, total = row
        if total:
            rank = below + ((equal + 1) / 2 if equal else 0)
            metric.percentile = float(min(100.0, max(0.0, rank / total * 100.0)))


def derive_market_signals(
    conn: duckdb.DuckDBPyConnection,
    catchment_type: str,
    metrics: list[MetricValue],
) -> list[MetricValue]:
    by_id = {metric.metric_id: metric for metric in metrics}

    def component_percentiles(metric_ids: list[str]) -> dict[str, float]:
        if catchment_type not in {"250m", "500m", "1km"}:
            return {}
        return {
            metric_id: float(by_id[metric_id].percentile)
            for metric_id in metric_ids
            if metric_id in by_id and by_id[metric_id].percentile is not None
        }

    def signal_evidence(metric_ids: list[str]) -> list[str]:
        return sorted(
            {
                evidence_id
                for metric_id in metric_ids
                if metric_id in by_id
                for evidence_id in (by_id[metric_id].evidence_ids or [])
            }
        )

    demand = component_percentiles(
        [
            "resident_population",
            "daytime_jobs",
            "demand_complement_count",
            "hotel_count",
            "legal_short_term_rental_count",
            "culture_and_library_count",
            "subway_entries_2025",
            "bus_stop_count",
            "citibike_station_count",
            "tlc_pickup_trips_average_daily_area_weighted",
            "tlc_dropoff_trips_average_daily_area_weighted",
        ]
    )
    competition = component_percentiles(
        [
            "direct_competitor_count",
            "competitors_per_1000_residents",
            "competitors_per_1000_jobs",
            "substitute_count",
        ]
    )
    market_ready = len(demand) >= 2 and len(competition) >= 2
    risk = component_percentiles(
        [
            "active_dob_violations_per_100_parcels",
            "active_ecb_violations_per_100_parcels",
        ]
    )
    environmental_ids = [
        "fema_special_flood_hazard_overlap_pct",
        "hurricane_evacuation_overlap_pct",
        "future_2050_floodplain_overlap_pct",
        "sea_level_2100_floodplain_overlap_pct",
    ]
    environmental = {
        metric_id: float(by_id[metric_id].value)
        for metric_id in environmental_ids
        if metric_id in by_id and by_id[metric_id].value is not None
    }
    if not market_ready and not risk and not environmental:
        return []

    def band(value: float) -> str:
        if value >= 67:
            return "高"
        if value <= 33:
            return "低"
        return "中"

    benchmark = conn.execute(
        """
        WITH latest_benchmark AS (
            SELECT benchmark_run_id, snapshot_id
            FROM benchmark_quantile
            WHERE benchmark_scope='nyc_citywide_h3_r10_stratified'
              AND catchment_type=?
            ORDER BY created_at DESC
            LIMIT 1
        )
        SELECT b.snapshot_id, count(DISTINCT o.sample_cell_id)
        FROM latest_benchmark b
        LEFT JOIN benchmark_observation o
          ON o.benchmark_run_id=b.benchmark_run_id
         AND o.benchmark_scope='nyc_citywide_h3_r10_stratified'
         AND o.catchment_type=?
        GROUP BY b.snapshot_id
        """,
        [catchment_type, catchment_type],
    ).fetchone()
    benchmark_snapshot_id = benchmark[0] if benchmark else None
    sample_size = int(benchmark[1]) if benchmark else 0
    common = {
        "source_id": "derived_citywide_benchmark",
        "snapshot_id": benchmark_snapshot_id,
        "source_year": "current local snapshot set",
        "measure_type": "benchmark_classification",
        "confidence": 0.7 if sample_size >= 250 else 0.6,
        "geography_type": "borough-stratified H3 center reference distribution",
    }
    signals: list[MetricValue] = []
    if market_ready:
        demand_median = float(np.median(list(demand.values())))
        competition_median = float(np.median(list(competition.values())))
        demand_band = band(demand_median)
        competition_band = band(competition_median)
        profile = f"需求支撑{demand_band}、竞争压力{competition_band}"
        signals.extend(
            [
                MetricValue(
                    "demand_support_signal",
                    text_value=f"{demand_band}（组成指标中位百分位 {demand_median:.0f}）",
                    evidence_ids=signal_evidence(list(demand)),
                    warnings=[f"组成指标及百分位：{json_dumps(demand)}", "分类阈值为第33/67百分位，不是需求人数或综合分数。"],
                    **common,
                ),
                MetricValue(
                    "competition_pressure_signal",
                    text_value=f"{competition_band}（组成指标中位百分位 {competition_median:.0f}）",
                    evidence_ids=signal_evidence(list(competition)),
                    warnings=[f"组成指标及百分位：{json_dumps(competition)}", "竞争压力高不等于不可进入，低也不等于存在需求。"],
                    **common,
                ),
                MetricValue(
                    "market_opportunity_profile",
                    text_value=profile,
                    evidence_ids=signal_evidence([*demand, *competition]),
                    warnings=[
                        f"需求组成：{json_dumps(demand)}",
                        f"竞争组成：{json_dumps(competition)}",
                        "这是透明的双轴机会画像，不是校准后的综合选址得分。",
                    ],
                    **common,
                ),
            ]
        )
    if risk:
        risk_median = float(np.median(list(risk.values())))
        risk_values = {
            metric_id: by_id[metric_id].value
            for metric_id in risk
            if metric_id in by_id
        }
        if risk_values and all(value == 0 for value in risk_values.values()):
            risk_text = "低（未关联到现行违规记录）"
        else:
            risk_text = f"{band(risk_median)}（组成指标中位百分位 {risk_median:.0f}）"
        signals.append(
            MetricValue(
                "regulatory_risk_signal",
                text_value=risk_text,
                evidence_ids=signal_evidence(list(risk)),
                warnings=[
                    f"组成指标值：{json_dumps(risk_values)}",
                    f"组成指标百分位：{json_dumps(risk)}",
                    "这是基于公开行政记录密度的筛查信号，不是安全结论、法律意见或合规认定；没有记录也不证明没有风险。",
                ],
                **common,
            )
        )
    if environmental:
        maximum_metric_id, maximum_overlap = max(environmental.items(), key=lambda pair: pair[1])
        if maximum_overlap <= 0:
            exposure_text = "未识别到覆盖重叠"
        elif maximum_overlap < 10:
            exposure_text = f"局部重叠（最大覆盖 {maximum_overlap:.1f}%）"
        elif maximum_overlap < 50:
            exposure_text = f"显著重叠（最大覆盖 {maximum_overlap:.1f}%）"
        else:
            exposure_text = f"大范围重叠（最大覆盖 {maximum_overlap:.1f}%）"
        evidence_ids = sorted(
            {
                evidence_id
                for metric_id in environmental
                for evidence_id in (by_id[metric_id].evidence_ids or [])
            }
        )
        signals.append(
            MetricValue(
                "environmental_exposure_profile",
                text_value=exposure_text,
                source_id="derived",
                source_year="latest local hurricane and planning flood layers",
                measure_type="transparent_threshold_classification",
                confidence=min(
                    by_id[metric_id].confidence or 0.5 for metric_id in environmental
                ),
                geography_type="catchment overlay",
                evidence_ids=evidence_ids,
                warnings=[
                    f"组成图层覆盖率：{json_dumps(environmental)}",
                    f"最大覆盖来自：{maximum_metric_id}",
                    "分档仅描述当前数据图层与商圈范围的几何重叠；不是实时灾害预报、FEMA保险判定、工程评估、法律意见或地块尽调。",
                ],
            )
        )
    return signals


def _latest_snapshot(conn: duckdb.DuckDBPyConnection, source_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT d.snapshot_id FROM dataset_snapshot d
        WHERE d.source_id=? AND d.status='SUCCEEDED'
          AND EXISTS (
              SELECT 1 FROM ingestion_run r
              WHERE r.source_id=d.source_id
                AND r.status IN ('SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS')
                AND r.completed_at >= d.retrieved_at
          )
        ORDER BY d.retrieved_at DESC LIMIT 1
        """,
        [source_id],
    ).fetchone()
    return row[0] if row else None


def _latest_vintage(conn: duckdb.DuckDBPyConnection, source_id: str) -> str | None:
    snapshot_id = _latest_snapshot(conn, source_id)
    if snapshot_id is None:
        return None
    row = conn.execute(
        "SELECT vintage FROM dataset_snapshot WHERE snapshot_id=?", [snapshot_id]
    ).fetchone()
    return row[0] if row else None


def _latest_census_variable_snapshot(
    conn: duckdb.DuckDBPyConnection,
    source_id: str,
    variable: str,
) -> str | None:
    row = conn.execute(
        """
        SELECT arg_max(snapshot_id, retrieved_at)
        FROM census_estimate
        WHERE source_id=? AND variable=?
        """,
        [source_id, variable],
    ).fetchone()
    return row[0] if row and row[0] else None


def _point_frame(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    id_column: str,
    geometry: BaseGeometry,
    *,
    where: str = "TRUE",
    params: list[Any] | None = None,
    columns: str = "*",
) -> pd.DataFrame:
    west, south, east, north = geometry.bounds
    query = (
        f"SELECT {columns} FROM {table} WHERE {where} "
        "AND longitude BETWEEN ? AND ? AND latitude BETWEEN ? AND ?"
    )
    frame = conn.execute(query, [*(params or []), west, east, south, north]).fetchdf()
    if frame.empty:
        return frame
    mask = contains_xy(geometry, frame["longitude"].to_numpy(float), frame["latitude"].to_numpy(float))
    return frame.loc[mask].copy()


def _polygon_frame(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    geometry: BaseGeometry,
    *,
    where: str = "TRUE",
    params: list[Any] | None = None,
    columns: str = "*",
) -> tuple[pd.DataFrame, list[BaseGeometry]]:
    west, south, east, north = geometry.bounds
    frame = conn.execute(
        f"""
        SELECT {columns} FROM {table}
        WHERE {where}
          AND min_longitude <= ? AND max_longitude >= ?
          AND min_latitude <= ? AND max_latitude >= ?
        """,
        [*(params or []), east, west, north, south],
    ).fetchdf()
    if frame.empty:
        return frame, []
    geometries = list(from_wkt(frame["geom_wkt"].to_numpy()))
    keep = [index for index, candidate in enumerate(geometries) if geometry.intersects(candidate)]
    if not keep:
        return frame.iloc[0:0].copy(), []
    return frame.iloc[keep].reset_index(drop=True), [geometries[index] for index in keep]


def _parcel_frame(
    conn: duckdb.DuckDBPyConnection,
    geometry: BaseGeometry,
) -> pd.DataFrame:
    frame, geometries = _polygon_frame(
        conn,
        "parcel",
        geometry,
        where="source_id='mappluto_26v2'",
        columns=(
            "parcel_id,bbl,lot_area_sqft,building_area_sqft,residential_area_sqft,"
            "office_area_sqft,retail_area_sqft,units_residential,units_total,"
            "min_longitude,min_latitude,max_longitude,max_latitude,geom_wkt,snapshot_id"
        ),
    )
    if frame.empty:
        frame["intersection_fraction"] = pd.Series(dtype=float)
        return frame
    fractions: list[float] = []
    for parcel_geometry in geometries:
        if parcel_geometry.area <= 0:
            fractions.append(0.0)
        elif geometry.covers(parcel_geometry):
            fractions.append(1.0)
        else:
            fractions.append(min(1.0, max(0.0, geometry.intersection(parcel_geometry).area / parcel_geometry.area)))
    frame["intersection_fraction"] = fractions
    return frame.loc[frame["intersection_fraction"] > 0].copy()


def _records_for_bbls(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    bbls: list[str],
    *,
    where: str = "TRUE",
    params: list[Any] | None = None,
    columns: str = "r.*",
) -> pd.DataFrame:
    if not bbls:
        return pd.DataFrame()
    keys = pd.DataFrame({"bbl": sorted(set(bbls))})
    conn.register("_metric_bbls", keys)
    try:
        return conn.execute(
            f"""
            SELECT {columns}
            FROM {table} r
            JOIN _metric_bbls b ON r.geography_id=b.bbl
            WHERE {where}
            """,
            params or [],
        ).fetchdf()
    finally:
        conn.unregister("_metric_bbls")


def _weighted_sum(frame: pd.DataFrame, column: str) -> float:
    if frame.empty:
        return 0.0
    values = pd.to_numeric(frame[column], errors="coerce").fillna(0.0).clip(lower=0.0)
    return float((values * frame["intersection_fraction"]).sum())


def _case_key(row: pd.Series, preferred_fields: tuple[str, ...]) -> str:
    attributes = row.get("attributes_json")
    if isinstance(attributes, str):
        try:
            attributes = json.loads(attributes)
        except json.JSONDecodeError:
            attributes = {}
    if not isinstance(attributes, dict):
        attributes = {}
    for field in preferred_fields:
        value = attributes.get(field)
        if value not in (None, ""):
            return f"{row.get('geography_id')}|{str(value).strip().upper()}"
    return str(row.get("regulatory_record_id"))


def _acs_context(conn: duckdb.DuckDBPyConnection) -> tuple[pd.DataFrame, list[BaseGeometry], STRtree] | None:
    boundaries = conn.execute(
        """
        SELECT geography_id, geom_wkt, area_m2, snapshot_id
        FROM geo_boundary
        WHERE geography_type='block_group' AND source_id='tiger_bg_2024'
        """
    ).fetchdf()
    if boundaries.empty:
        return None
    geometries = list(from_wkt(boundaries["geom_wkt"].to_numpy()))
    return boundaries, geometries, STRtree(geometries)


def _decennial_context(conn: duckdb.DuckDBPyConnection) -> tuple[pd.DataFrame, list[BaseGeometry], STRtree] | None:
    boundaries = conn.execute(
        """
        SELECT geography_id, geom_wkt, area_m2, snapshot_id
        FROM geo_boundary
        WHERE geography_type='census_block' AND source_id='nyc_census_blocks_2020'
        """
    ).fetchdf()
    if boundaries.empty:
        return None
    geometries = list(from_wkt(boundaries["geom_wkt"].to_numpy()))
    return boundaries, geometries, STRtree(geometries)


def _acs_weights(
    geometry: BaseGeometry,
    context: tuple[pd.DataFrame, list[BaseGeometry], STRtree],
) -> pd.DataFrame:
    boundaries, geometries, tree = context
    candidate_indices = tree.query(geometry, predicate="intersects")
    metric_transform = metric_transform_for_geometry(geometry)
    target_metric = transform(metric_transform, geometry)
    rows: list[dict[str, Any]] = []
    for index in candidate_indices:
        boundary = geometries[int(index)]
        boundary_metric = transform(metric_transform, boundary)
        if boundary_metric.area <= 0:
            continue
        intersection_area = target_metric.intersection(boundary_metric).area
        if intersection_area <= 0:
            continue
        rows.append(
            {
                "geography_id": boundaries.iloc[int(index)]["geography_id"],
                "fraction": min(1.0, intersection_area / boundary_metric.area),
                "intersection_area_m2": intersection_area,
                "boundary_area_m2": boundary_metric.area,
            }
        )
    return pd.DataFrame(rows)


def _acs_aggregate(
    conn: duckdb.DuckDBPyConnection,
    weights: pd.DataFrame,
    variable: str,
    *,
    median: bool = False,
) -> tuple[float | None, float | None, list[str], float | None]:
    if weights.empty:
        return None, None, [], None
    geoids = weights["geography_id"].tolist()
    placeholders = ",".join("?" for _ in geoids)
    estimates = conn.execute(
        f"""
        SELECT census_estimate_id, geography_id, geography_type,
               estimate, margin_of_error, confidence
        FROM census_estimate
        WHERE variable=? AND geography_type='block_group'
          AND geography_id IN ({placeholders})
        """,
        [variable, *geoids],
    ).fetchdf()
    working_weights = weights[["geography_id", "fraction"]].copy()
    if estimates.empty:
        # Some ACS detailed tables are only published at tract level. Derive
        # tract overlap from the already-computed block-group intersections.
        tract_ids = sorted({str(geoid)[:11] for geoid in geoids})
        tract_placeholders = ",".join("?" for _ in tract_ids)
        tract_areas = conn.execute(
            f"""
            SELECT substr(geography_id, 1, 11) AS geography_id, sum(area_m2) AS tract_area_m2
            FROM geo_boundary
            WHERE geography_type='block_group'
              AND substr(geography_id, 1, 11) IN ({tract_placeholders})
            GROUP BY 1
            """,
            tract_ids,
        ).fetchdf()
        tract_weights = weights.assign(
            geography_id=weights["geography_id"].astype(str).str[:11]
        ).groupby("geography_id", as_index=False)["intersection_area_m2"].sum()
        tract_weights = tract_weights.merge(tract_areas, on="geography_id", how="left")
        tract_weights["fraction"] = (
            tract_weights["intersection_area_m2"] / tract_weights["tract_area_m2"]
        ).clip(upper=1.0)
        working_weights = tract_weights[["geography_id", "fraction"]]
        estimates = conn.execute(
            f"""
            SELECT census_estimate_id, geography_id, geography_type,
                   estimate, margin_of_error, confidence
            FROM census_estimate
            WHERE variable=? AND geography_type='tract'
              AND geography_id IN ({tract_placeholders})
            """,
            [variable, *tract_ids],
        ).fetchdf()
        if estimates.empty:
            return None, None, [], None
    values = estimates.merge(working_weights, on="geography_id", how="inner")
    values = values.loc[values["estimate"].notna()].copy()
    if values.empty:
        return None, None, [], None
    if median:
        if values["geography_type"].eq("tract").all():
            population = conn.execute(
                f"""
                SELECT substr(geography_id,1,11) AS geography_id, sum(estimate) AS population
                FROM census_estimate
                WHERE variable='B01003_001' AND geography_type='block_group'
                  AND substr(geography_id,1,11) IN ({tract_placeholders})
                GROUP BY 1
                """,
                tract_ids,
            ).fetchdf()
        else:
            population = conn.execute(
                f"SELECT geography_id, estimate AS population FROM census_estimate WHERE variable='B01003_001' AND geography_id IN ({placeholders})",
                geoids,
            ).fetchdf()
        values = values.merge(population, on="geography_id", how="left")
        values["weight"] = values["population"].fillna(0).clip(lower=0) * values["fraction"]
        if values["weight"].sum() <= 0:
            values["weight"] = values["fraction"]
        value = float(np.average(values["estimate"], weights=values["weight"]))
        normalized = values["weight"] / values["weight"].sum()
        moe = float(np.sqrt(np.nansum((values["margin_of_error"].fillna(0) * normalized) ** 2)))
    else:
        value = float((values["estimate"] * values["fraction"]).sum())
        moe = float(np.sqrt(np.nansum((values["margin_of_error"].fillna(0) * values["fraction"]) ** 2)))
    confidence = float(np.average(values["confidence"].fillna(0.5), weights=values["fraction"]))
    return value, moe, values["census_estimate_id"].astype(str).tolist(), confidence


def _tract_health_aggregate(
    conn: duckdb.DuckDBPyConnection,
    weights: pd.DataFrame,
    indicator_id: str,
    *,
    source_id: str = "cdc_places_2025_tract",
) -> tuple[float | None, float | None, list[str], float | None]:
    if weights.empty:
        return None, None, [], None
    tract_overlap = (
        weights.assign(geography_id=weights["geography_id"].astype(str).str[:11])
        .groupby("geography_id", as_index=False)["intersection_area_m2"]
        .sum()
    )
    tract_ids = tract_overlap["geography_id"].astype(str).tolist()
    placeholders = ",".join("?" for _ in tract_ids)
    tract_areas = conn.execute(
        f"""
        SELECT geography_id, area_m2
        FROM geo_boundary
        WHERE source_id='tiger_tract_2024'
          AND geography_type='tract_2024'
          AND geography_id IN ({placeholders})
        """,
        tract_ids,
    ).fetchdf()
    if tract_areas.empty:
        return None, None, [], None
    tract_overlap = tract_overlap.merge(tract_areas, on="geography_id", how="inner")
    tract_overlap["fraction"] = (
        tract_overlap["intersection_area_m2"] / tract_overlap["area_m2"]
    ).clip(lower=0.0, upper=1.0)
    health = conn.execute(
        f"""
        SELECT health_indicator_id, geography_id, value, margin_of_error, confidence
        FROM health_indicator
        WHERE source_id=?
          AND indicator_id=?
          AND geography_id IN ({placeholders})
        """,
        [source_id, indicator_id, *tract_ids],
    ).fetchdf()
    if health.empty:
        return None, None, [], None
    population = conn.execute(
        f"""
        SELECT census_estimate_id, geography_id, estimate AS population
        FROM census_estimate
        WHERE source_id='acs_2024_5yr'
          AND variable='B01003_001'
          AND geography_type='tract'
          AND geography_id IN ({placeholders})
        """,
        tract_ids,
    ).fetchdf()
    values = health.merge(tract_overlap[["geography_id", "fraction"]], on="geography_id")
    values = values.merge(population, on="geography_id", how="left")
    values = values.loc[values["value"].notna() & (values["fraction"] > 0)].copy()
    if values.empty:
        return None, None, [], None
    values["weight"] = values["population"].fillna(0).clip(lower=0) * values["fraction"]
    if values["weight"].sum() <= 0:
        values["weight"] = values["fraction"]
    normalized = values["weight"] / values["weight"].sum()
    value = float(np.average(values["value"].astype(float), weights=values["weight"]))
    margin = float(
        np.sqrt(
            np.nansum(
                (pd.to_numeric(values["margin_of_error"], errors="coerce").fillna(0) * normalized)
                ** 2
            )
        )
    )
    confidence = float(
        np.average(values["confidence"].fillna(0.5), weights=values["weight"])
    )
    evidence = values["health_indicator_id"].astype(str).tolist()
    evidence.extend(
        values["census_estimate_id"].dropna().astype(str).tolist()
    )
    return value, margin, evidence, confidence


def _decennial_aggregate(
    conn: duckdb.DuckDBPyConnection,
    weights: pd.DataFrame,
    variable: str,
) -> tuple[float | None, list[str]]:
    if weights.empty:
        return None, []
    geoids = weights["geography_id"].astype(str).tolist()
    placeholders = ",".join("?" for _ in geoids)
    estimates = conn.execute(
        f"""
        SELECT census_estimate_id, geography_id, estimate
        FROM census_estimate
        WHERE source_id='census_decennial_2020_pl'
          AND geography_type='census_block' AND variable=?
          AND geography_id IN ({placeholders})
        """,
        [variable, *geoids],
    ).fetchdf()
    if estimates.empty:
        return None, []
    values = estimates.merge(weights[["geography_id", "fraction"]], on="geography_id", how="inner")
    values = values.loc[values["estimate"].notna()].copy()
    if values.empty:
        return None, []
    return (
        float((values["estimate"] * values["fraction"]).sum()),
        values["census_estimate_id"].astype(str).tolist(),
    )


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _sum_age_variables(
    conn: duckdb.DuckDBPyConnection,
    weights: pd.DataFrame,
    lines: list[int],
) -> tuple[float | None, float | None, list[str], float | None]:
    results = [_acs_aggregate(conn, weights, f"B01001_{line:03d}") for line in lines]
    usable = [result for result in results if result[0] is not None]
    if not usable:
        return None, None, [], None
    value = sum(result[0] or 0 for result in usable)
    moe = math.sqrt(sum((result[1] or 0) ** 2 for result in usable))
    evidence = [evidence for result in usable for evidence in result[2]]
    confidence = float(np.mean([result[3] for result in usable if result[3] is not None]))
    return value, moe, evidence, confidence


def calculate_metrics(
    conn: duckdb.DuckDBPyConnection,
    geometry: BaseGeometry,
    *,
    borough: str | None,
    business_type: str,
    acs_context: tuple[pd.DataFrame, list[BaseGeometry], STRtree] | None = None,
    decennial_context: tuple[pd.DataFrame, list[BaseGeometry], STRtree] | None = None,
) -> list[MetricValue]:
    metrics: list[MetricValue] = []
    metric_transform = metric_transform_for_geometry(geometry)
    catchment_area_m2 = float(transform(metric_transform, geometry).area)
    weights = _acs_weights(geometry, acs_context) if acs_context else pd.DataFrame()
    acs_snapshot = _latest_census_variable_snapshot(
        conn, "acs_2024_5yr", "B01003_001"
    )

    population, population_moe, population_evidence, population_confidence = _acs_aggregate(
        conn, weights, "B01003_001"
    )
    metrics.append(
        MetricValue(
            "resident_population",
            population,
            unit="people",
            source_id="acs_2024_5yr",
            snapshot_id=acs_snapshot,
            source_year="2020-2024",
            measure_type="survey_estimate",
            confidence=population_confidence,
            margin_of_error=population_moe,
            geography_type="area_weighted_block_groups",
            evidence_ids=population_evidence,
            warnings=["Area-weighted estimate; ACS block-group statistics do not describe an individual address."],
        )
    )
    catchment_area_sq_mile = catchment_area_m2 / 2_589_988.110336
    metrics.append(
        MetricValue(
            "resident_population_density_per_sq_mile",
            (
                population / catchment_area_sq_mile
                if population is not None and catchment_area_sq_mile > 0
                else None
            ),
            unit="people_per_square_mile",
            source_id="acs_2024_5yr",
            snapshot_id=acs_snapshot,
            source_year="2020-2024",
            measure_type="derived_area_weighted_survey_density",
            confidence=population_confidence,
            margin_of_error=(
                population_moe / catchment_area_sq_mile
                if population_moe is not None and catchment_area_sq_mile > 0
                else None
            ),
            geography_type="area_weighted_block_groups_to_projected_catchment_area",
            evidence_ids=population_evidence,
            warnings=[
                "ACS population is area-weighted to the catchment and divided by projected catchment area; it is not parcel occupancy or an individual attribute."
            ],
        )
    )
    decennial_weights = _acs_weights(geometry, decennial_context) if decennial_context else pd.DataFrame()
    decennial_population, decennial_evidence = _decennial_aggregate(
        conn, decennial_weights, "P0010001"
    )
    metrics.append(
        MetricValue(
            "decennial_population_2020",
            decennial_population,
            unit="people",
            source_id="census_decennial_2020_pl",
            snapshot_id=_latest_snapshot(conn, "census_decennial_2020_pl"),
            source_year="2020-04-01",
            measure_type="area_weighted_decennial_enumeration_count",
            confidence=0.92 if decennial_population is not None else None,
            geography_type="area_weighted_2020_census_blocks",
            evidence_ids=decennial_evidence,
            warnings=[
                "Area-weighted allocation of published 2020 Census block counts; not a current population estimate.",
                "Disclosure-avoidance methods apply, and the statistic does not describe any individual address.",
            ],
        )
    )
    for metric_id, variable, unit in (
        ("median_household_income", "B19013_001", "USD"),
        ("per_capita_income", "B19301_001", "USD"),
        ("median_age", "B01002_001", "years"),
    ):
        value, moe, evidence, confidence = _acs_aggregate(conn, weights, variable, median=True)
        metrics.append(
            MetricValue(
                metric_id,
                value,
                unit=unit,
                source_id="acs_2024_5yr",
                snapshot_id=_latest_census_variable_snapshot(
                    conn, "acs_2024_5yr", variable
                ),
                source_year="2020-2024",
                measure_type="survey_estimate",
                confidence=confidence,
                margin_of_error=moe,
                geography_type="population_weighted_block_groups",
                evidence_ids=evidence,
                warnings=["Weighted neighborhood summary; not an individual or household attribute."],
            )
        )
    age_groups = {
        "age_under_25": list(range(3, 11)) + list(range(27, 35)),
        "age_25_44": list(range(11, 15)) + list(range(35, 39)),
        "age_45_64": list(range(15, 20)) + list(range(39, 44)),
        "age_65_plus": list(range(20, 26)) + list(range(44, 50)),
    }
    for metric_id, lines in age_groups.items():
        value, moe, evidence, confidence = _sum_age_variables(conn, weights, lines)
        metrics.append(
            MetricValue(
                metric_id,
                value,
                unit="people",
                source_id="acs_2024_5yr",
                snapshot_id=_latest_census_variable_snapshot(
                    conn, "acs_2024_5yr", "B01001_001"
                ),
                source_year="2020-2024",
                measure_type="survey_estimate",
                confidence=confidence,
                margin_of_error=moe,
                geography_type="area_weighted_block_groups",
                evidence_ids=evidence,
            )
        )
    derived_ratios = {
        "bachelors_or_higher_share": (
            ["B15003_022", "B15003_023", "B15003_024", "B15003_025"],
            "B15003_001",
        ),
        "foreign_born_share": (["B05002_013"], "B05002_001"),
        "family_household_share": (["B11001_002"], "B11001_001"),
        "nonfamily_household_share": (["B11001_007"], "B11001_001"),
        "civilian_labor_force_employment_rate": (["B23025_004"], "B23025_003"),
        "civilian_labor_force_unemployment_rate": (["B23025_005"], "B23025_003"),
        "non_hispanic_white_share": (["B03002_003"], "B03002_001"),
        "non_hispanic_black_share": (["B03002_004"], "B03002_001"),
        "non_hispanic_asian_share": (["B03002_006"], "B03002_001"),
        "hispanic_or_latino_share": (["B03002_012"], "B03002_001"),
        "english_only_language_share": (["C16001_002"], "C16001_001"),
        "spanish_language_share": (["C16001_003"], "C16001_001"),
        "other_indo_european_language_share": (["C16001_006"], "C16001_001"),
        "asian_pacific_language_share": (["C16001_009"], "C16001_001"),
        "drove_alone_commute_share": (["B08301_003"], "B08301_001"),
        "public_transit_commute_share": (["B08301_010"], "B08301_001"),
        "walked_commute_share": (["B08301_019"], "B08301_001"),
        "worked_from_home_share": (["B08301_021"], "B08301_001"),
        "no_vehicle_household_share": (["B08201_002"], "B08201_001"),
        "owner_occupied_share": (["B25003_002"], "B25003_001"),
        "renter_occupied_share": (["B25003_003"], "B25003_001"),
        "management_business_science_arts_occupation_share": (
            ["C24010_003", "C24010_039"],
            "C24010_001",
        ),
        "service_occupation_share": (["C24010_019", "C24010_055"], "C24010_001"),
        "sales_office_occupation_share": (
            ["C24010_027", "C24010_063"],
            "C24010_001",
        ),
        "natural_resources_construction_maintenance_occupation_share": (
            ["C24010_030", "C24010_066"],
            "C24010_001",
        ),
        "production_transport_material_moving_occupation_share": (
            ["C24010_034", "C24010_070"],
            "C24010_001",
        ),
    }
    for metric_id, (numerators, denominator_variable) in derived_ratios.items():
        numerator_parts = [_acs_aggregate(conn, weights, variable) for variable in numerators]
        numerator = sum(result[0] or 0 for result in numerator_parts) if any(result[0] is not None for result in numerator_parts) else None
        numerator_moe = (
            math.sqrt(sum((result[1] or 0.0) ** 2 for result in numerator_parts))
            if numerator is not None
            else None
        )
        denominator, denominator_moe, denominator_evidence, denominator_confidence = _acs_aggregate(
            conn, weights, denominator_variable
        )
        ratio = _safe_ratio(numerator, denominator)
        ratio_moe = None
        if (
            ratio is not None
            and numerator is not None
            and denominator is not None
            and denominator > 0
            and numerator_moe is not None
            and denominator_moe is not None
        ):
            ratio_moe = math.sqrt(
                (numerator_moe / denominator) ** 2
                + (numerator * denominator_moe / (denominator**2)) ** 2
            )
        evidence = [item for result in numerator_parts for item in result[2]] + denominator_evidence
        component_confidences = [
            result[3] for result in numerator_parts if result[3] is not None
        ]
        if denominator_confidence is not None:
            component_confidences.append(denominator_confidence)
        metrics.append(
            MetricValue(
                metric_id,
                ratio,
                unit="share",
                source_id="acs_2024_5yr",
                snapshot_id=_latest_census_variable_snapshot(
                    conn, "acs_2024_5yr", denominator_variable
                ),
                source_year="2020-2024",
                measure_type="derived_survey_estimate",
                confidence=(min(component_confidences) if component_confidences else None),
                margin_of_error=ratio_moe,
                geography_type="area_weighted_published_census_geographies",
                evidence_ids=evidence,
                warnings=[
                    "Aggregated regional survey statistic; it does not describe any person, household, resident, worker, or address.",
                    "Displayed margin of error is a conservative first-order propagation from published ACS component MOEs and does not model covariance.",
                ],
            )
        )

    mappluto_snapshot = _latest_snapshot(conn, "mappluto_26v2") if borough else None
    parcels = _parcel_frame(conn, geometry) if mappluto_snapshot else pd.DataFrame()
    parcel_count = float(len(parcels)) if mappluto_snapshot else None
    parcel_evidence = parcels["parcel_id"].astype(str).tolist() if not parcels.empty else []
    land_warnings = (
        [
            "Parcel count includes every intersecting lot once; floor area and unit totals allocate edge lots by parcel-overlap fraction.",
            "MapPLUTO values are administrative property facts and may lag alterations or contain source-specific special-lot conventions.",
        ]
        if borough
        else [
            "No cross-state parcel metric is reported: regional parcel systems are fragmented and no non-authoritative substitute is presented as a parcel layer."
        ]
    )
    metrics.append(
        MetricValue(
            "parcel_count",
            parcel_count,
            unit="intersecting_tax_lots",
            source_id="mappluto_26v2",
            snapshot_id=mappluto_snapshot,
            source_year=_latest_vintage(conn, "mappluto_26v2"),
            measure_type="intersecting_administrative_record_count",
            confidence=0.97 if mappluto_snapshot else None,
            geography_type="tax_lots_intersecting_catchment",
            evidence_ids=parcel_evidence,
            warnings=land_warnings,
        )
    )
    land_specs = (
        ("intersecting_lot_area_sqft", "lot_area_sqft", "square_feet"),
        ("building_floor_area_sqft", "building_area_sqft", "square_feet"),
        ("residential_floor_area_sqft", "residential_area_sqft", "square_feet"),
        ("office_floor_area_sqft", "office_area_sqft", "square_feet"),
        ("retail_floor_area_sqft", "retail_area_sqft", "square_feet"),
        ("residential_units", "units_residential", "dwelling_units"),
        ("total_units", "units_total", "units"),
    )
    land_values: dict[str, float | None] = {}
    for metric_id, column, unit in land_specs:
        value = _weighted_sum(parcels, column) if mappluto_snapshot else None
        land_values[metric_id] = value
        metrics.append(
            MetricValue(
                metric_id,
                value,
                unit=unit,
                source_id="mappluto_26v2",
                snapshot_id=mappluto_snapshot,
                source_year=_latest_vintage(conn, "mappluto_26v2"),
                measure_type="parcel_overlap_weighted_administrative_sum",
                confidence=0.92 if mappluto_snapshot else None,
                geography_type="area_weighted_intersecting_tax_lots",
                evidence_ids=parcel_evidence,
                warnings=land_warnings,
            )
        )
    catchment_area_sqft = float(catchment_area_m2 * 10.76391041671)
    for metric_id, numerator_id in (
        ("building_floor_area_density", "building_floor_area_sqft"),
        ("residential_floor_area_density", "residential_floor_area_sqft"),
        ("office_floor_area_density", "office_floor_area_sqft"),
        ("retail_floor_area_density", "retail_floor_area_sqft"),
    ):
        numerator = land_values.get(numerator_id)
        metrics.append(
            MetricValue(
                metric_id,
                numerator / catchment_area_sqft if numerator is not None and catchment_area_sqft > 0 else None,
                unit="floor_sqft_per_catchment_sqft",
                source_id="derived",
                snapshot_id=mappluto_snapshot,
                source_year=_latest_vintage(conn, "mappluto_26v2"),
                measure_type="derived_spatial_density",
                confidence=0.88 if numerator is not None else None,
                geography_type="catchment",
                evidence_ids=parcel_evidence,
                warnings=[
                    "Gross floor area is divided by total catchment area, including streets, parks and water; this is not parcel FAR.",
                    *land_warnings,
                ],
            )
        )
    building_source = "nyc_building_footprints" if borough else "overture_buildings"
    building_snapshot = _latest_snapshot(conn, building_source)
    if building_snapshot:
        buildings, _ = _polygon_frame(
            conn,
            "building",
            geometry,
            where=f"source_id='{building_source}'",
            columns=(
                "building_id,min_longitude,min_latitude,max_longitude,max_latitude,geom_wkt,snapshot_id"
            ),
        )
    else:
        buildings = pd.DataFrame()
    metrics.append(
        MetricValue(
            "building_footprint_count",
            float(len(buildings)) if building_snapshot else None,
            unit="intersecting_building_footprints",
            source_id=building_source,
            snapshot_id=building_snapshot,
            source_year="current published snapshot",
            measure_type=(
                "intersecting_administrative_record_count"
                if borough
                else "intersecting_compiled_record_count"
            ),
            confidence=(0.95 if borough else 0.75) if building_snapshot else None,
            geography_type="building_footprints_intersecting_catchment",
            evidence_ids=buildings["building_id"].astype(str).tolist() if not buildings.empty else [],
            warnings=[
                "Counts each intersecting footprint once, including footprints that only cross the catchment edge; footprint does not always equal a separately occupied building.",
                *([] if borough else ["Regional footprint coverage is compiled from Overture Buildings under ODbL and is not an authoritative parcel or occupancy record."]),
            ],
        )
    )

    bbls = parcels["bbl"].dropna().astype(str).unique().tolist() if not parcels.empty else []
    sales_snapshot = _latest_snapshot(conn, "nyc_rolling_sales") if borough else None
    sales = _records_for_bbls(
        conn,
        "property_record",
        bbls,
        where="r.source_id='nyc_rolling_sales' AND r.record_type='rolling_calendar_sale'",
        columns="r.property_record_id,r.geography_id,r.record_date,r.amount,r.snapshot_id",
    ) if sales_snapshot else pd.DataFrame()
    positive_sale_prices = sales.loc[sales["amount"] > 0, "amount"] if not sales.empty else pd.Series(dtype=float)
    sale_count = float(len(sales)) if sales_snapshot else None
    sale_warnings = [
        "Rolling calendar source covers the publisher's preceding twelve months; records are transactions, not current appraisals.",
        "Zero-dollar and non-market transfers remain in record counts but are excluded from the median positive sale price.",
        "Apartment number is intentionally excluded; tax-lot linkage can place multiple unit sales on the same BBL.",
    ]
    for metric_id, value, unit, measure_type in (
        ("rolling_sale_record_count", sale_count, "sale_records", "administrative_transaction_record_count"),
        (
            "rolling_sale_price_median",
            float(positive_sale_prices.median()) if not positive_sale_prices.empty else None,
            "USD",
            "median_positive_recorded_sale_price",
        ),
        (
            "rolling_sales_per_100_parcels",
            sale_count / parcel_count * 100.0 if sale_count is not None and parcel_count else None,
            "sale_records_per_100_intersecting_parcels",
            "derived_transaction_density",
        ),
    ):
        metrics.append(
            MetricValue(
                metric_id,
                value,
                unit=unit,
                source_id="nyc_rolling_sales" if metric_id != "rolling_sales_per_100_parcels" else "derived",
                snapshot_id=sales_snapshot,
                source_year="current rolling twelve-month publication",
                measure_type=measure_type,
                confidence=0.9 if value is not None else None,
                geography_type="records_linked_to_intersecting_tax_lots",
                evidence_ids=sales["property_record_id"].astype(str).tolist() if not sales.empty else [],
                warnings=sale_warnings,
            )
        )

    acris_snapshot = _latest_snapshot(conn, "acris_real_property_master_recent") if borough else None
    acris_latest = conn.execute(
        "SELECT max(record_date) FROM property_record WHERE source_id='acris_real_property_master_recent'"
    ).fetchone()[0] if acris_snapshot else None
    acris_start = (pd.Timestamp(acris_latest) - pd.DateOffset(months=24)).date() if acris_latest else None
    acris_documents = _records_for_bbls(
        conn,
        "property_record",
        bbls,
        where=(
            "r.source_id='acris_real_property_master_recent' "
            "AND r.record_date BETWEEN ? AND ?"
        ),
        params=[acris_start, acris_latest],
        columns="r.property_record_id,r.geography_id,r.record_type,r.record_date,r.amount,r.snapshot_id",
    ) if acris_start and bbls else pd.DataFrame()
    acris_warnings = [
        "Counts ACRIS documents linked locally to intersecting tax lots; one document affecting multiple lots is counted once per linked lot.",
        "Only DEED, RPTT&RET, MTGE, SAT and ASST document types are in the bounded 2021-present extract.",
        "No party, name, street-address or unit data is retained. Filing records and amounts are not appraisals, title opinions or legal conclusions.",
    ]
    acris_values = (
        (
            "acris_document_record_count_24m",
            float(len(acris_documents)) if acris_snapshot else None,
            "document_lot_records",
            "administrative_property_document_count",
        ),
        (
            "acris_deed_document_count_24m",
            float(acris_documents["record_type"].eq("acris_deed").sum()) if acris_snapshot else None,
            "deed_document_lot_records",
            "administrative_property_document_count",
        ),
        (
            "acris_mortgage_document_count_24m",
            float(acris_documents["record_type"].eq("acris_mtge").sum()) if acris_snapshot else None,
            "mortgage_document_lot_records",
            "administrative_property_document_count",
        ),
        (
            "acris_document_records_per_100_parcels_24m",
            len(acris_documents) / parcel_count * 100.0
            if acris_snapshot and parcel_count
            else None,
            "document_lot_records_per_100_intersecting_parcels",
            "derived_property_document_density",
        ),
    )
    for metric_id, value, unit, measure_type in acris_values:
        metrics.append(
            MetricValue(
                metric_id,
                value,
                unit=unit,
                source_id=(
                    "derived"
                    if metric_id == "acris_document_records_per_100_parcels_24m"
                    else "acris_real_property_master_recent"
                ),
                snapshot_id=acris_snapshot,
                source_year=f"{acris_start} through {acris_latest}" if acris_start else None,
                measure_type=measure_type,
                confidence=0.9 if value is not None else None,
                geography_type="records_linked_to_intersecting_tax_lots",
                evidence_ids=(
                    acris_documents["property_record_id"].astype(str).tolist()
                    if not acris_documents.empty
                    else []
                ),
                warnings=acris_warnings,
            )
        )

    case_snapshot = _latest_snapshot(conn, "derived_public_transaction_cases") if borough else None
    transaction_cases = _records_for_bbls(
        conn,
        "transaction_case",
        bbls,
        where="r.source_id='derived_public_transaction_cases'",
        columns=(
            "r.transaction_case_id,r.geography_id,r.case_type,r.transaction_date,"
            "r.amount,r.snapshot_id"
        ),
    ) if case_snapshot else pd.DataFrame()
    case_period = conn.execute(
        "SELECT min(transaction_date), max(transaction_date) FROM transaction_case "
        "WHERE source_id='derived_public_transaction_cases'"
    ).fetchone() if case_snapshot else (None, None)
    case_amounts = (
        pd.to_numeric(transaction_cases["amount"], errors="coerce").dropna()
        if not transaction_cases.empty
        else pd.Series(dtype=float)
    )
    case_warnings = [
        "Privacy-minimized cases combine positive-amount rolling-sale and ACRIS deed records, deduplicated by tax lot, date and rounded amount.",
        "They are public real-property records, not business acquisitions, current valuations, legal conclusions, or property-type-matched comparables.",
        "Names, parties, street and unit addresses, contact details, and personal profiles are excluded.",
    ]
    for metric_id, value, unit, measure_type in (
        (
            "historical_transaction_case_count",
            float(len(transaction_cases)) if case_snapshot else None,
            "public_cases",
            "privacy_minimized_public_case_count",
        ),
        (
            "historical_transaction_amount_median",
            float(case_amounts.median()) if not case_amounts.empty else None,
            "USD",
            "median_positive_public_case_amount",
        ),
    ):
        metrics.append(
            MetricValue(
                metric_id,
                value,
                unit=unit,
                source_id="derived_public_transaction_cases",
                snapshot_id=case_snapshot,
                source_year=(
                    f"{case_period[0]} through {case_period[1]}"
                    if case_period[0] is not None
                    else None
                ),
                measure_type=measure_type,
                confidence=0.82 if value is not None else None,
                geography_type="cases_linked_to_intersecting_tax_lots",
                evidence_ids=(
                    transaction_cases["transaction_case_id"].astype(str).tolist()
                    if not transaction_cases.empty
                    else []
                ),
                warnings=case_warnings,
            )
        )

    permit_sources = {
        source_id: (_latest_snapshot(conn, source_id) if borough else None)
        for source_id in ("dob_permit_issuance_recent", "dob_now_approved_permits_recent")
    }
    available_permit_sources = [source_id for source_id, snapshot in permit_sources.items() if snapshot]
    permit_latest = conn.execute(
        """
        SELECT max(issued_at) FROM regulatory_record
        WHERE record_type IN ('building_permit_bis','building_permit_dob_now')
        """
    ).fetchone()[0] if available_permit_sources else None
    permit_start = (pd.Timestamp(permit_latest) - pd.DateOffset(months=24)).date() if permit_latest else None
    permits = _records_for_bbls(
        conn,
        "regulatory_record",
        bbls,
        where=(
            "r.record_type IN ('building_permit_bis','building_permit_dob_now') "
            "AND r.issued_at BETWEEN ? AND ?"
        ),
        params=[permit_start, permit_latest],
        columns="r.regulatory_record_id,r.geography_id,r.record_type,r.issued_at,r.attributes_json,r.source_id",
    ) if permit_start and bbls else pd.DataFrame()
    permit_count = float(len(permits)) if available_permit_sources else None
    metrics.append(
        MetricValue(
            "building_permit_issued_24m_count",
            permit_count,
            unit="permit_records",
            source_id="derived",
            source_year=f"{permit_start} through {permit_latest}" if permit_start else None,
            measure_type="combined_administrative_record_count",
            confidence=(0.9 if len(available_permit_sources) == 2 else 0.65) if permit_count is not None else None,
            geography_type="records_linked_to_intersecting_tax_lots",
            evidence_ids=permits["regulatory_record_id"].astype(str).tolist() if not permits.empty else [],
            warnings=[
                f"Available permit systems: {json_dumps(available_permit_sources)}.",
                "Counts permit records, including renewals and multiple work types; it is not a count of unique projects or proof work occurred.",
                "DOB NOW source excludes separate electrical, elevator and LAA permit systems.",
            ],
        )
    )

    dob_violation_sources = {
        source_id: (_latest_snapshot(conn, source_id) if borough else None)
        for source_id in ("dob_violations_active", "dob_safety_violations_active")
    }
    available_dob_violation_sources = [
        source_id for source_id, snapshot in dob_violation_sources.items() if snapshot
    ]
    dob_violations = _records_for_bbls(
        conn,
        "regulatory_record",
        bbls,
        where="r.record_type IN ('dob_violation_active_legacy','dob_safety_violation_active')",
        columns="r.regulatory_record_id,r.geography_id,r.record_type,r.attributes_json,r.source_id",
    ) if available_dob_violation_sources else pd.DataFrame()
    if not dob_violations.empty:
        dob_violations["case_key"] = dob_violations.apply(
            _case_key, axis=1, preferred_fields=("violation_number", "isn_dob_bis_viol")
        )
        dob_violations = dob_violations.drop_duplicates("case_key", keep="first")
    active_dob_count = float(len(dob_violations)) if available_dob_violation_sources else None
    ecb_snapshot = _latest_snapshot(conn, "dob_ecb_violations_active") if borough else None
    ecb_violations = _records_for_bbls(
        conn,
        "regulatory_record",
        bbls,
        where="r.record_type='dob_ecb_violation_active'",
        columns="r.regulatory_record_id,r.geography_id,r.record_type,r.attributes_json,r.source_id",
    ) if ecb_snapshot else pd.DataFrame()
    if not ecb_violations.empty:
        ecb_violations["case_key"] = ecb_violations.apply(
            _case_key, axis=1, preferred_fields=("ecb_violation_number", "isn_dob_bis_extract")
        )
        ecb_violations = ecb_violations.drop_duplicates("case_key", keep="first")
    active_ecb_count = float(len(ecb_violations)) if ecb_snapshot else None
    violation_warning = [
        "Counts currently published administrative records linked by BBL; they are screening facts, not safety conclusions or legal opinions.",
        "Legacy and DOB NOW civil-penalty records are deduplicated when the same BBL and violation number are available; residual overlap may remain.",
        "No linked record does not prove absence of a violation, hazard or compliance issue.",
    ]
    violation_metrics = (
        (
            "active_dob_violation_count",
            active_dob_count,
            "active_dob_records",
            dob_violations,
            None,
        ),
        (
            "active_dob_violations_per_100_parcels",
            active_dob_count / parcel_count * 100.0 if active_dob_count is not None and parcel_count else None,
            "active_dob_records_per_100_intersecting_parcels",
            dob_violations,
            None,
        ),
        (
            "active_ecb_violation_count",
            active_ecb_count,
            "active_ecb_records",
            ecb_violations,
            ecb_snapshot,
        ),
        (
            "active_ecb_violations_per_100_parcels",
            active_ecb_count / parcel_count * 100.0 if active_ecb_count is not None and parcel_count else None,
            "active_ecb_records_per_100_intersecting_parcels",
            ecb_violations,
            ecb_snapshot,
        ),
    )
    for metric_id, value, unit, record_frame, snapshot_id in violation_metrics:
        metrics.append(
            MetricValue(
                metric_id,
                value,
                unit=unit,
                source_id="derived" if "per_100" in metric_id or metric_id.startswith("active_dob") else "dob_ecb_violations_active",
                snapshot_id=snapshot_id,
                source_year="current active inventories",
                measure_type=(
                    "deduplicated_administrative_record_density"
                    if "per_100" in metric_id
                    else "deduplicated_administrative_record_count"
                ),
                confidence=(
                    0.88 if metric_id.startswith("active_ecb")
                    else (0.88 if len(available_dob_violation_sources) == 2 else 0.62)
                ) if value is not None else None,
                geography_type="records_linked_to_intersecting_tax_lots",
                evidence_ids=(
                    record_frame["regulatory_record_id"].astype(str).tolist()
                    if not record_frame.empty
                    else []
                ),
                warnings=[
                    f"Available legacy/DOB NOW civil-penalty systems: {json_dumps(available_dob_violation_sources)}.",
                    *violation_warning,
                ],
            )
        )

    co_sources = {
        source_id: (_latest_snapshot(conn, source_id) if borough else None)
        for source_id in ("dob_certificate_occupancy_legacy", "dob_now_certificate_occupancy")
    }
    available_co_sources = [source_id for source_id, snapshot in co_sources.items() if snapshot]
    co_latest = conn.execute(
        """
        SELECT max(issued_at) FROM regulatory_record
        WHERE record_type IN ('certificate_of_occupancy_bis','certificate_of_occupancy_dob_now')
        """
    ).fetchone()[0] if available_co_sources else None
    co_start = (pd.Timestamp(co_latest) - pd.DateOffset(months=24)).date() if co_latest else None
    certificates = _records_for_bbls(
        conn,
        "regulatory_record",
        bbls,
        where=(
            "r.record_type IN ('certificate_of_occupancy_bis','certificate_of_occupancy_dob_now') "
            "AND r.issued_at BETWEEN ? AND ?"
        ),
        params=[co_start, co_latest],
        columns="r.regulatory_record_id,r.geography_id,r.record_type,r.issued_at,r.attributes_json,r.source_id",
    ) if co_start and bbls else pd.DataFrame()
    metrics.append(
        MetricValue(
            "certificate_of_occupancy_issued_24m_count",
            float(len(certificates)) if available_co_sources else None,
            unit="certificate_records",
            source_id="derived",
            source_year=f"{co_start} through {co_latest}" if co_start else None,
            measure_type="combined_administrative_record_count",
            confidence=(0.9 if len(available_co_sources) == 2 else 0.65) if available_co_sources else None,
            geography_type="records_linked_to_intersecting_tax_lots",
            evidence_ids=certificates["regulatory_record_id"].astype(str).tolist() if not certificates.empty else [],
            warnings=[
                f"Available certificate systems: {json_dumps(available_co_sources)}.",
                "Counts issued certificate records in the latest source-relative 24-month window, not unique buildings or proof of present use.",
            ],
        )
    )

    requests_311_snapshot = _latest_snapshot(conn, "nyc_311_recent_aggregates") if borough else None
    requests_311 = _records_for_bbls(
        conn,
        "regulatory_record",
        bbls,
        where=(
            "r.source_id='nyc_311_recent_aggregates' "
            "AND r.record_type='311_request_aggregate_24m'"
        ),
        columns=(
            "r.regulatory_record_id,r.geography_id,r.snapshot_id,"
            "try_cast(json_extract_string(r.attributes_json, '$.request_count') AS DOUBLE) AS request_count,"
            "json_extract_string(r.attributes_json, '$.complaint_type') AS complaint_type"
        ),
    ) if requests_311_snapshot else pd.DataFrame()
    requests_311_total = (
        float(pd.to_numeric(requests_311["request_count"], errors="coerce").fillna(0).sum())
        if requests_311_snapshot
        else None
    )
    noise_311 = (
        requests_311.loc[
            requests_311["complaint_type"].fillna("").str.contains("noise", case=False, regex=False)
        ]
        if not requests_311.empty
        else requests_311
    )
    noise_311_total = (
        float(pd.to_numeric(noise_311["request_count"], errors="coerce").fillna(0).sum())
        if requests_311_snapshot
        else None
    )
    period_311 = conn.execute(
        "SELECT valid_from, valid_to FROM dataset_snapshot WHERE snapshot_id=?",
        [requests_311_snapshot],
    ).fetchone() if requests_311_snapshot else None
    request_warnings = [
        "The Raw source is already aggregated by BBL, problem and agency; no individual request id, incident address, descriptor, free text or coordinate was downloaded.",
        "A 311 request is a report to the City, not an independently verified condition, unique incident, legal finding or proof the issue remains open.",
    ]
    request_metrics = (
        (
            "311_request_count_24m",
            requests_311_total,
            "service_requests",
            requests_311,
            "server_aggregated_administrative_request_sum",
        ),
        (
            "311_noise_request_count_24m",
            noise_311_total,
            "noise_service_requests",
            noise_311,
            "server_aggregated_administrative_request_sum",
        ),
        (
            "311_requests_per_100_parcels_24m",
            requests_311_total / parcel_count * 100.0
            if requests_311_total is not None and parcel_count
            else None,
            "service_requests_per_100_intersecting_parcels",
            requests_311,
            "derived_request_density",
        ),
        (
            "311_noise_requests_per_100_parcels_24m",
            noise_311_total / parcel_count * 100.0
            if noise_311_total is not None and parcel_count
            else None,
            "noise_service_requests_per_100_intersecting_parcels",
            noise_311,
            "derived_request_density",
        ),
    )
    for metric_id, value, unit, record_frame, measure_type in request_metrics:
        metrics.append(
            MetricValue(
                metric_id,
                value,
                unit=unit,
                source_id="derived" if "per_100" in metric_id else "nyc_311_recent_aggregates",
                snapshot_id=requests_311_snapshot,
                source_year=(
                    f"{period_311[0]} through {period_311[1]}" if period_311 else None
                ),
                measure_type=measure_type,
                confidence=0.78 if value is not None else None,
                geography_type="aggregates_linked_to_intersecting_tax_lots",
                evidence_ids=(
                    record_frame["regulatory_record_id"].astype(str).tolist()
                    if not record_frame.empty
                    else []
                ),
                warnings=request_warnings,
            )
        )

    workforce = _point_frame(
        conn,
        "workforce_estimate",
        "workforce_estimate_id",
        geometry,
        where="source_id='lodes_2023' AND measure IN ('C000','CNS01','CNS02','CNS03','CNS04','CNS05','CNS06','CNS07','CNS08','CNS09','CNS10','CNS11','CNS12','CNS13','CNS14','CNS15','CNS16','CNS17','CNS18','CNS19','CNS20')",
        columns="workforce_estimate_id, geography_id, measure, value, longitude, latitude, snapshot_id",
    )
    workforce_snapshot = (
        str(workforce["snapshot_id"].value_counts().index[0])
        if not workforce.empty and workforce["snapshot_id"].notna().any()
        else None
    )
    jobs = float(workforce.loc[workforce["measure"] == "C000", "value"].sum()) if not workforce.empty else None
    metrics.append(
        MetricValue(
            "daytime_jobs",
            jobs,
            unit="jobs",
            source_id="lodes_2023",
            snapshot_id=workforce_snapshot,
            source_year="2023",
            measure_type="administrative_estimate",
            confidence=0.9 if jobs is not None else None,
            geography_type="census_block_points",
            evidence_ids=workforce.loc[workforce["measure"] == "C000", "workforce_estimate_id"].astype(str).tolist() if not workforce.empty else [],
            warnings=["LODES counts jobs, not necessarily distinct natural persons."],
        )
    )
    if not workforce.empty:
        industries = workforce.loc[workforce["measure"].str.startswith("CNS")].groupby("measure")["value"].sum().sort_values(ascending=False).head(5)
        metrics.append(
            MetricValue(
                "top_workplace_industries",
                text_value=json_dumps({key: float(value) for key, value in industries.items()}),
                source_id="lodes_2023",
                snapshot_id=workforce_snapshot,
                source_year="2023",
                measure_type="administrative_estimate",
                confidence=0.9,
                geography_type="census_block_points",
                evidence_ids=workforce.loc[
                    workforce["measure"].isin(industries.index), "workforce_estimate_id"
                ].astype(str).tolist(),
            )
        )

    pois = _point_frame(
        conn,
        "poi",
        "poi_id",
        geometry,
        where="source_id='overture_places'",
        columns="poi_id, basic_category, status, longitude, latitude, snapshot_id",
    )
    overture_vintage = _latest_vintage(conn, "overture_places")
    taxonomy = taxonomy_config().get("business_types", {}).get(business_type, {})
    if borough:
        businesses = _point_frame(
            conn,
            "business_location",
            "business_location_id",
            geometry,
            where="source_id='dohmh_restaurants'",
            columns="business_location_id, primary_category, cuisine, status, longitude, latitude, snapshot_id",
        )
        direct = len(businesses)
        closed = int((businesses.get("status", pd.Series(dtype=str)) == "closed_by_dohmh").sum())
        competitor_source = "dohmh_restaurants"
        competitor_snapshot = _latest_snapshot(conn, competitor_source)
        competitor_source_year = "current active-establishment extract"
        competitor_measure_type = "administrative_record_count"
        competitor_confidence = 0.9
        competitor_evidence_ids = businesses["business_location_id"].astype(str).tolist()
        competitor_warnings = [
            "Broad restaurant set; same-cuisine and service-format refinement requires query-specific cuisine/price inputs."
        ]
    else:
        direct_categories = set(taxonomy.get("direct", {}).get("overture_basic_categories", []))
        direct_pois = pois.loc[pois["basic_category"].isin(direct_categories)] if not pois.empty else pois
        direct = len(direct_pois)
        closed = int(
            direct_pois.get("status", pd.Series(dtype=str)).fillna("").str.contains("closed", case=False).sum()
        )
        competitor_source = "overture_places"
        competitor_snapshot = _latest_snapshot(conn, competitor_source)
        competitor_source_year = overture_vintage
        competitor_measure_type = "compiled_record_count"
        competitor_confidence = 0.7
        competitor_evidence_ids = direct_pois["poi_id"].astype(str).tolist()
        competitor_warnings = [
            f"Regional direct categories: {json_dumps(sorted(direct_categories))}.",
            "Overture is a compiled place inventory with mixed upstream freshness; count is a screening proxy, not an authoritative business registry.",
        ]
    for metric_id, value in (("direct_competitor_count", direct), ("closed_competitor_count", closed)):
        metrics.append(
            MetricValue(
                metric_id,
                float(value),
                unit="locations",
                source_id=competitor_source,
                snapshot_id=competitor_snapshot,
                source_year=competitor_source_year,
                measure_type=competitor_measure_type,
                confidence=competitor_confidence,
                geography_type="point_in_catchment",
                evidence_ids=competitor_evidence_ids,
                warnings=competitor_warnings,
            )
        )
    substitute_categories = set(taxonomy.get("substitutes", {}).get("overture_basic_categories", []))
    complement_categories = set(taxonomy.get("complements", {}).get("overture_basic_categories", []))
    substitute = pois.loc[pois["basic_category"].isin(substitute_categories)] if not pois.empty else pois
    complements = pois.loc[pois["basic_category"].isin(complement_categories)] if not pois.empty else pois
    nightlife = pois.loc[pois["basic_category"].isin({"bar", "night_club", "pub"})] if not pois.empty else pois
    attractions = pois.loc[pois["basic_category"].isin({"cinema", "theatre", "museum", "tourist_attraction"})] if not pois.empty else pois
    for metric_id, frame in (
        ("substitute_count", substitute),
        ("demand_complement_count", complements),
        ("nightlife_location_count", nightlife),
        ("attraction_count", attractions),
    ):
        metrics.append(
            MetricValue(
                metric_id,
                float(len(frame)),
                unit="locations",
                source_id="overture_places",
                snapshot_id=_latest_snapshot(conn, "overture_places"),
                source_year=overture_vintage,
                measure_type="compiled_record_count",
                confidence=float(frame["status"].notna().mean() * 0.2 + 0.65) if not frame.empty else 0.6,
                geography_type="point_in_catchment",
                evidence_ids=frame["poi_id"].astype(str).tolist(),
            )
        )
    regional_poi_groups = {
        "regional_hotel_poi_count": pois.loc[
            pois["basic_category"].isin(
                {"hotel", "lodging", "private_lodging", "resort", "hostel", "motel"}
            )
        ],
        "regional_school_poi_count": pois.loc[
            pois["basic_category"].isin(
                {
                    "preschool",
                    "elementary_school",
                    "middle_school",
                    "high_school",
                    "specialty_school",
                    "college_university",
                }
            )
        ],
        "regional_healthcare_poi_count": pois.loc[
            pois["basic_category"].isin(
                {
                    "health_care",
                    "hospital",
                    "general_hospital",
                    "specialty_hospital",
                    "primary_care_or_general_clinic",
                    "urgent_care_clinic",
                    "walk_in_clinic",
                    "behavioral_or_mental_health_clinic",
                    "pediatric_clinic",
                    "dental_clinic",
                    "medical_service",
                }
            )
        ],
        "regional_park_poi_count": pois.loc[
            pois["basic_category"].isin({"park", "national_park", "dog_park"})
        ],
        "regional_library_poi_count": pois.loc[pois["basic_category"].eq("library")],
    }
    for metric_id, frame in regional_poi_groups.items():
        metrics.append(
            MetricValue(
                metric_id,
                float(len(frame)),
                unit="compiled_place_points",
                source_id="overture_places",
                snapshot_id=_latest_snapshot(conn, "overture_places"),
                source_year=overture_vintage,
                measure_type="compiled_place_proxy_count",
                confidence=0.6 if not frame.empty else 0.55,
                geography_type="overture_category_points_in_catchment",
                evidence_ids=frame["poi_id"].astype(str).tolist(),
                warnings=[
                    "Overture is a compiled POI inventory with mixed upstream freshness; categories may be duplicated, stale, incomplete or misclassified.",
                    "This is a regional screening proxy, not an operator, regulator or local-government authoritative inventory.",
                ],
            )
        )
    hotels = _point_frame(
        conn,
        "hotel",
        "hotel_id",
        geometry,
        where="source_id='nyc_hotels'",
        columns="hotel_id, longitude, latitude, snapshot_id",
    )
    metrics.append(
        MetricValue(
            "hotel_count",
            float(len(hotels)),
            unit="properties",
            source_id="nyc_hotels",
            snapshot_id=_latest_snapshot(conn, "nyc_hotels"),
            source_year="current published snapshot",
            measure_type="administrative_record_count",
            confidence=0.85,
            geography_type="point_in_catchment",
            evidence_ids=hotels["hotel_id"].astype(str).tolist(),
        )
    )
    licensed_businesses = _point_frame(
        conn,
        "business_location",
        "business_location_id",
        geometry,
        where="source_id='nysla_active_licenses' AND status='active_license'",
        columns="business_location_id, longitude, latitude, snapshot_id",
    )
    metrics.append(
        MetricValue(
            "alcohol_license_count",
            float(len(licensed_businesses)),
            unit="active_licenses",
            source_id="nysla_active_licenses",
            snapshot_id=_latest_snapshot(conn, "nysla_active_licenses"),
            source_year="current active inventory",
            measure_type="administrative_record_count",
            confidence=0.92,
            geography_type="licensed_points_in_catchment",
            evidence_ids=licensed_businesses["business_location_id"].astype(str).tolist(),
            warnings=["Current active-license inventory, not a complete license history or a measure of alcohol sales."],
        )
    )
    short_term_rentals = _point_frame(
        conn,
        "short_term_rental",
        "short_term_rental_id",
        geometry,
        where="source_id='nyc_str_registration' AND status='registered'",
        columns="short_term_rental_id, longitude, latitude, snapshot_id",
    )
    metrics.append(
        MetricValue(
            "legal_short_term_rental_count",
            float(len(short_term_rentals)),
            unit="registrations",
            source_id="nyc_str_registration",
            snapshot_id=_latest_snapshot(conn, "nyc_str_registration"),
            source_year="2026-01-07",
            measure_type="administrative_record_count",
            confidence=0.78,
            geography_type="building_points_in_catchment",
            evidence_ids=short_term_rentals["short_term_rental_id"].astype(str).tolist(),
            warnings=[
                "Official registration status as of 2026-01-07; later changes are not represented.",
                "Registration does not prove every stay complies with occupancy rules; building points are used instead of unit locations.",
            ],
        )
    )
    facilities = _point_frame(
        conn,
        "poi",
        "poi_id",
        geometry,
        where="source_id='nyc_facilities'",
        columns="poi_id, basic_category, longitude, latitude, snapshot_id",
    )
    facility_groups = {
        "public_facility_count": facilities,
        "school_count": facilities.loc[facilities["basic_category"] == "school"],
        "health_facility_count": facilities.loc[facilities["basic_category"] == "health_facility"],
        "park_facility_count": facilities.loc[facilities["basic_category"] == "park"],
        "culture_and_library_count": facilities.loc[
            facilities["basic_category"].isin({"cultural_facility", "library"})
        ],
    }
    for metric_id, facility_frame in facility_groups.items():
        metrics.append(
            MetricValue(
                metric_id,
                float(len(facility_frame)),
                unit="facilities",
                source_id="nyc_facilities",
                snapshot_id=_latest_snapshot(conn, "nyc_facilities"),
                source_year="current compiled publication",
                measure_type="compiled_administrative_record_count",
                confidence=0.82,
                geography_type="facility_points_in_catchment",
                evidence_ids=facility_frame["poi_id"].astype(str).tolist(),
                warnings=["Compiled from heterogeneous agency sources with different update cycles and capacity definitions."],
            )
        )
    stations = _point_frame(
        conn,
        "transit_stop",
        "transit_stop_id",
        geometry,
        where="source_id='mta_subway_stations'",
        columns="transit_stop_id, complex_id, longitude, latitude, snapshot_id",
    )
    complex_ids = stations["complex_id"].dropna().astype(str).unique().tolist() if not stations.empty else []
    ridership_snapshot_id = _latest_snapshot(conn, "mta_hourly_2025")
    annual_entries = 0.0 if ridership_snapshot_id is not None and not complex_ids else None
    ridership_evidence: list[str] = []
    if complex_ids:
        placeholders = ",".join("?" for _ in complex_ids)
        annual_entries, ridership_evidence = conn.execute(
            f"SELECT sum(ridership), list(transit_ridership_id) FROM transit_ridership WHERE source_id='mta_hourly_2025' AND complex_id IN ({placeholders})",
            complex_ids,
        ).fetchone()
        annual_entries = float(annual_entries) if annual_entries is not None else None
        ridership_evidence = [str(value) for value in (ridership_evidence or [])]
    metrics.extend(
        [
            MetricValue(
                "subway_complex_count",
                float(len(complex_ids)),
                unit="complexes",
                source_id="mta_subway_stations",
                snapshot_id=_latest_snapshot(conn, "mta_subway_stations"),
                source_year="current",
                measure_type="administrative_record_count",
                confidence=0.98,
                geography_type="point_in_catchment",
                evidence_ids=stations["transit_stop_id"].astype(str).tolist(),
            ),
            MetricValue(
                "subway_entries_2025",
                annual_entries,
                unit="entries",
                source_id="mta_hourly_2025",
                snapshot_id=ridership_snapshot_id,
                source_year="2025",
                measure_type="administrative_estimate",
                confidence=0.9 if annual_entries is not None else None,
                geography_type="station_complexes_in_catchment",
                geography_id=",".join(complex_ids),
                evidence_ids=ridership_evidence,
            ),
        ]
    )
    entrance_snapshot_id = _latest_snapshot(conn, "mta_subway_entrances_2024")
    entrances = _point_frame(
        conn,
        "transit_stop",
        "transit_stop_id",
        geometry,
        where="source_id='mta_subway_entrances_2024'",
        columns=(
            "transit_stop_id, entrance_type, entry_allowed, exit_allowed, "
            "longitude, latitude, snapshot_id"
        ),
    )
    if not entrances.empty:
        physical_key = (
            entrances["longitude"].round(6).astype(str)
            + "|"
            + entrances["latitude"].round(6).astype(str)
            + "|"
            + entrances["entrance_type"].fillna("").astype(str)
            + "|"
            + entrances["entry_allowed"].fillna(False).astype(str)
            + "|"
            + entrances["exit_allowed"].fillna(False).astype(str)
        )
        physical_entrances = entrances.loc[~physical_key.duplicated()].copy()
    else:
        physical_entrances = entrances
    for metric_id, entrance_frame in (
        ("subway_entrance_exit_count", physical_entrances),
        ("subway_entry_allowed_count", physical_entrances.loc[physical_entrances.get("entry_allowed", False).eq(True)] if not physical_entrances.empty else physical_entrances),
    ):
        metrics.append(
            MetricValue(
                metric_id,
                float(len(entrance_frame)) if entrance_snapshot_id is not None else None,
                unit="entrance_exit_points",
                source_id="mta_subway_entrances_2024",
                snapshot_id=entrance_snapshot_id,
                source_year="2024",
                measure_type="deduplicated_administrative_point_count",
                confidence=0.94 if entrance_snapshot_id is not None else None,
                geography_type="physical_entrance_exit_points_in_catchment",
                evidence_ids=entrance_frame["transit_stop_id"].astype(str).tolist(),
                warnings=[
                    "Physical points are deduplicated by coordinate, entrance type and entry/exit permissions because one entrance can be associated with multiple station complexes.",
                    "The 2024 static inventory does not guarantee current construction access, elevator operation, hours, or ADA routing.",
                ],
            )
        )
    bus_stops = _point_frame(
        conn,
        "transit_stop",
        "transit_stop_id",
        geometry,
        where="source_id='mta_bus_gtfs_static'",
        columns="transit_stop_id, source_stop_id, name, longitude, latitude, snapshot_id",
    )
    if not bus_stops.empty:
        physical_stop_keys = (
            bus_stops["name"].fillna("").str.upper().str.strip()
            + "|"
            + bus_stops["longitude"].round(5).astype(str)
            + "|"
            + bus_stops["latitude"].round(5).astype(str)
        )
        bus_stop_count = float(physical_stop_keys.nunique())
    else:
        bus_stop_count = 0.0 if _latest_snapshot(conn, "mta_bus_gtfs_static") else None
    metrics.append(
        MetricValue(
            "bus_stop_count",
            bus_stop_count,
            unit="scheduled_stop_points",
            source_id="mta_bus_gtfs_static",
            snapshot_id=_latest_snapshot(conn, "mta_bus_gtfs_static"),
            source_year="current quarterly schedule",
            measure_type="scheduled_transit_record_count",
            confidence=0.92 if bus_stop_count is not None else None,
            geography_type="deduplicated_name_and_coordinate_points_in_catchment",
            evidence_ids=bus_stops["transit_stop_id"].astype(str).tolist(),
            warnings=[
                "Counts scheduled stop points after exact name-and-rounded-coordinate deduplication across six MTA feeds.",
                "Static GTFS omits most temporary detours and does not measure arrivals, reliability, or observed ridership.",
            ],
        )
    )
    regional_rail_snapshot_id = _latest_snapshot(conn, "regional_mta_rail_gtfs_static")
    regional_rail_stops = _point_frame(
        conn,
        "transit_stop",
        "transit_stop_id",
        geometry,
        where="source_id='regional_mta_rail_gtfs_static'",
        columns="transit_stop_id, source_stop_id, name, longitude, latitude, snapshot_id",
    )
    metrics.append(
        MetricValue(
            "regional_rail_station_count",
            float(len(regional_rail_stops)) if regional_rail_snapshot_id is not None else None,
            unit="scheduled_parent_stations",
            source_id="regional_mta_rail_gtfs_static",
            snapshot_id=regional_rail_snapshot_id,
            source_year="current timetable",
            measure_type="scheduled_transit_record_count",
            confidence=0.95 if regional_rail_snapshot_id is not None else None,
            geography_type="parent_station_points_in_catchment",
            evidence_ids=regional_rail_stops["transit_stop_id"].astype(str).tolist(),
            warnings=[
                "MTA static GTFS represents scheduled stations, not observed service, reliability, ridership, or unique passengers.",
                "NJT Port Jervis/Pascack Valley, PATH and CTtransit feeds are not included because their credential or license gates were not accepted on the user's behalf.",
            ],
        )
    )
    regional_transit_places = _point_frame(
        conn,
        "poi",
        "poi_id",
        geometry,
        where=(
            "source_id='overture_places' AND basic_category IN ("
            "'train_station','public_transit_facility_or_service',"
            "'rail_facility_or_service','ferry_service')"
        ),
        columns="poi_id, basic_category, longitude, latitude, snapshot_id",
    )
    overture_snapshot_id = _latest_snapshot(conn, "overture_places")
    metrics.append(
        MetricValue(
            "regional_transit_poi_count",
            float(len(regional_transit_places)) if overture_snapshot_id is not None else None,
            unit="compiled_place_points",
            source_id="overture_places",
            snapshot_id=overture_snapshot_id,
            source_year="current Overture release",
            measure_type="compiled_place_proxy_count",
            confidence=0.6 if overture_snapshot_id is not None else None,
            geography_type="transit_category_place_points_in_catchment",
            evidence_ids=regional_transit_places["poi_id"].astype(str).tolist(),
            warnings=[
                "Overture transit-place categories are a compiled POI proxy, not an operator-authoritative station inventory.",
                "Records can be duplicated, stale, incompletely categorized, or absent; this metric does not measure schedules, service frequency, accessibility, reliability, or ridership.",
            ],
        )
    )
    citibike_snapshot_id = _latest_snapshot(conn, "citibike_gbfs_current")
    citibike_stations = _point_frame(
        conn,
        "transit_stop",
        "transit_stop_id",
        geometry,
        where="source_id='citibike_gbfs_current'",
        columns="transit_stop_id, source_stop_id, longitude, latitude, snapshot_id",
    )
    citibike_status = _point_frame(
        conn,
        "mobility_proxy",
        "mobility_proxy_id",
        geometry,
        where=(
            "source_id='citibike_gbfs_current' "
            "AND proxy_type IN ('citibike_bikes_available','citibike_docks_available')"
        ),
        columns=(
            "mobility_proxy_id, geography_id, proxy_type, value, longitude, latitude, "
            "snapshot_id, period"
        ),
    )

    def current_citibike_supply(proxy_type: str) -> float | None:
        if citibike_snapshot_id is None:
            return None
        values = pd.to_numeric(
            citibike_status.loc[citibike_status["proxy_type"] == proxy_type, "value"],
            errors="coerce",
        )
        return float(values.fillna(0).clip(lower=0).sum())

    status_periods = sorted(
        {
            str(value)
            for value in citibike_status.get("period", pd.Series(dtype=str)).dropna().tolist()
        }
    )
    citibike_warning = [
        "Citi Bike availability is a point-in-time operational snapshot and can change within seconds.",
        "Counts are station-level supply near the site, not completed trips, unique riders, or forecast demand.",
    ]
    metrics.extend(
        [
            MetricValue(
                "citibike_station_count",
                float(len(citibike_stations)) if citibike_snapshot_id is not None else None,
                unit="stations",
                source_id="citibike_gbfs_current",
                snapshot_id=citibike_snapshot_id,
                source_year="current GBFS snapshot",
                measure_type="published_bikeshare_station_count",
                confidence=0.98 if citibike_snapshot_id is not None else None,
                geography_type="station_points_in_catchment",
                evidence_ids=citibike_stations["transit_stop_id"].astype(str).tolist(),
                warnings=[
                    "Published station locations are current network infrastructure, not evidence of station use or trip demand."
                ],
            ),
            MetricValue(
                "citibike_bikes_available_current",
                current_citibike_supply("citibike_bikes_available"),
                unit="bikes",
                source_id="citibike_gbfs_current",
                snapshot_id=citibike_snapshot_id,
                source_year=status_periods[-1] if status_periods else "current GBFS snapshot",
                measure_type="real_time_station_status_sum",
                confidence=0.9 if citibike_snapshot_id is not None else None,
                geography_type="station_points_in_catchment",
                evidence_ids=citibike_status.loc[
                    citibike_status["proxy_type"] == "citibike_bikes_available",
                    "mobility_proxy_id",
                ].astype(str).tolist(),
                warnings=citibike_warning,
            ),
            MetricValue(
                "citibike_docks_available_current",
                current_citibike_supply("citibike_docks_available"),
                unit="docks",
                source_id="citibike_gbfs_current",
                snapshot_id=citibike_snapshot_id,
                source_year=status_periods[-1] if status_periods else "current GBFS snapshot",
                measure_type="real_time_station_status_sum",
                confidence=0.9 if citibike_snapshot_id is not None else None,
                geography_type="station_points_in_catchment",
                evidence_ids=citibike_status.loc[
                    citibike_status["proxy_type"] == "citibike_docks_available",
                    "mobility_proxy_id",
                ].astype(str).tolist(),
                warnings=citibike_warning,
            ),
        ]
    )
    traffic_volume = _point_frame(
        conn,
        "mobility_proxy",
        "mobility_proxy_id",
        geometry,
        where="source_id='nyc_dot_traffic_counts' AND proxy_type='median_vehicle_volume_15min'",
        columns="mobility_proxy_id, geography_id, value, longitude, latitude, snapshot_id",
    )
    pedestrian_counts = _point_frame(
        conn,
        "traffic_observation",
        "traffic_observation_id",
        geometry,
        where="source_id='nyc_dot_pedestrian_counts' AND value IS NOT NULL",
        columns="traffic_observation_id, geography_id, value, longitude, latitude, snapshot_id",
    )
    traffic_speeds = _point_frame(
        conn,
        "traffic_observation",
        "traffic_observation_id",
        geometry,
        where="source_id='nyc_dot_traffic_speed_current' AND value IS NOT NULL",
        columns="traffic_observation_id, geography_id, value, longitude, latitude, snapshot_id",
    )
    mobility_metrics = [
        MetricValue(
            "vehicle_volume_monitor_count",
            float(traffic_volume["geography_id"].nunique()),
            unit="road_segments",
            source_id="nyc_dot_traffic_counts",
            snapshot_id=_latest_snapshot(conn, "nyc_dot_traffic_counts"),
            source_year="2025-present",
            measure_type="observed_site_count",
            confidence=0.85,
            geography_type="monitored_road_segment_points_in_catchment",
            evidence_ids=traffic_volume["mobility_proxy_id"].astype(str).tolist(),
            warnings=["DOT volume monitors are a sparse roadway sample, not complete street coverage."],
        ),
        MetricValue(
            "vehicle_volume_median_15min",
            float(traffic_volume["value"].median()) if not traffic_volume.empty else None,
            unit="vehicles_per_15_minutes",
            source_id="nyc_dot_traffic_counts",
            snapshot_id=_latest_snapshot(conn, "nyc_dot_traffic_counts"),
            source_year="2025-present",
            measure_type="median_of_road_segment_time_medians",
            confidence=0.72 if not traffic_volume.empty else None,
            geography_type="monitored_road_segment_points_in_catchment",
            evidence_ids=traffic_volume["mobility_proxy_id"].astype(str).tolist(),
            warnings=["Median across nearby sampled segments; it is not a unique-vehicle total or neighborhood traffic census."],
        ),
        MetricValue(
            "pedestrian_monitor_count",
            float(pedestrian_counts["geography_id"].nunique()),
            unit="count_sites",
            source_id="nyc_dot_pedestrian_counts",
            snapshot_id=_latest_snapshot(conn, "nyc_dot_pedestrian_counts"),
            source_year="2026-05",
            measure_type="observed_site_count",
            confidence=0.8,
            geography_type="fixed_count_sites_in_catchment",
            evidence_ids=pedestrian_counts["traffic_observation_id"].astype(str).tolist(),
            warnings=["Only 114 selected citywide locations are monitored; zero means no nearby published site, not zero pedestrians."],
        ),
        MetricValue(
            "pedestrian_count_latest_median",
            float(pedestrian_counts["value"].median()) if not pedestrian_counts.empty else None,
            unit="pedestrians_per_published_count_period",
            source_id="nyc_dot_pedestrian_counts",
            snapshot_id=_latest_snapshot(conn, "nyc_dot_pedestrian_counts"),
            source_year="2026-05",
            measure_type="median_published_count",
            confidence=0.68 if not pedestrian_counts.empty else None,
            geography_type="fixed_count_sites_in_catchment",
            evidence_ids=pedestrian_counts["traffic_observation_id"].astype(str).tolist(),
            warnings=["Median of published AM, midday, and PM periods at sparse selected sites; periods are not interchangeable with daily footfall."],
        ),
        MetricValue(
            "traffic_speed_link_count",
            float(traffic_speeds["geography_id"].nunique()),
            unit="monitored_links",
            source_id="nyc_dot_traffic_speed_current",
            snapshot_id=_latest_snapshot(conn, "nyc_dot_traffic_speed_current"),
            source_year="latest refresh snapshot",
            measure_type="sensor_link_count",
            confidence=0.8,
            geography_type="monitored_link_centroids_in_catchment",
            evidence_ids=traffic_speeds["traffic_observation_id"].astype(str).tolist(),
            warnings=["Sensor coverage is concentrated on arterials and highways; link centroids are spatial proxies."],
        ),
        MetricValue(
            "traffic_speed_current_median_mph",
            float(traffic_speeds["value"].median()) if not traffic_speeds.empty else None,
            unit="miles_per_hour",
            source_id="nyc_dot_traffic_speed_current",
            snapshot_id=_latest_snapshot(conn, "nyc_dot_traffic_speed_current"),
            source_year="latest refresh snapshot",
            measure_type="median_real_time_sensor_estimate",
            confidence=0.7 if not traffic_speeds.empty else None,
            geography_type="monitored_link_centroids_in_catchment",
            evidence_ids=traffic_speeds["traffic_observation_id"].astype(str).tolist(),
            warnings=["Point-in-time median across nearby monitored links, not a typical or neighborhood-wide speed."],
        ),
    ]
    metrics.extend(mobility_metrics)

    target_metric_geometry = transform(metric_transform, geometry)
    hazard_definitions = (
        (
            "fema_special_flood_hazard_overlap_pct",
            "fema_nfhl_special_flood_hazard_areas",
            "source_id='fema_nfhl_special_flood_hazard_areas' AND geography_type='fema_nfhl_special_flood_hazard_area'",
            "current FEMA NFHL service snapshot at retrieval time",
            [
                "Includes only NFHL records whose SFHA_TF flag is T and clips retained geometry to the official CSA 408 county union.",
                "The service response is simplified to approximately one-metre tolerance before local projected-area calculations.",
                "NFHL study coverage and effective dates vary by community; consult the official FEMA map service for current determinations.",
            ],
        ),
        (
            "hurricane_evacuation_overlap_pct",
            "nyc_hurricane_evacuation_zones",
            "source_id='nyc_hurricane_evacuation_zones' AND geography_id<>'X'",
            "current published evacuation planning layer",
            [
                "Includes numbered evacuation zones and excludes the source's X area.",
                "An actual evacuation order and current instructions must be confirmed with NYC Emergency Management.",
            ],
        ),
        (
            "future_2050_floodplain_overlap_pct",
            "nyc_future_floodplain_2050s",
            "source_id='nyc_future_floodplain_2050s'",
            "2050s planning scenario",
            [
                "Published 2050s 100-year floodplain scenario based on preliminary FEMA mapping and an NPCC high sea-level-rise projection.",
                "The underlying source warns against using this layer for current risk, insurance ratings, regulation, or site-scale design.",
            ],
        ),
        (
            "sea_level_2100_floodplain_overlap_pct",
            "nyc_sea_level_rise_2100_floodplain",
            "source_id='nyc_sea_level_rise_2100_floodplain'",
            "2100 planning scenario",
            [
                "Published 2100 100-year floodplain scenario using a 90th-percentile 75-inch sea-level-rise projection.",
                "This is a long-range planning scenario, not a prediction of a specific event or a parcel-level risk determination.",
            ],
        ),
    )
    for metric_id, source_id, where, source_year, source_warnings in hazard_definitions:
        snapshot_id = _latest_snapshot(conn, source_id)
        hazard_frame, hazard_geometries = _polygon_frame(
            conn,
            "geo_boundary",
            geometry,
            where=where,
            columns=(
                "boundary_id, geography_id, geom_wkt, min_longitude, min_latitude, "
                "max_longitude, max_latitude, snapshot_id"
            ),
        )
        overlap_pct: float | None = None
        if snapshot_id is not None:
            overlap_pct = 0.0
            if hazard_geometries and target_metric_geometry.area > 0:
                hazard_union = union_all(hazard_geometries)
                overlap_area = target_metric_geometry.intersection(
                    transform(metric_transform, hazard_union)
                ).area
                overlap_pct = float(
                    min(100.0, max(0.0, overlap_area / target_metric_geometry.area * 100.0))
                )
        metrics.append(
            MetricValue(
                metric_id,
                overlap_pct,
                unit="percent_of_catchment_area",
                source_id=source_id,
                snapshot_id=snapshot_id,
                source_year=source_year,
                measure_type="exact_projected_area_overlap",
                confidence=0.82 if snapshot_id is not None else None,
                geography_type="published_planning_polygons_intersecting_catchment",
                evidence_ids=hazard_frame["boundary_id"].astype(str).tolist(),
                warnings=[
                    *source_warnings,
                    "Geometric overlap is a planning-screening signal, not an engineering, insurance, legal, regulatory, or expected-loss conclusion.",
                ],
            )
        )

    tlc_snapshot_id = _latest_snapshot(conn, "tlc_trip_record_monthly_aggregates")
    taxi_zone_snapshot_id = _latest_snapshot(conn, "tlc_taxi_zones")
    taxi_zone_frame, taxi_zone_geometries = _polygon_frame(
        conn,
        "geo_boundary",
        geometry,
        where="source_id='tlc_taxi_zones' AND geography_type='tlc_taxi_zone'",
        columns=(
            "boundary_id, geography_id, geom_wkt, area_m2, min_longitude, min_latitude, "
            "max_longitude, max_latitude, snapshot_id"
        ),
    )
    taxi_zone_weights: dict[str, float] = {}
    for position, zone_geometry in enumerate(taxi_zone_geometries):
        zone_metric = transform(metric_transform, zone_geometry)
        if zone_metric.area <= 0:
            continue
        fraction = target_metric_geometry.intersection(zone_metric).area / zone_metric.area
        if fraction > 0:
            taxi_zone_weights[str(taxi_zone_frame.iloc[position]["geography_id"])] = float(
                min(1.0, max(0.0, fraction))
            )
    if taxi_zone_weights:
        taxi_zone_ids = sorted(taxi_zone_weights)
        placeholders = ",".join("?" for _ in taxi_zone_ids)
        tlc_cells = conn.execute(
            f"""
            SELECT mobility_proxy_id, proxy_type, value, period, geography_id,
                   valid_from, valid_to, snapshot_id
            FROM mobility_proxy
            WHERE source_id='tlc_trip_record_monthly_aggregates'
              AND geography_id IN ({placeholders})
            """,
            taxi_zone_ids,
        ).fetchdf()
    else:
        tlc_cells = pd.DataFrame(
            columns=[
                "mobility_proxy_id",
                "proxy_type",
                "value",
                "period",
                "geography_id",
                "valid_from",
                "valid_to",
                "snapshot_id",
            ]
        )
    if not tlc_cells.empty:
        tlc_cells["zone_weight"] = (
            tlc_cells["geography_id"].astype(str).map(taxi_zone_weights).fillna(0.0)
        )
        tlc_cells["weighted_value"] = (
            pd.to_numeric(tlc_cells["value"], errors="coerce").fillna(0.0)
            * tlc_cells["zone_weight"]
        )
        period_days = int(
            (
                pd.to_datetime(tlc_cells["valid_to"]).max()
                - pd.to_datetime(tlc_cells["valid_from"]).min()
            ).days
            + 1
        )
    else:
        tlc_cells["weighted_value"] = pd.Series(dtype=float)
        period_days = 1

    def tlc_direction_value(direction: str) -> float | None:
        if tlc_snapshot_id is None:
            return None
        mask = tlc_cells["proxy_type"].astype(str).str.endswith(f"_{direction}_trip_count")
        return float(tlc_cells.loc[mask, "weighted_value"].sum())

    tlc_vintage = _latest_vintage(conn, "tlc_trip_record_monthly_aggregates")
    tlc_warning = [
        "Estimate area-weights taxi-zone monthly aggregates under a uniform-within-zone assumption; it is not exact site footfall.",
        "Yellow and green taxi trips are included; other for-hire, transit, walking, cycling, and private-vehicle activity are excluded.",
        "Cells below five trips and source rows without a published taxi-zone geometry were excluded before standardization.",
        "No individual trip rows or linked origin-destination pairs are retained locally.",
    ]
    pickup_monthly = tlc_direction_value("pickup")
    dropoff_monthly = tlc_direction_value("dropoff")
    tlc_evidence = tlc_cells["mobility_proxy_id"].astype(str).tolist()
    metrics.extend(
        [
            MetricValue(
                "tlc_taxi_zone_count",
                float(len(taxi_zone_weights)) if taxi_zone_snapshot_id is not None else None,
                unit="intersecting_taxi_zones",
                source_id="tlc_taxi_zones",
                snapshot_id=taxi_zone_snapshot_id,
                source_year="current published taxi-zone boundaries",
                measure_type="exact_geometry_intersection_count",
                confidence=0.98 if taxi_zone_snapshot_id is not None else None,
                geography_type="taxi_zone_polygons_intersecting_catchment",
                evidence_ids=taxi_zone_frame["boundary_id"].astype(str).tolist(),
                warnings=["Taxi zones are coarse operating geographies, not neighborhoods or exact trip locations."],
            ),
            MetricValue(
                "tlc_pickup_trips_monthly_area_weighted",
                pickup_monthly,
                unit="estimated_trips_per_source_month",
                source_id="tlc_trip_record_monthly_aggregates",
                snapshot_id=tlc_snapshot_id,
                source_year=tlc_vintage,
                measure_type="privacy_guarded_area_weighted_mobility_proxy",
                confidence=0.62 if pickup_monthly is not None else None,
                geography_type="taxi_zone_area_weighted_to_catchment",
                evidence_ids=tlc_evidence,
                warnings=tlc_warning,
            ),
            MetricValue(
                "tlc_dropoff_trips_monthly_area_weighted",
                dropoff_monthly,
                unit="estimated_trips_per_source_month",
                source_id="tlc_trip_record_monthly_aggregates",
                snapshot_id=tlc_snapshot_id,
                source_year=tlc_vintage,
                measure_type="privacy_guarded_area_weighted_mobility_proxy",
                confidence=0.62 if dropoff_monthly is not None else None,
                geography_type="taxi_zone_area_weighted_to_catchment",
                evidence_ids=tlc_evidence,
                warnings=tlc_warning,
            ),
            MetricValue(
                "tlc_pickup_trips_average_daily_area_weighted",
                pickup_monthly / period_days if pickup_monthly is not None else None,
                unit="estimated_trips_per_day",
                source_id="tlc_trip_record_monthly_aggregates",
                snapshot_id=tlc_snapshot_id,
                source_year=tlc_vintage,
                measure_type="derived_daily_area_weighted_mobility_proxy",
                confidence=0.6 if pickup_monthly is not None else None,
                geography_type="taxi_zone_area_weighted_to_catchment",
                evidence_ids=tlc_evidence,
                warnings=tlc_warning,
            ),
            MetricValue(
                "tlc_dropoff_trips_average_daily_area_weighted",
                dropoff_monthly / period_days if dropoff_monthly is not None else None,
                unit="estimated_trips_per_day",
                source_id="tlc_trip_record_monthly_aggregates",
                snapshot_id=tlc_snapshot_id,
                source_year=tlc_vintage,
                measure_type="derived_daily_area_weighted_mobility_proxy",
                confidence=0.6 if dropoff_monthly is not None else None,
                geography_type="taxi_zone_area_weighted_to_catchment",
                evidence_ids=tlc_evidence,
                warnings=tlc_warning,
            ),
        ]
    )

    borough_codes = {"Manhattan": "1", "Bronx": "2", "Brooklyn": "3", "Queens": "4", "Staten Island": "5"}
    borough_code = borough_codes.get(borough or "")
    if borough_code:
        for table, metric_id, name_filter in (
            ("environment_indicator", "pm25_indicator", "%Fine particles%"),
            ("health_indicator", "asthma_health_indicator", "%Asthma emergency department visits%"),
        ):
            row = conn.execute(
                f"""
                SELECT value, unit, period, source_id, snapshot_id, measure_type, confidence,
                       geography_type, geography_id, {table}_id
                FROM {table}
                WHERE lower(geography_type)='borough' AND geography_id=? AND name ILIKE ?
                ORDER BY valid_from DESC NULLS LAST LIMIT 1
                """,
                [borough_code, name_filter],
            ).fetchone()
            if row:
                metrics.append(
                    MetricValue(
                        metric_id,
                        float(row[0]) if row[0] is not None else None,
                        unit=row[1],
                        source_id=row[3],
                        snapshot_id=row[4],
                        source_year=row[2],
                        measure_type=row[5],
                        confidence=row[6],
                        geography_type=row[7],
                        geography_id=row[8],
                        evidence_ids=[row[9]],
                        warnings=["Borough-level area statistic; it is not an attribute of the site, building, or any person."],
                    )
                )

    for metric_id, indicator_id in (
        ("cdc_places_current_asthma_pct", "CASTHMA"),
        ("cdc_places_diagnosed_diabetes_pct", "DIABETES"),
        ("cdc_places_obesity_pct", "OBESITY"),
        ("cdc_places_uninsured_pct", "ACCESS2"),
        ("cdc_places_food_insecurity_pct", "FOODINSECU"),
    ):
        health_source_id = "cdc_places_2025_tract"
        health_source_year = "2023 model estimate / 2025 release"
        value, margin, evidence, confidence = _tract_health_aggregate(
            conn, weights, indicator_id, source_id=health_source_id
        )
        fallback_used = value is None
        if fallback_used:
            health_source_id = "cdc_places_2024_tract_fallback"
            health_source_year = "2022 model estimate / 2024 release fallback"
            value, margin, evidence, confidence = _tract_health_aggregate(
                conn, weights, indicator_id, source_id=health_source_id
            )
        metrics.append(
            MetricValue(
                metric_id,
                value,
                unit="percent",
                source_id=health_source_id,
                snapshot_id=_latest_snapshot(conn, health_source_id),
                source_year=health_source_year,
                measure_type=(
                    "population_weighted_older_small_area_model_fallback"
                    if fallback_used and value is not None
                    else "population_weighted_small_area_model_estimate"
                ),
                confidence=(
                    min(confidence, 0.58 if fallback_used else 0.64)
                    if confidence is not None
                    else None
                ),
                margin_of_error=margin,
                geography_type="population_weighted_intersecting_census_tracts",
                evidence_ids=evidence,
                warnings=[
                    "CDC PLACES tract values are model-based population estimates and are not attributes of this site, any address, household, building or person.",
                    "Catchment value is population-weighted using tract overlap and ACS total population; measure-specific adult denominators differ, so use it as a screening aggregate.",
                    "The displayed uncertainty propagates published tract 95% confidence-interval half-widths under an independence approximation; it is not an official CDC catchment confidence interval.",
                    (
                        "The 2025 release had no usable selected tract estimate for this catchment, so the explicitly labeled 2024 release fallback is shown."
                        if fallback_used and value is not None
                        else "The current 2025 release is used; no older fallback was applied."
                    ),
                ],
            )
        )

    population_for_rate = population or 0
    jobs_for_rate = jobs or 0
    metrics.extend(
        [
            MetricValue(
                "competitors_per_1000_residents",
                (direct / population_for_rate * 1000) if population_for_rate > 0 else None,
                unit="locations_per_1000_residents",
                source_id="derived",
                source_year=f"{competitor_source_year} / ACS 2020-2024",
                measure_type="derived_ratio",
                confidence=min(population_confidence or 0.5, 0.85),
                evidence_ids=sorted(
                    set(competitor_evidence_ids + population_evidence)
                ),
            ),
            MetricValue(
                "competitors_per_1000_jobs",
                (direct / jobs_for_rate * 1000) if jobs_for_rate > 0 else None,
                unit="locations_per_1000_jobs",
                source_id="derived",
                source_year=f"{competitor_source_year} / LODES 2023",
                measure_type="derived_ratio",
                confidence=0.8 if jobs_for_rate > 0 else None,
                evidence_ids=sorted(
                    set(
                        competitor_evidence_ids
                        + (
                            workforce.loc[
                                workforce["measure"] == "C000", "workforce_estimate_id"
                            ].astype(str).tolist()
                            if not workforce.empty
                            else []
                        )
                    )
                ),
            ),
        ]
    )

    if not borough:
        nyc_only_sources = {
            "census_decennial_2020_pl",
            "mappluto_26v2",
            "nyc_rolling_sales",
            "acris_real_property_master_recent",
            "derived_public_transaction_cases",
            "dob_permit_issuance_recent",
            "dob_now_approved_permits_recent",
            "dob_violations_active",
            "dob_safety_violations_active",
            "dob_ecb_violations_active",
            "dob_certificate_occupancy_legacy",
            "dob_now_certificate_occupancy",
            "nyc_311_recent_aggregates",
            "nyc_hotels",
            "nysla_active_licenses",
            "nyc_str_registration",
            "nyc_facilities",
            "mta_subway_stations",
            "mta_subway_entrances_2024",
            "mta_hourly_2025",
            "mta_bus_gtfs_static",
            "citibike_gbfs_current",
            "citibike_monthly_operating_report",
            "nyc_dot_traffic_counts",
            "nyc_dot_pedestrian_counts",
            "nyc_dot_traffic_speed_current",
            "nyc_hurricane_evacuation_zones",
            "nyc_future_floodplain_2050s",
            "nyc_sea_level_rise_2100_floodplain",
            "tlc_taxi_zones",
            "tlc_trip_record_monthly_aggregates",
        }
        coverage_warning = (
            "该来源仅覆盖纽约五区；跨州站点显示为不可用，而不是把边界外无记录解释为零。"
        )
        for metric in metrics:
            if metric.source_id in nyc_only_sources:
                metric.value = None
                metric.text_value = None
                metric.snapshot_id = None
                metric.confidence = None
                metric.evidence_ids = []
                metric.warnings = [*(metric.warnings or []), coverage_warning]

    # Transparent proxy indices. These are not shares and are intentionally not a composite site score.
    resident_index = min(100.0, (population or 0) / 200.0)
    worker_index = min(100.0, (jobs or 0) / 250.0)
    culture_count = len(facility_groups["culture_and_library_count"])
    if borough:
        visitor_index = min(
            100.0,
            len(hotels) * 6.0
            + len(short_term_rentals) * 2.0
            + len(attractions) * 2.0
            + culture_count * 1.5
            + math.log1p(annual_entries or 0) * 2.0
            + math.log1p(
                ((pickup_monthly or 0.0) + (dropoff_monthly or 0.0)) / max(1, period_days)
            )
            * 2.0,
        )
        nightlife_index = min(100.0, len(nightlife) * 4.0 + len(licensed_businesses) * 0.75)
        transit_index: float | None = min(
            100.0, len(complex_ids) * 18.0 + math.log1p(annual_entries or 0) * 2.0
        )
    else:
        visitor_index = min(
            100.0,
            len(attractions) * 2.0
            + len(regional_poi_groups["regional_hotel_poi_count"]) * 2.0,
        )
        nightlife_index = min(100.0, len(nightlife) * 4.0)
        transit_evidence_available = (
            regional_rail_snapshot_id is not None or overture_snapshot_id is not None
        )
        transit_index = (
            min(
                100.0,
                len(regional_rail_stops) * 20.0 + len(regional_transit_places) * 4.0,
            )
            if transit_evidence_available
            else None
        )
    index_values = {
        "resident_index": resident_index,
        "worker_index": worker_index,
        "visitor_proxy_index": visitor_index,
        "nightlife_index": nightlife_index,
        "transit_hub_index": transit_index,
    }
    index_components = {
        "resident_index": ["resident_population"],
        "worker_index": ["daytime_jobs"],
        "visitor_proxy_index": [
            "hotel_count",
            "legal_short_term_rental_count",
            "attraction_count",
            "culture_and_library_count",
            "subway_entries_2025",
            "tlc_pickup_trips_average_daily_area_weighted",
            "tlc_dropoff_trips_average_daily_area_weighted",
        ],
        "nightlife_index": ["nightlife_location_count", "alcohol_license_count"],
        "transit_hub_index": ["subway_complex_count", "subway_entries_2025"],
    }
    if not borough:
        index_components["visitor_proxy_index"] = [
            "attraction_count",
            "regional_hotel_poi_count",
        ]
        index_components["nightlife_index"] = ["nightlife_location_count"]
        index_components["transit_hub_index"] = [
            "regional_rail_station_count",
            "regional_transit_poi_count",
        ]
    metric_lookup = {metric.metric_id: metric for metric in metrics}

    def component_evidence(metric_ids: list[str]) -> list[str]:
        return sorted(
            {
                evidence_id
                for metric_id in metric_ids
                for evidence_id in (
                    metric_lookup.get(metric_id).evidence_ids
                    if metric_lookup.get(metric_id) is not None
                    and metric_lookup.get(metric_id).evidence_ids is not None
                    else []
                )
            }
        )

    for metric_id, value in index_values.items():
        metrics.append(
            MetricValue(
                metric_id,
                value,
                unit="index_0_100",
                source_id="derived",
                source_year="mixed latest local snapshots; component years in evidence",
                measure_type="model_proxy",
                confidence=0.55 if value is not None else None,
                evidence_ids=component_evidence(index_components[metric_id]),
                warnings=[
                    f"Component metrics: {json_dumps(index_components[metric_id])}",
                    "Transparent uncalibrated proxy index; not a population share or composite site score.",
                ],
            )
        )
    ranked = sorted(
        ((metric_id, value) for metric_id, value in index_values.items() if value is not None),
        key=lambda pair: pair[1],
        reverse=True,
    )
    if population is None or jobs is None or len(ranked) < 2:
        audience = "证据不足"
    elif ranked[0][1] < 35 or ranked[0][1] < ranked[1][1] * 1.25:
        audience = "混合型"
    else:
        audience = {
            "resident_index": "居民主导",
            "worker_index": "上班族主导",
            "visitor_proxy_index": "游客主导",
            "nightlife_index": "夜生活主导",
            "transit_hub_index": "交通换乘型",
        }[ranked[0][0]]
    metrics.append(
        MetricValue(
            "audience_type",
            text_value=audience,
            source_id="derived",
            source_year="mixed latest local snapshots; component years in evidence",
            measure_type="model_classification",
            confidence=0.55,
            evidence_ids=sorted(
                {
                    evidence_id
                    for component_ids in index_components.values()
                    for evidence_id in component_evidence(component_ids)
                }
            ),
            warnings=[
                f"Compared proxy indices: {json_dumps(index_values)}",
                "Classification uses open-data proxies and does not claim exact audience percentages.",
            ],
        )
    )
    return metrics


def persist_metrics(
    conn: duckdb.DuckDBPyConnection,
    *,
    site_id: str,
    catchment_id: str,
    calculation_version_id: str,
    metrics: list[MetricValue],
) -> None:
    conn.execute("DELETE FROM site_metric WHERE site_id=? AND catchment_id=?", [site_id, catchment_id])
    rows = []
    for metric in metrics:
        rows.append(
            {
                "site_metric_id": new_id("metric"),
                "site_id": site_id,
                "catchment_id": catchment_id,
                "metric_id": metric.metric_id,
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
                "calculation_version_id": calculation_version_id,
                "evidence_record_ids_json": json_dumps(metric.evidence_ids or []),
                "warnings_json": json_dumps(metric.warnings or []),
                "created_at": utc_now(),
            }
        )
    if rows:
        frame = pd.DataFrame(rows)
        conn.register("_site_metrics", frame)
        conn.execute("INSERT INTO site_metric BY NAME SELECT * FROM _site_metrics")
        conn.unregister("_site_metrics")
