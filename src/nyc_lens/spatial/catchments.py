from __future__ import annotations

import math
import os
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb
import httpx
import networkx as nx
from pyproj import Transformer
from shapely import concave_hull
from shapely.geometry import LineString, MultiPoint, Point
from shapely.ops import transform, unary_union

from ..config import geographies_config
from ..db import begin_run, finish_run
from ..provenance import save_bytes_snapshot


@lru_cache(maxsize=4)
def _transformers(projected_crs: str) -> tuple[Any, Any]:
    return (
        Transformer.from_crs("EPSG:4326", projected_crs, always_xy=True).transform,
        Transformer.from_crs(projected_crs, "EPSG:4326", always_xy=True).transform,
    )


def analysis_crs_for_point(longitude: float, latitude: float) -> str:
    configured = geographies_config()
    bbox = configured["nyc_bbox"]
    if bbox["west"] <= longitude <= bbox["east"] and bbox["south"] <= latitude <= bbox["north"]:
        return configured["analysis_crs"]
    return configured["regional_analysis_crs"]


def metric_transform_for_geometry(geometry: Any) -> Any:
    center = geometry.centroid
    projected_crs = analysis_crs_for_point(float(center.x), float(center.y))
    return _transformers(projected_crs)[0]


@dataclass(frozen=True)
class NetworkExtract:
    graph: nx.Graph
    snapshot_id: str
    node_count: int
    edge_count: int


def _network_from_payload(payload: dict[str, Any], snapshot_id: str) -> NetworkExtract:
    nodes: dict[int, tuple[float, float]] = {}
    ways: list[dict[str, Any]] = []
    for element in payload.get("elements", []):
        if element.get("type") == "node" and "lon" in element and "lat" in element:
            nodes[int(element["id"])] = (float(element["lon"]), float(element["lat"]))
        elif element.get("type") == "way":
            ways.append(element)
    graph = nx.Graph()
    for node_id, (node_lon, node_lat) in nodes.items():
        graph.add_node(node_id, lon=node_lon, lat=node_lat)
    for way in ways:
        node_ids = way.get("nodes", [])
        tags = way.get("tags", {})
        for left, right in zip(node_ids, node_ids[1:]):
            if left not in nodes or right not in nodes:
                continue
            left_lon, left_lat = nodes[left]
            right_lon, right_lat = nodes[right]
            length = _haversine_m(left_lon, left_lat, right_lon, right_lat)
            if length <= 0:
                continue
            if graph.has_edge(left, right):
                graph[left][right]["length_m"] = min(graph[left][right]["length_m"], length)
            else:
                graph.add_edge(
                    left,
                    right,
                    length_m=length,
                    way_id=str(way.get("id")),
                    highway=tags.get("highway"),
                )
    if not graph.nodes or not graph.edges:
        raise ValueError("The OSM extract returned no usable walking-network edges.")
    return NetworkExtract(
        graph=graph,
        snapshot_id=snapshot_id,
        node_count=graph.number_of_nodes(),
        edge_count=graph.number_of_edges(),
    )


def metric_buffer(longitude: float, latitude: float, distance_m: float):
    forward, reverse = _transformers(analysis_crs_for_point(longitude, latitude))
    point_metric = transform(forward, Point(longitude, latitude))
    return transform(reverse, point_metric.buffer(distance_m, quad_segs=32))


def _haversine_m(a_lon: float, a_lat: float, b_lon: float, b_lat: float) -> float:
    radius = 6_371_008.8
    a_lat_r, b_lat_r = math.radians(a_lat), math.radians(b_lat)
    delta_lat = b_lat_r - a_lat_r
    delta_lon = math.radians(b_lon - a_lon)
    value = math.sin(delta_lat / 2) ** 2 + math.cos(a_lat_r) * math.cos(b_lat_r) * math.sin(delta_lon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(value))


