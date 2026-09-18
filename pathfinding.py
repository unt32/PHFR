"""
pathfinding.py
---------------
Self-contained A* pathfinding module for time-weighted road-network routing.

Works with any networkx Graph/DiGraph/MultiGraph/MultiDiGraph (e.g. an
OSMnx-style graph) where:
  - each node carries 'x' (lon) / 'y' (lat) attributes (or 'lon'/'lat')
  - each edge carries precomputed route weights such as 'travel_time'

Public API
----------
haversine_distance_m(lat1, lon1, lat2, lon2) -> float
astar_path(graph, source, target, ...)     -> list[node_id]

Exceptions
----------
RouteNotFoundError       - no path connects source and target
MissingCoordinatesError  - source/target node has no usable lon/lat
"""

import heapq
import math

from edge_weights import (
    DEFAULT_SPEED_BY_HIGHWAY_KPH,
    MAX_PLAUSIBLE_SPEED_MPS,
    MAX_PLAUSIBLE_SPEED_KPH,
    UNIVERSAL_DEFAULT_SPEED_KPH,
    GraphAttributeAnnotator,
    InvalidEdgeWeightError,
    validate_edge_weight,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RouteNotFoundError(Exception):
    """Raised when no path exists between the requested nodes."""


class MissingCoordinatesError(Exception):
    """Raised when a node lacks the lon/lat data the A* heuristic needs."""


def parse_maxspeed_kph(raw):
    """Backward-compatible wrapper for maxspeed parsing."""
    return GraphAttributeAnnotator.parse_maxspeed_kph(raw)


def speed_kph_for_edge(edge_data, speed_by_highway=None, default_kph=UNIVERSAL_DEFAULT_SPEED_KPH):
    """Backward-compatible wrapper for resolving an edge's speed."""
    return GraphAttributeAnnotator(speed_by_highway, default_kph).speed_kph_for_edge(edge_data)


def add_travel_times(graph, speed_by_highway=None, default_kph=UNIVERSAL_DEFAULT_SPEED_KPH):
    """Backward-compatible wrapper for graph edge annotation."""
    return GraphAttributeAnnotator(speed_by_highway, default_kph).annotate(graph)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

EARTH_RADIUS_M = 6_371_008.8  # mean Earth radius, metres


def haversine_distance_m(lat1, lon1, lat2, lon2):
    """Great-circle distance between two lon/lat points, in metres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = phi2 - phi1
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _node_lonlat(graph, node):
    """Return (lon, lat) for a node, or None if coordinates are missing."""
    data = graph.nodes[node]
    lon = data.get("x", data.get("lon"))
    lat = data.get("y", data.get("lat"))
    if lon is None or lat is None:
        return None
    return float(lon), float(lat)


# ---------------------------------------------------------------------------
# A*
# ---------------------------------------------------------------------------

def _min_weight_edge_data(graph, u, v, weight):
    """Return the parallel edge (u -> v) with the smallest `weight`.

    Works for simple graphs (edge_data is already an attribute dict) and
    MultiGraph/MultiDiGraph (edge_data is {edge_key: attribute_dict}).
    """
    edge_data = graph[u][v]
    if graph.is_multigraph():
        def safe_weight(data):
            try:
                value = float(data[weight])
            except (KeyError, TypeError, ValueError):
                return float("inf")
            return value if math.isfinite(value) and value >= 0 else float("inf")

        return min(edge_data.values(), key=safe_weight)
    return edge_data


def astar_path(graph, source, target, weight="travel_time", max_speed_mps=MAX_PLAUSIBLE_SPEED_MPS):
    """A* shortest path from `source` to `target`, minimizing `weight`.

    Parameters
    ----------
    graph : networkx Graph / DiGraph / MultiGraph / MultiDiGraph
        Nodes need 'x'/'y' (or 'lon'/'lat'). Edges need the `weight`
        attribute. Graphs loaded through MapEngine are annotated before
        routing.
    source, target : node id
    weight : str
        Edge attribute to minimize.
        - "travel_time" (seconds): heuristic = straight_line_m / max_speed_mps
        - "length" (metres): heuristic = straight_line_m
        - anything else: heuristic = 0 (degrades gracefully to Dijkstra,
          since a zero heuristic is always admissible)
    max_speed_mps : float
        Fastest speed any edge could plausibly have, in m/s. Must be >= the
        network's actual fastest edge speed, or the heuristic can overshoot
        and A* may return a suboptimal (non-fastest) route.

    Returns
    -------
    list of node ids, source through target inclusive.

    Raises
    ------
    KeyError
        `source` or `target` is not in the graph.
    InvalidEdgeWeightError
        An edge on the frontier is missing the requested `weight` attribute or
        has a non-finite/negative value.
    MissingCoordinatesError
        `source` or `target` lacks lon/lat coordinates, which the
        heuristic is measured against.
    RouteNotFoundError
        No path connects `source` and `target`.
    """
    if source not in graph:
        raise KeyError(f"Source node not in graph: {source!r}")
    if target not in graph:
        raise KeyError(f"Target node not in graph: {target!r}")
    if source == target:
        return [source]

    target_lonlat = _node_lonlat(graph, target)
    if target_lonlat is None or _node_lonlat(graph, source) is None:
        raise MissingCoordinatesError(
            "Source or target node is missing 'x'/'y' (lon/lat) coordinates, "
            "which the A* distance heuristic requires."
        )
    target_lon, target_lat = target_lonlat

    if weight == "length":
        distance_to_time = None  # heuristic stays in metres, no conversion
    elif weight == "travel_time":
        distance_to_time = max_speed_mps
    else:
        distance_to_time = False  # sentinel: unknown units -> zero heuristic

    def heuristic(node):
        if distance_to_time is False:
            return 0.0
        lonlat = _node_lonlat(graph, node)
        if lonlat is None:
            # A node in the interior of the search without coordinates can't
            # break correctness, only the tightness of the estimate for that
            # node -> degrade to Dijkstra there instead of failing the route.
            return 0.0
        lon, lat = lonlat
        distance_m = haversine_distance_m(lat, lon, target_lat, target_lon)
        return distance_m if distance_to_time is None else distance_m / distance_to_time

    counter = 0  # heapq tie-breaker so node ids are never compared directly
    open_set = [(heuristic(source), counter, source)]
    came_from = {}
    g_score = {source: 0.0}
    visited = set()

    while open_set:
        _, _, current = heapq.heappop(open_set)

        if current == target:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path

        if current in visited:
            continue
        visited.add(current)

        for neighbor in graph[current]:
            if neighbor in visited:
                continue

            edge_data = _min_weight_edge_data(graph, current, neighbor, weight)
            tentative_g = g_score[current] + validate_edge_weight(
                edge_data, weight, current, neighbor
            )

            if tentative_g < g_score.get(neighbor, float("inf")):
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g
                counter += 1
                heapq.heappush(open_set, (tentative_g + heuristic(neighbor), counter, neighbor))

    raise RouteNotFoundError(f"No path exists between {source!r} and {target!r}.")
