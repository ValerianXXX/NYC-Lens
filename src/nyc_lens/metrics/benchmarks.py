from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any, Callable

import duckdb
import numpy as np
import pandas as pd
from shapely import contains_xy, from_wkt

from ..db import begin_run, finish_run
from ..provenance import referenced_snapshot_set, save_bytes_snapshot
from ..spatial.catchments import metric_buffer
from ..util import json_dumps, utc_now
from ..ingest.common import stable_id, standardized_path, update_snapshot_details
from .engine import _acs_context, _decennial_context, calculate_metrics


BENCHMARK_SCOPE = "nyc_citywide_h3_r10_stratified"
BENCHMARK_METHOD_VERSION = "0.2.0"
BENCHMARK_SAMPLE_VERSION = "0.1.0"
RADIAL_CATCHMENTS = {"250m": 250.0, "500m": 500.0, "1km": 1000.0}
Progress = Callable[[int, int, str], None]


def _deterministic_borough_sample(
    conn: duckdb.DuckDBPyConnection,
    sample_per_borough: int,
) -> pd.DataFrame:
    cells = conn.execute(
        """
        SELECT cell_id, centroid_lon, centroid_lat
        FROM geo_cell
        WHERE source_id='derived_h3_grid' AND h3_resolution=10
        """
    ).fetchdf()
    boroughs = conn.execute(
        """
        SELECT name, geom_wkt
        FROM geo_boundary
        WHERE source_id='nyc_borough_boundaries' AND geography_type='borough'
        ORDER BY geography_id
        """
    ).fetchall()
    if len(cells) < 40_000 or len(boroughs) != 5:
        raise ValueError("Complete NYC H3 and borough layers are required before benchmark generation")
    cells["borough"] = None
    longitude = cells["centroid_lon"].to_numpy(float)
    latitude = cells["centroid_lat"].to_numpy(float)
    for borough_name, geometry_wkt in boroughs:
        mask = contains_xy(from_wkt(geometry_wkt), longitude, latitude)
        cells.loc[mask, "borough"] = borough_name
    cells = cells.loc[cells["borough"].notna()].copy()
    cells["sample_key"] = cells["cell_id"].map(
        lambda cell: hashlib.sha256(f"{BENCHMARK_SAMPLE_VERSION}|{cell}".encode("utf-8")).hexdigest()
    )
    selected = (
        cells.sort_values(["borough", "sample_key"])
        .groupby("borough", as_index=False, group_keys=False)
        .head(sample_per_borough)
        .reset_index(drop=True)
    )
    counts = selected.groupby("borough").size().to_dict()
    if len(counts) != 5 or any(count != sample_per_borough for count in counts.values()):
        raise ValueError(f"Unable to select the requested borough-stratified benchmark sample: {counts}")
    return selected


