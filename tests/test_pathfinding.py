import networkx as nx
import pytest

from app.core.exceptions import MissingCoordinatesError, RouteNotFoundError
from app.core.pathfinding import astar_path, haversine_distance_m


def _toy_graph() -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=0.0, y=0.0)
    graph.add_node(2, x=0.0, y=0.01)
    graph.add_node(3, x=0.0, y=0.02)
    # A slower direct edge and a faster two-hop route, so a naive
    # shortest-hop-count search would pick the wrong path.
    graph.add_edge(1, 2, travel_time=10.0, length=100.0)
    graph.add_edge(2, 3, travel_time=10.0, length=100.0)
    graph.add_edge(1, 3, travel_time=50.0, length=500.0)
    return graph


def test_astar_prefers_the_faster_route():
    graph = _toy_graph()
    assert astar_path(graph, 1, 3, weight="travel_time") == [1, 2, 3]


def test_astar_returns_single_node_when_source_equals_target():
    graph = _toy_graph()
    assert astar_path(graph, 1, 1) == [1]


def test_astar_raises_route_not_found_for_disconnected_nodes():
    graph = _toy_graph()
    graph.add_node(4, x=1.0, y=1.0)
    with pytest.raises(RouteNotFoundError):
        astar_path(graph, 1, 4)


def test_astar_raises_key_error_for_unknown_nodes():
    graph = _toy_graph()
    with pytest.raises(KeyError):
        astar_path(graph, 1, 999)


def test_astar_raises_missing_coordinates_error():
    graph = _toy_graph()
    graph.add_node(5)  # no x/y
    graph.add_edge(1, 5, travel_time=1.0, length=10.0)
    with pytest.raises(MissingCoordinatesError):
        astar_path(graph, 1, 5)


def test_haversine_distance_is_zero_for_identical_points():
    assert haversine_distance_m(45.0, 28.0, 45.0, 28.0) == 0.0


def test_haversine_distance_is_positive_for_distinct_points():
    assert haversine_distance_m(45.0, 28.0, 46.0, 29.0) > 0.0
