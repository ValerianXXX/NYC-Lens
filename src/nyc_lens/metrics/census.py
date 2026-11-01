"""Source-scoped Census/health aggregates with complete, traceable inputs.

These are spatial screening estimates, not address-level observations. The
four-item result contract is kept for the engine and benchmark callers.
"""
from __future__ import annotations

import math
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from shapely import from_wkt
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform
from shapely.strtree import STRtree

from ..spatial.catchments import metric_transform_for_geometry

Aggregate = tuple[float | None, float | None, list[str], float | None]
ACS_SOURCE = "acs_2024_5yr"
BG_SOURCE = "tiger_bg_2024"


def _note(diagnostics: list[str] | None, message: str) -> None:
    if diagnostics is not None and message not in diagnostics:
        diagnostics.append(message)


def _ids(frame: pd.DataFrame, column: str) -> list[str]:
    return frame[column].dropna().astype(str).tolist() if column in frame else []


def _unavailable(evidence: list[str]) -> Aggregate:
    return None, None, list(dict.fromkeys(evidence)), None


def _complete_rows(
    frame: pd.DataFrame, geoids: list[str], column: str, label: str,
    diagnostics: list[str] | None, *, positive: bool = False,
) -> bool:
    """Require exactly one usable observation per expected geography."""
    if frame["geography_id"].duplicated().any():
        _note(diagnostics, f"Unavailable: {label} has duplicate active geography records.")
        return False
    if set(frame["geography_id"].astype(str)) != set(geoids):
        _note(diagnostics, f"Unavailable: {label} is missing one or more required geographies.")
        return False
    values = pd.to_numeric(frame[column], errors="coerce")
    if not (np.isfinite(values) & (values > 0 if positive else values >= 0)).all():
        _note(diagnostics, f"Unavailable: {label} contains missing or unusable values.")
        return False
    return True


def _usable_weights(weights: pd.DataFrame, diagnostics: list[str] | None) -> bool:
    if weights.empty:
        _note(diagnostics, "Unavailable: no source boundary intersects the catchment.")
        return False
    if not _complete_rows(weights, _ids(weights, "geography_id"), "fraction",
                          "boundary overlap", diagnostics, positive=True):
        return False
    if (weights["fraction"] > 1).any():
        _note(diagnostics, "Unavailable: a boundary overlap fraction exceeds one.")
        return False
    return True


def _acs_context(conn: duckdb.DuckDBPyConnection) -> tuple[pd.DataFrame, list[BaseGeometry], STRtree] | None:
    boundaries = conn.execute("""
        SELECT boundary_id, geography_id, geom_wkt, area_m2, snapshot_id
        FROM geo_boundary WHERE geography_type='block_group' AND source_id=?
        """, [BG_SOURCE]).fetchdf()
    if boundaries.empty:
        return None
    geometries = list(from_wkt(boundaries["geom_wkt"].to_numpy()))
    return boundaries, geometries, STRtree(geometries)


def _decennial_context(conn: duckdb.DuckDBPyConnection) -> tuple[pd.DataFrame, list[BaseGeometry], STRtree] | None:
    boundaries = conn.execute("""
        SELECT boundary_id, geography_id, geom_wkt, area_m2, snapshot_id
        FROM geo_boundary
        WHERE geography_type='census_block' AND source_id='nyc_census_blocks_2020'
        """).fetchdf()
    if boundaries.empty:
        return None
    geometries = list(from_wkt(boundaries["geom_wkt"].to_numpy()))
    return boundaries, geometries, STRtree(geometries)


def _acs_weights(
    geometry: BaseGeometry, context: tuple[pd.DataFrame, list[BaseGeometry], STRtree],
) -> pd.DataFrame:
    boundaries, geometries, tree = context
    metric_transform = metric_transform_for_geometry(geometry)
    target_metric = transform(metric_transform, geometry)
    rows: list[dict[str, Any]] = []
    for index in tree.query(geometry, predicate="intersects"):
        boundary_metric = transform(metric_transform, geometries[int(index)])
        if boundary_metric.area <= 0:
            continue
        intersection_area = target_metric.intersection(boundary_metric).area
        if intersection_area <= 0:
            continue
        row = boundaries.iloc[int(index)]
        rows.append({
            "geography_id": row["geography_id"], "boundary_id": row["boundary_id"],
            "fraction": min(1.0, intersection_area / boundary_metric.area),
            "intersection_area_m2": intersection_area, "boundary_area_m2": boundary_metric.area,
        })
    return pd.DataFrame(rows, columns=["geography_id", "boundary_id", "fraction",
                                       "intersection_area_m2", "boundary_area_m2"])


def _estimates(conn: duckdb.DuckDBPyConnection, geoids: list[str], variable: str,
               kind: str, source: str = ACS_SOURCE) -> pd.DataFrame:
    placeholders = ",".join("?" for _ in geoids)
    return conn.execute(f"""
        SELECT census_estimate_id, geography_id, estimate, margin_of_error, confidence
        FROM census_estimate WHERE source_id=? AND geography_type=? AND variable=?
          AND geography_id IN ({placeholders})
        """, [source, kind, variable, *geoids]).fetchdf()


