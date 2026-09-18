"""
pathfinding.py
---------------
Self-contained A* pathfinding module for time-weighted road-network routing.

Works with any networkx Graph/DiGraph/MultiGraph/MultiDiGraph (e.g. an
OSMnx-style graph) where:
  - each node carries 'x' (lon) / 'y' (lat) attributes (or 'lon'/'lat')
  - each edge carries 'length' (metres) and, optionally, 'maxspeed' /
    'highway' OSM tags

Public API
----------
parse_maxspeed_kph(raw)                    -> float | None
speed_kph_for_edge(edge_data, ...)         -> float
add_travel_times(graph, ...)               -> graph (mutated in-place)
haversine_distance_m(lat1, lon1, lat2, lon2) -> float
astar_path(graph, source, target, ...)     -> list[node_id]

Exceptions
----------
RouteNotFoundError       - no path connects source and target
MissingCoordinatesError  - source/target node has no usable lon/lat
"""

import heapq
import math
import re


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RouteNotFoundError(Exception):
    """Raised when no path exists between the requested nodes."""


class MissingCoordinatesError(Exception):
    """Raised when a node lacks the lon/lat data the A* heuristic needs."""


# ---------------------------------------------------------------------------
# Speeds
# ---------------------------------------------------------------------------

# Typical speeds (km/h) by OSM `highway` road class. Used whenever an edge's
# `maxspeed` tag is missing or unparseable.
DEFAULT_SPEED_BY_HIGHWAY_KPH = {
    "motorway": 110,
    "motorway_link": 80,
    "trunk": 90,
    "trunk_link": 70,
    "primary": 60,
    "primary_link": 50,
    "secondary": 50,
    "secondary_link": 40,
    "tertiary": 40,
    "tertiary_link": 30,
    "unclassified": 20,
    "residential": 20,
    "living_street": 10,
    "service": 10,
    "track": 5,
    "path": 3,
}

# Fallback for any `highway` value not covered above (or absent entirely).
# Guarantees every edge ends up with a valid, non-zero speed.
UNIVERSAL_DEFAULT_SPEED_KPH = 30.0

# Fastest speed any edge in the network could plausibly have. Used to turn
# straight-line distance into a travel-time *lower bound* for the A*
# heuristic, so it never overestimates true remaining travel time
# (admissibility) and is consistent along every edge.
MAX_PLAUSIBLE_SPEED_KPH = 130.0
MAX_PLAUSIBLE_SPEED_MPS = MAX_PLAUSIBLE_SPEED_KPH / 3.6

_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")


def parse_maxspeed_kph(raw):
    """Parse an OSM-style `maxspeed` value into a float km/h, or None.

    Handles the messy shapes `maxspeed` shows up in once loaded from OSM:
      - plain numeric strings: "50"
      - strings with units: "50 km/h", "30 mph", "50kmh"
      - non-numeric placeholders: "walk", "none", "signals", "variable"
      - lists of any of the above (a way tagged with multiple limits) ->
        the *minimum* parsed value is used, since that's the binding limit
      - NaN / None / already-numeric (int/float) values

    Returns None if nothing usable could be extracted, so the caller can
    fall back to a road-class based default instead.
    """
    if raw is None:
        return None

    if isinstance(raw, float) and math.isnan(raw):
        return None

    if isinstance(raw, (int, float)):
        value = float(raw)
        return value if value > 0 else None

    if isinstance(raw, (list, tuple, set)):
        parsed = [parse_maxspeed_kph(item) for item in raw]
        parsed = [p for p in parsed if p is not None]
        return min(parsed) if parsed else None

    if isinstance(raw, str):
        text = raw.strip().lower()
        if not text or text in {"none", "signals", "walk", "variable"}:
            return None
        match = _NUMBER_RE.search(text)
        if not match:
            return None
        value = float(match.group(1))
        if "mph" in text:
            value *= 1.60934
        # Anything else ("km/h", "kmh", "kph", or a bare number) is already km/h.
        return value if value > 0 else None

    return None


def speed_kph_for_edge(edge_data, speed_by_highway=None, default_kph=UNIVERSAL_DEFAULT_SPEED_KPH):
    """Resolve one edge's travel speed in km/h.

    Priority: parsed `maxspeed` tag -> road-class default for its `highway`
    tag -> universal fallback. Always returns a positive, non-zero value.
    """
    speed_by_highway = speed_by_highway or DEFAULT_SPEED_BY_HIGHWAY_KPH

    parsed = parse_maxspeed_kph(edge_data.get("maxspeed"))
    if parsed:
        return parsed

    highway = edge_data.get("highway")
    highways = highway if isinstance(highway, (list, tuple)) else [highway]
    for h in highways:
        if h in speed_by_highway:
            return speed_by_highway[h]

    return default_kph


def add_travel_times(graph, speed_by_highway=None, default_kph=UNIVERSAL_DEFAULT_SPEED_KPH):
    """Attach `speed_kph` and `travel_time` (seconds) to every edge, in-place.

        travel_time = length_in_meters / speed_in_meters_per_second

    An edge with no usable `length` is treated as 0 m (travel_time 0 s)
    rather than raising - a single malformed length shouldn't block routing
    on every other edge. Returns the same graph object for chaining.
    """
    for _, _, data in graph.edges(data=True):
        speed_kph = speed_kph_for_edge(data, speed_by_highway, default_kph)
        speed_mps = speed_kph * 1000.0 / 3600.0
        length_m = data.get("length") or 0.0
        data["speed_kph"] = speed_kph
        data["travel_time"] = length_m / speed_mps if speed_mps > 0 else 0.0
    return graph


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
        return min(edge_data.values(), key=lambda d: d.get(weight, float("inf")))
    return edge_data


def astar_path(graph, source, target, weight="travel_time", max_speed_mps=MAX_PLAUSIBLE_SPEED_MPS):
    """A* shortest path from `source` to `target`, minimizing `weight`.

    Parameters
    ----------
    graph : networkx Graph / DiGraph / MultiGraph / MultiDiGraph
        Nodes need 'x'/'y' (or 'lon'/'lat'). Edges need the `weight`
        attribute - call `add_travel_times(graph)` first if using the
        default weight="travel_time".
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
        `source`/`target` isn't in the graph, or an edge on the frontier is
        missing the `weight` attribute (run `add_travel_times` first).
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
            if weight not in edge_data:
                raise KeyError(
                    f"Edge {current!r} -> {neighbor!r} has no '{weight}' attribute; "
                    "call add_travel_times(graph) first if using weight='travel_time'."
                )
            tentative_g = g_score[current] + edge_data[weight]

            if tentative_g < g_score.get(neighbor, float("inf")):
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g
                counter += 1
                heapq.heappush(open_set, (tentative_g + heuristic(neighbor), counter, neighbor))

    raise RouteNotFoundError(f"No path exists between {source!r} and {target!r}.")
