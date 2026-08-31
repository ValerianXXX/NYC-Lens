import math

import networkx as nx
import pytest
from shapely.geometry import Point

from nyc_lens.spatial.catchments import (
    NetworkExtract,
    catchment_area_m2,
    metric_buffer,
    walk_isochrone,
)


def test_metric_buffer_uses_projected_meters() -> None:
    polygon = metric_buffer(-73.9862, 40.7567, 500)
    assert polygon.is_valid
    assert catchment_area_m2(polygon) == pytest.approx(math.pi * 500**2, rel=0.01)


def test_walk_isochrone_is_network_derived_not_radial() -> None:
    graph = nx.Graph()
    origin_lon, origin_lat = -73.9862, 40.7567
    for row in range(5):
        for column in range(5):
            node = row * 5 + column
            graph.add_node(
                node,
                lon=origin_lon + (column - 2) * 0.001,
                lat=origin_lat + (row - 2) * 0.001,
            )
            if column:
                graph.add_edge(node - 1, node, length_m=100)
            if row:
                graph.add_edge(node - 5, node, length_m=100)
    network = NetworkExtract(graph=graph, snapshot_id="fixture", node_count=25, edge_count=40)
    polygon = walk_isochrone(network, longitude=origin_lon, latitude=origin_lat, cutoff_m=220)
    radial = metric_buffer(origin_lon, origin_lat, 220)
    assert polygon.is_valid and not polygon.is_empty
    assert polygon.contains(Point(origin_lon, origin_lat))
    assert abs(catchment_area_m2(polygon) - catchment_area_m2(radial)) > 1_000