def _quantile_frame(
    observations: pd.DataFrame,
    *,
    benchmark_run_id: str,
    benchmark_snapshot_id: str,
    snapshot_set_json: str,
    created_at: Any,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    quantiles = np.linspace(0.0, 1.0, 101)
    for (catchment_type, metric_id), group in observations.groupby(["catchment_type", "metric_id"]):
        values = group["value"].to_numpy(float)
        source_ids = sorted({str(value) for value in group["source_id"].dropna().unique()})
        for quantile, value in zip(quantiles, np.quantile(values, quantiles)):
            records.append(
                {
                    "benchmark_quantile_id": stable_id(
                        "benchq", benchmark_run_id, catchment_type, metric_id, f"{quantile:.2f}"
                    ),
                    "benchmark_run_id": benchmark_run_id,
                    "benchmark_scope": BENCHMARK_SCOPE,
                    "catchment_type": catchment_type,
                    "metric_id": metric_id,
                    "quantile": float(quantile),
                    "value": float(value),
                    "sample_size": len(values),
                    "geography_basis": "deterministic borough-stratified H3 resolution 10 cell centers",
                    "source_ids_json": json_dumps(source_ids),
                    "snapshot_set_json": snapshot_set_json,
                    "source_id": "derived_citywide_benchmark",
                    "snapshot_id": benchmark_snapshot_id,
                    "measure_type": "empirical_spatial_quantile",
                    "created_at": created_at,
                    "method_version": BENCHMARK_METHOD_VERSION,
                }
            )
    return pd.DataFrame(records)


def refresh_citywide_benchmarks(
    conn: duckdb.DuckDBPyConnection,
    *,
    sample_per_borough: int = 50,
    workers: int = 8,
    progress: Progress | None = None,
) -> dict[str, Any]:
    if sample_per_borough < 20:
        raise ValueError("sample_per_borough must be at least 20")
    if workers < 1:
        raise ValueError("workers must be at least 1")
    source_id = "derived_citywide_benchmark"
    run_id = begin_run(conn, source_id, "deterministic citywide radial benchmark")
    try:
        selected = _deterministic_borough_sample(conn, sample_per_borough)
        snapshot_set = referenced_snapshot_set(conn)
        snapshot_set_json = json_dumps(snapshot_set)
        derivation = {
            "benchmark_scope": BENCHMARK_SCOPE,
            "method_version": BENCHMARK_METHOD_VERSION,
            "sample_version": BENCHMARK_SAMPLE_VERSION,
            "sample_per_borough": sample_per_borough,
            "sample_size": len(selected),
            "workers": workers,
            "catchments": RADIAL_CATCHMENTS,
            "sampling": "lowest deterministic SHA-256 keys within each borough among H3 resolution 10 land-center cells",
            "sample_cells": selected[["cell_id", "borough", "centroid_lon", "centroid_lat"]].to_dict("records"),
            "snapshot_set": snapshot_set,
            "limitations": [
                "Radial catchments only; walking-network catchments are not assigned a benchmark percentile.",
                "Spatial reference distribution, not a calibrated site score or causal opportunity model.",
            ],
        }
        snapshot = save_bytes_snapshot(
            conn,
            source_id=source_id,
            vintage=BENCHMARK_METHOD_VERSION,
            filename="benchmark_design.json",
            content=json.dumps(derivation, ensure_ascii=False, indent=2).encode("utf-8"),
            source_url="https://h3geo.org/",
            run_id=run_id,
            content_type="application/json",
            query={key: value for key, value in derivation.items() if key != "sample_cells"},
            warnings=derivation["limitations"],
        )
        created_at = utc_now()
        total_cells = len(selected)
        selected_rows = list(selected.itertuples(index=False))
        progress_lock = Lock()
        completed_cells = 0

        def calculate_chunk(rows: list[Any]) -> list[dict[str, Any]]:
            nonlocal completed_cells
            worker_conn = conn.cursor()
            try:
                context = _acs_context(worker_conn)
                decennial_context = _decennial_context(worker_conn)
                chunk_records: list[dict[str, Any]] = []
                for row in rows:
                    for catchment_type, distance_m in RADIAL_CATCHMENTS.items():
                        geometry = metric_buffer(
                            float(row.centroid_lon), float(row.centroid_lat), distance_m
                        )
                        metrics = calculate_metrics(
                            worker_conn,
                            geometry,
                            borough=str(row.borough),
                            business_type="full_service_restaurant",
                            acs_context=context,
                            decennial_context=decennial_context,
                        )
                        for metric in metrics:
                            if metric.value is None:
                                continue
                            value = float(metric.value)
                            if not np.isfinite(value):
                                continue
                            chunk_records.append(
                                {
                                    "benchmark_observation_id": stable_id(
                                        "bench",
                                        run_id,
                                        row.cell_id,
                                        catchment_type,
                                        metric.metric_id,
                                    ),
                                    "benchmark_run_id": run_id,
                                    "benchmark_scope": BENCHMARK_SCOPE,
                                    "sample_cell_id": row.cell_id,
                                    "borough": row.borough,
                                    "catchment_type": catchment_type,
                                    "metric_id": metric.metric_id,
                                    "value": value,
                                    "unit": metric.unit,
                                    "percentile": None,
                                    "source_id": metric.source_id,
                                    "snapshot_id": metric.snapshot_id,
                                    "source_year": metric.source_year,
                                    "measure_type": metric.measure_type,
                                    "confidence": metric.confidence,
                                    "snapshot_set_json": snapshot_set_json,
                                    "created_at": created_at,
                                    "method_version": BENCHMARK_METHOD_VERSION,
                                }
                            )
                    with progress_lock:
                        completed_cells += 1
                        current_progress = completed_cells
                    if progress:
                        progress(current_progress, total_cells, f"{row.borough} {row.cell_id}")
                return chunk_records
            finally:
                worker_conn.close()

        worker_count = min(workers, total_cells)
        chunks = [selected_rows[index::worker_count] for index in range(worker_count)]
        if worker_count == 1:
            record_chunks = [calculate_chunk(chunks[0])]
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                record_chunks = list(executor.map(calculate_chunk, chunks))
        records = [record for chunk in record_chunks for record in chunk]
        observations = pd.DataFrame(records)
        expected_pairs = len(selected) * len(RADIAL_CATCHMENTS)
        observed_pairs = len(observations[["sample_cell_id", "catchment_type"]].drop_duplicates())
        if observed_pairs != expected_pairs:
            raise ValueError(f"Benchmark cell/catchment coverage mismatch: {observed_pairs}/{expected_pairs}")
        observations["percentile"] = observations.groupby(
            ["catchment_type", "metric_id"]
        )["value"].rank(method="average", pct=True) * 100.0
        quantiles = _quantile_frame(
            observations,
            benchmark_run_id=run_id,
            benchmark_snapshot_id=snapshot.snapshot_id,
            snapshot_set_json=snapshot_set_json,
            created_at=created_at,
        )
        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM benchmark_observation WHERE benchmark_scope=?", [BENCHMARK_SCOPE])
            conn.execute("DELETE FROM benchmark_quantile WHERE benchmark_scope=?", [BENCHMARK_SCOPE])
            conn.register("_benchmark_observations", observations)
            conn.register("_benchmark_quantiles", quantiles)
            conn.execute("INSERT INTO benchmark_observation BY NAME SELECT * FROM _benchmark_observations")
            conn.execute("INSERT INTO benchmark_quantile BY NAME SELECT * FROM _benchmark_quantiles")
            conn.unregister("_benchmark_observations")
            conn.unregister("_benchmark_quantiles")
            conn.execute("COMMIT")
        except Exception:
            for name in ("_benchmark_observations", "_benchmark_quantiles"):
                try:
                    conn.unregister(name)
                except Exception:
                    pass
            conn.execute("ROLLBACK")
            raise
        observations.to_parquet(
            standardized_path(source_id, BENCHMARK_METHOD_VERSION, "observations.parquet"),
            index=False,
        )
        quantiles.to_parquet(
            standardized_path(source_id, BENCHMARK_METHOD_VERSION, "quantiles.parquet"),
            index=False,
        )
        update_snapshot_details(
            conn,
            snapshot.snapshot_id,
            observations,
            warnings=derivation["limitations"],
        )
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=0,
            downloaded_bytes=0,
            inserted_records=len(observations) + len(quantiles),
            warnings=[
                f"Built {len(observations)} numeric observations and {len(quantiles)} quantile rows from {len(selected)} centers.",
                *derivation["limitations"],
            ],
        )
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "benchmark_scope": BENCHMARK_SCOPE,
            "sample_centers": len(selected),
            "sample_per_borough": sample_per_borough,
            "workers": worker_count,
            "catchment_types": list(RADIAL_CATCHMENTS),
            "observation_rows": len(observations),
            "quantile_rows": len(quantiles),
            "numeric_metrics": observations["metric_id"].nunique(),
            "snapshot_sources": len(snapshot_set),
        }
    except Exception as exc:
        finish_run(
            conn,
            run_id,
            "FAILED",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        raise