def _tract_overlap(weights: pd.DataFrame) -> pd.DataFrame:
    return weights.assign(geography_id=weights["geography_id"].astype(str).str[:11]).groupby(
        "geography_id", as_index=False)["intersection_area_m2"].sum()


def _tract_weights(conn: duckdb.DuckDBPyConnection, weights: pd.DataFrame,
                   evidence: list[str], diagnostics: list[str] | None) -> tuple[pd.DataFrame, list[str]] | None:
    overlap = _tract_overlap(weights)
    tract_ids = _ids(overlap, "geography_id")
    placeholders = ",".join("?" for _ in tract_ids)
    boundaries = conn.execute(f"""
        SELECT boundary_id, geography_id, area_m2 FROM geo_boundary
        WHERE geography_type='block_group' AND source_id=?
          AND substr(geography_id, 1, 11) IN ({placeholders})
        """, [BG_SOURCE, *tract_ids]).fetchdf()
    evidence.extend(_ids(boundaries, "boundary_id"))
    bg_ids = _ids(boundaries, "geography_id")
    if not set(_ids(weights, "geography_id")) <= set(bg_ids):
        _note(diagnostics, "Unavailable: tract area denominators are missing an intersecting block group.")
        return None
    if not _complete_rows(boundaries, bg_ids, "area_m2", "tract area denominator",
                          diagnostics, positive=True):
        return None
    areas = boundaries.assign(geography_id=boundaries["geography_id"].str[:11]).groupby(
        "geography_id", as_index=False)["area_m2"].sum()
    overlap = overlap.merge(areas, on="geography_id", how="left", validate="one_to_one")
    overlap["fraction"] = (overlap["intersection_area_m2"] / overlap["area_m2"]).clip(upper=1.0)
    return overlap, bg_ids


def _margin(values: pd.Series, factors: pd.Series, diagnostics: list[str] | None) -> float | None:
    margins = pd.to_numeric(values, errors="coerce")
    # Missing uncertainty must not turn into a zero contribution to precision.
    used = factors > 0
    if not (np.isfinite(margins[used]) & (margins[used] >= 0)).all():
        _note(diagnostics, "Uncertainty unavailable: at least one contributing input lacks a usable published margin of error.")
        return None
    return float(np.sqrt(((margins[used] * factors[used]) ** 2).sum()))


def _acs_aggregate(
    conn: duckdb.DuckDBPyConnection, weights: pd.DataFrame, variable: str, *,
    median: bool = False, diagnostics: list[str] | None = None,
) -> Aggregate:
    evidence = _ids(weights, "boundary_id")
    if not _usable_weights(weights, diagnostics):
        return _unavailable(evidence)
    geoids = _ids(weights, "geography_id")
    estimates = _estimates(conn, geoids, variable, "block_group")
    working = weights[["geography_id", "fraction"]].copy()
    population_geoids = geoids
    tract = estimates.empty
    if tract:
        # Published tract-only tables retain the existing allocation method.
        # Every denominator block group is an input, including those outside
        # the catchment. A partial BG table is never mixed with tract estimates.
        geoids = sorted({geoid[:11] for geoid in geoids})
        estimates = _estimates(conn, geoids, variable, "tract")
        evidence.extend(_ids(estimates, "census_estimate_id"))
        if estimates.empty:
            _note(diagnostics, f"Unavailable: ACS {variable} has no selected block-group or tract observations.")
            return _unavailable(evidence)
        allocation = _tract_weights(conn, weights, evidence, diagnostics)
        if allocation is None:
            return _unavailable(evidence)
        working, population_geoids = allocation
        _note(diagnostics, f"ACS {variable} uses published tract estimates allocated by block-group overlap.")
    evidence.extend(_ids(estimates, "census_estimate_id"))
    if not _complete_rows(estimates, geoids, "estimate", f"ACS {variable}", diagnostics):
        return _unavailable(evidence)
    values = estimates.merge(working[["geography_id", "fraction"]], on="geography_id", validate="one_to_one")
    factors = values["fraction"]
    if median:
        population = _estimates(conn, population_geoids, "B01003_001", "block_group")
        evidence.extend(_ids(population, "census_estimate_id"))
        if not _complete_rows(population, population_geoids, "estimate", "ACS population weights", diagnostics):
            return _unavailable(evidence)
        if tract:
            population = population.assign(geography_id=population["geography_id"].str[:11]).groupby(
                "geography_id", as_index=False)["estimate"].sum()
        population = population.rename(columns={"estimate": "population"})
        values = values.merge(population[["geography_id", "population"]], on="geography_id", validate="one_to_one")
        factors = values["population"] * values["fraction"]
        if factors.sum() <= 0:
            _note(diagnostics, "Unavailable: total observed population weight is zero; no area-only substitute is used.")
            return _unavailable(evidence)
        factors = factors / factors.sum()
    value = float((values["estimate"] * factors).sum())
    margin = _margin(values["margin_of_error"], factors, diagnostics)
    confidence = float(np.average(values["confidence"].fillna(0.5), weights=values["fraction"]))
    return value, margin, list(dict.fromkeys(evidence)), confidence