def fetch_walk_network(
    conn: duckdb.DuckDBPyConnection,
    *,
    site_id: str,
    longitude: float,
    latitude: float,
    radius_m: int = 1700,
) -> NetworkExtract:
    source_id = "osm_walk_network"
    run_id = begin_run(conn, source_id, "walking network extract")
    cached_rows = conn.execute(
        """
        SELECT snapshot_id, raw_path, query_json
        FROM dataset_snapshot
        WHERE source_id=? AND status='SUCCEEDED'
        ORDER BY retrieved_at DESC
        """,
        [source_id],
    ).fetchall()
    for cached_snapshot_id, raw_path, query_json in cached_rows:
        query_payload = json.loads(query_json) if isinstance(query_json, str) else query_json
        center = (query_payload or {}).get("center")
        cached_radius = (query_payload or {}).get("radius_m")
        if (
            center
            and cached_radius is not None
            and float(cached_radius) >= radius_m
            and _haversine_m(longitude, latitude, float(center[0]), float(center[1])) <= 20
            and Path(raw_path).is_file()
        ):
            payload = json.loads(Path(raw_path).read_text(encoding="utf-8"))
            network = _network_from_payload(payload, cached_snapshot_id)
            finish_run(
                conn,
                run_id,
                "SKIPPED_NOT_DUE",
                warnings=[f"Reused local OSM network snapshot {cached_snapshot_id}."],
            )
            return network
    configured_endpoint = os.getenv("NYC_LENS_OVERPASS_URL")
    endpoints = [configured_endpoint] if configured_endpoint else [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.private.coffee/api/interpreter",
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    ]
    query = f"""
[out:json][timeout:120];
way(around:{radius_m},{latitude:.7f},{longitude:.7f})
  ["highway"]
  ["highway"!~"motorway|motorway_link|trunk|trunk_link|raceway|construction|proposed"]
  ["access"!="private"]
  ["foot"!="no"];
(._;>;);
out body;
    """.strip()
    try:
        response = None
        attempted: list[str] = []
        errors: list[str] = []
        for endpoint in endpoints:
            if not endpoint:
                continue
            attempted.append(endpoint)
            try:
                candidate = httpx.post(
                    endpoint,
                    data={"data": query},
                    timeout=180,
                    follow_redirects=True,
                    headers={"User-Agent": os.getenv("NYC_LENS_USER_AGENT", "NYC-Lens/0.1 local-research")},
                )
                if candidate.status_code == 429 or candidate.status_code >= 500:
                    errors.append(f"{endpoint}: HTTP {candidate.status_code}")
                    continue
                candidate.raise_for_status()
                response = candidate
                break
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                errors.append(f"{endpoint}: {type(exc).__name__}")
        if response is None:
            raise RuntimeError("All bounded Overpass endpoints failed: " + "; ".join(errors))
        snapshot = save_bytes_snapshot(
            conn,
            source_id=source_id,
            vintage="query_time",
            filename=f"{site_id}_walk_network.json",
            content=response.content,
            source_url=str(response.url),
            run_id=run_id,
            content_type="application/json",
            query={"overpass_ql": query, "center": [longitude, latitude], "radius_m": radius_m, "attempted_endpoints": attempted},
            warnings=[
                "OpenStreetMap ODbL attribution and derivative database obligations apply.",
                *errors,
            ],
            http_status=response.status_code,
        )
        network = _network_from_payload(response.json(), snapshot.snapshot_id)
        finish_run(
            conn,
            run_id,
            "SUCCEEDED",
            request_count=len(attempted),
            downloaded_bytes=snapshot.byte_size,
            inserted_records=network.edge_count,
        )
        return network
    except Exception as exc:
        finish_run(conn, run_id, "FAILED", error={"type": type(exc).__name__, "message": str(exc)})
        raise


def walk_isochrone(
    network: NetworkExtract,
    *,
    longitude: float,
    latitude: float,
    cutoff_m: float,
):
    graph = network.graph
    origin = min(
        graph.nodes,
        key=lambda node_id: _haversine_m(
            longitude,
            latitude,
            graph.nodes[node_id]["lon"],
            graph.nodes[node_id]["lat"],
        ),
    )
    distances = nx.single_source_dijkstra_path_length(graph, origin, cutoff=cutoff_m, weight="length_m")
    reached = set(distances)
    if len(reached) < 3:
        raise ValueError(f"Only {len(reached)} walking-network nodes were reachable within {cutoff_m} m.")
    forward, reverse = _transformers(analysis_crs_for_point(longitude, latitude))
    node_points_metric = [
        transform(forward, Point(graph.nodes[node]["lon"], graph.nodes[node]["lat"]))
        for node in reached
    ]
    edge_lines_metric = []
    for left, right in graph.edges:
        if left in reached and right in reached:
            line = LineString(
                [
                    (graph.nodes[left]["lon"], graph.nodes[left]["lat"]),
                    (graph.nodes[right]["lon"], graph.nodes[right]["lat"]),
                ]
            )
            edge_lines_metric.append(transform(forward, line))
    corridor = unary_union([line.buffer(22, cap_style="round") for line in edge_lines_metric])
    hull = concave_hull(MultiPoint(node_points_metric), ratio=0.28, allow_holes=False).buffer(18)
    polygon_metric = unary_union([corridor, hull]).buffer(0)
    if polygon_metric.geom_type == "MultiPolygon":
        polygon_metric = max(polygon_metric.geoms, key=lambda value: value.area)
    return transform(reverse, polygon_metric)


def catchment_area_m2(geometry) -> float:
    return float(transform(metric_transform_for_geometry(geometry), geometry).area)