def _tract_health_aggregate(
    conn: duckdb.DuckDBPyConnection, weights: pd.DataFrame, indicator_id: str, *,
    source_id: str = "cdc_places_2025_tract", diagnostics: list[str] | None = None,
) -> Aggregate:
    evidence = _ids(weights, "boundary_id")
    if not _usable_weights(weights, diagnostics):
        return _unavailable(evidence)
    overlap = _tract_overlap(weights)
    tract_ids = _ids(overlap, "geography_id")
    placeholders = ",".join("?" for _ in tract_ids)
    areas = conn.execute(f"""
        SELECT boundary_id, geography_id, area_m2 FROM geo_boundary
        WHERE source_id='tiger_tract_2024' AND geography_type='tract_2024'
          AND geography_id IN ({placeholders})
        """, tract_ids).fetchdf()
    health = conn.execute(f"""
        SELECT health_indicator_id, geography_id, value, margin_of_error, confidence
        FROM health_indicator WHERE source_id=? AND indicator_id=?
          AND geography_id IN ({placeholders})
        """, [source_id, indicator_id, *tract_ids]).fetchdf()
    population = _estimates(conn, tract_ids, "B01003_001", "tract")
    evidence.extend(_ids(areas, "boundary_id"))
    evidence.extend(_ids(health, "health_indicator_id"))
    evidence.extend(_ids(population, "census_estimate_id"))
    valid = [
        _complete_rows(areas, tract_ids, "area_m2", "health tract area denominator", diagnostics, positive=True),
        _complete_rows(health, tract_ids, "value", f"{source_id} {indicator_id}", diagnostics),
        _complete_rows(population, tract_ids, "estimate", "ACS tract population weights", diagnostics),
    ]
    if not all(valid):
        return _unavailable(evidence)
    overlap = overlap.merge(areas, on="geography_id", validate="one_to_one")
    overlap["fraction"] = (overlap["intersection_area_m2"] / overlap["area_m2"]).clip(upper=1.0)
    values = health.merge(overlap[["geography_id", "fraction"]], on="geography_id", validate="one_to_one")
    values = values.merge(population[["geography_id", "estimate"]], on="geography_id", validate="one_to_one")
    factors = values["estimate"] * values["fraction"]
    if factors.sum() <= 0:
        _note(diagnostics, "Unavailable: total observed population weight is zero; no area-only substitute is used.")
        return _unavailable(evidence)
    factors = factors / factors.sum()
    value = float((values["value"] * factors).sum())
    margin = _margin(values["margin_of_error"], factors, diagnostics)
    confidence = float(np.average(values["confidence"].fillna(0.5), weights=factors))
    return value, margin, list(dict.fromkeys(evidence)), confidence


def _decennial_aggregate(
    conn: duckdb.DuckDBPyConnection, weights: pd.DataFrame, variable: str, *,
    diagnostics: list[str] | None = None,
) -> tuple[float | None, list[str]]:
    evidence = _ids(weights, "boundary_id")
    if not _usable_weights(weights, diagnostics):
        return None, evidence
    geoids = _ids(weights, "geography_id")
    estimates = _estimates(conn, geoids, variable, "census_block", "census_decennial_2020_pl")
    evidence.extend(_ids(estimates, "census_estimate_id"))
    evidence = list(dict.fromkeys(evidence))
    if not _complete_rows(estimates, geoids, "estimate", f"2020 Census {variable}", diagnostics):
        return None, evidence
    values = estimates.merge(weights[["geography_id", "fraction"]], on="geography_id", validate="one_to_one")
    return float((values["estimate"] * values["fraction"]).sum()), evidence


def _sum_components(results: list[Aggregate], diagnostics: list[str] | None = None) -> Aggregate:
    evidence = list(dict.fromkeys(item for result in results for item in result[2]))
    if not results or any(result[0] is None for result in results):
        _note(diagnostics, "Unavailable: a required Census component is missing; a partial total is not reported.")
        return _unavailable(evidence)
    value = sum(result[0] for result in results if result[0] is not None)
    margin = math.sqrt(sum(result[1] ** 2 for result in results if result[1] is not None)) if all(
        result[1] is not None for result in results) else None
    confidences = [result[3] for result in results if result[3] is not None]
    return value, margin, evidence, float(np.mean(confidences)) if confidences else None


def _sum_age_variables(
    conn: duckdb.DuckDBPyConnection, weights: pd.DataFrame, lines: list[int], *,
    diagnostics: list[str] | None = None,
) -> Aggregate:
    return _sum_components([_acs_aggregate(conn, weights, f"B01001_{line:03d}", diagnostics=diagnostics)
                            for line in lines], diagnostics)

