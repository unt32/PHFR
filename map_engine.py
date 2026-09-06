"""
map_engine.py
--------------
Core map & routing engine. Wraps `osmnx` (OSM data loading) and `networkx`
(graph algorithms) so the UI layer never has to touch either library
directly. Designed to be safely called from a background QThread.
"""

import heapq
import math
import os

import networkx as nx
import osmnx as ox

# Quieter console + cache downloaded data between runs for speed.
ox.settings.log_console = False
ox.settings.use_cache = True

NETWORK_TYPE = "drive"


class RouteNotFoundError(Exception):
    """Raised when no path exists between the selected nodes."""


class MapEngine:
    """Holds the current street-network graph and exposes loading/routing helpers.

    The graph is always kept unprojected (EPSG:4326, i.e. plain lon/lat as the
    node 'x'/'y' attributes). This keeps click-coordinates on the matplotlib
    canvas directly comparable to node coordinates without extra projection.
    """

    def __init__(self):
        self.graph = None  # networkx.MultiDiGraph
        self.graph_source = None  # human-readable description of how it was loaded

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_from_file(self, file_path: str):
        """Load a graph from a local .osm (XML) or .osm.pbf file (drive network)."""
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        lower = file_path.lower()
        if lower.endswith(".osm"):
            G = ox.graph_from_xml(file_path, simplify=True)
        elif lower.endswith(".pbf"):
            try:
                G = self._load_pbf(file_path)
            except ImportError as exc:
                raise ImportError(
                    "Loading .osm.pbf files requires the optional 'pyrosm' package.\n"
                    "Install it with:  pip install pyrosm"
                ) from exc
        else:
            raise ValueError(
                "Unsupported file type. Please choose a .osm or .osm.pbf file."
            )

        self._finalize_graph(G, f"File: {os.path.basename(file_path)}")
        return self.graph

    @staticmethod
    def _load_pbf(file_path: str):
        """Convert a .osm.pbf file into a routable networkx graph via pyrosm."""
        from pyrosm import OSM  # optional dependency, imported lazily

        osm = OSM(file_path)
        nodes, edges = osm.get_network(nodes=True, network_type=NETWORK_TYPE)
        G = osm.to_graph(nodes, edges, graph_type="networkx")
        return G

    def _finalize_graph(self, G, source_desc: str):
        """Attach speed / travel-time edge attributes needed for time-based routing.

        Real-world OSM `maxspeed` tags are messy (missing, numeric-only,
        lists, or NaN once loaded into a GeoDataFrame), and osmnx's
        `add_edge_speeds` does regex/string parsing on that tag. That can
        raise things like "expected string or bytes-like object, got
        'float'" on certain places. We sanitize the tag first, and if the
        library call still fails for any reason, fall back to a manual
        default-speed calculation so the app never crashes on load.
        """
        self.graph = G
        self.graph_source = source_desc
        self._add_speeds()
        self._add_travel_times()

    def _add_speeds(self, default_kph: float = 30.0):
        self._sanitize_maxspeed_tags()
        try:
            self.graph = ox.add_edge_speeds(self.graph)
        except Exception:
            # Retry once more after sanitizing again (defensive), then fall
            # back to assigning a flat default speed to every edge.
            try:
                self._sanitize_maxspeed_tags()
                self.graph = ox.add_edge_speeds(self.graph)
            except Exception:
                for _, _, _, data in self.graph.edges(keys=True, data=True):
                    data.setdefault("speed_kph", default_kph)

    def _add_travel_times(self):
        try:
            self.graph = ox.add_edge_travel_times(self.graph)
        except Exception:
            # Manual fallback: travel_time (s) = length (m) / speed (m/s)
            for _, _, _, data in self.graph.edges(keys=True, data=True):
                length_m = data.get("length", 0.0) or 0.0
                speed_kph = data.get("speed_kph", 30.0) or 30.0
                speed_mps = speed_kph * 1000.0 / 3600.0
                data["travel_time"] = length_m / speed_mps if speed_mps > 0 else 0.0

    def _sanitize_maxspeed_tags(self):
        """Ensure every edge's 'maxspeed' tag is either a clean string, a
        list of clean strings, or absent — never a bare NaN float, which is
        what trips up osmnx's internal string parsing."""
        for _, _, _, data in self.graph.edges(keys=True, data=True):
            maxspeed = data.get("maxspeed")
            if maxspeed is None:
                continue
            if isinstance(maxspeed, float):
                if math.isnan(maxspeed):
                    del data["maxspeed"]
                else:
                    data["maxspeed"] = str(maxspeed)
            elif isinstance(maxspeed, list):
                cleaned = [
                    str(m)
                    for m in maxspeed
                    if not (isinstance(m, float) and math.isnan(m))
                ]
                if cleaned:
                    data["maxspeed"] = cleaned
                else:
                    del data["maxspeed"]

    # ------------------------------------------------------------------
    # Geometry / lookups
    # ------------------------------------------------------------------

    def has_graph(self) -> bool:
        return self.graph is not None

    def nearest_node(self, lon: float, lat: float):
        """Return the graph node nearest to the given (lon, lat) coordinate."""
        if not self.has_graph():
            raise RuntimeError("No graph loaded yet.")
        try:
            return ox.distance.nearest_nodes(self.graph, lon, lat)
        except ImportError as exc:
            raise ImportError(
                "Finding the nearest map node requires the 'scikit-learn' package, "
                "which is missing.\n\nInstall it with:  pip install scikit-learn\n"
                "then restart the app."
            ) from exc

    def node_xy(self, node_id):
        """Return (lon, lat) for a node id."""
        data = self.graph.nodes[node_id]
        return data["x"], data["y"]

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def compute_route(self, orig_node, dest_node, weight: str = "length"):
        """Compute the shortest path between two nodes.

        `weight` should be 'length' (meters, i.e. shortest distance) or
        'travel_time' (seconds, i.e. fastest route). networkx's `shortest_path`
        uses Dijkstra's algorithm under the hood for weighted graphs.
        """
        if not self.has_graph():
            raise RuntimeError("No graph loaded yet.")
        if orig_node == dest_node:
            raise RouteNotFoundError("Start and end points are the same node.")
        try:
            route = astar_path(self.graph, orig_node, dest_node, weight=weight)
        except nx.NetworkXNoPath as exc:
            raise RouteNotFoundError(
                "No path exists between the selected start and end points."
            ) from exc
        except nx.NodeNotFound as exc:
            raise RouteNotFoundError(str(exc)) from exc
        return route

    def route_stats(self, route):
        """Return a dict with distance_km, time_min and node_count for a route."""
        G = self.graph
        length_m = 0.0
        for u, v in zip(route[:-1], route[1:]):
            # A MultiDiGraph may have several parallel edges between u and v;
            # take the shortest one, matching what shortest_path would use.
            candidates = G.get_edge_data(u, v).values()
            best = min(candidates, key=lambda d: d.get("length", float("inf")))
            length_m += best.get("length", 0.0)
        return {
            "distance_km": length_m / 1000.0,
            "node_count": len(route),
        }


def astar_path(graph, orig_node, dest_node, weight="length"):
    """
    A* shortest path algorithm, drop-in replacement for
    nx.shortest_path(graph, orig_node, dest_node, weight=weight).

    Assumes each node has 'x'/'y' (or 'lon'/'lat') attributes for the
    heuristic. Falls back to a zero heuristic (i.e. plain Dijkstra)
    if coordinates aren't available.
    """

    def heuristic(u, v):
        nu = graph.nodes[u]
        nv = graph.nodes[v]
        try:
            ux, uy = nu.get("x", nu.get("lon")), nu.get("y", nu.get("lat"))
            vx, vy = nv.get("x", nv.get("lon")), nv.get("y", nv.get("lat"))
            if ux is None or uy is None or vx is None or vy is None:
                return 0.0
            return math.hypot(ux - vx, uy - vy)
        except Exception:
            return 0.0

    # Priority queue entries: (f_score, counter, node)
    counter = 0
    open_set = [(heuristic(orig_node, dest_node), counter, orig_node)]
    came_from = {}

    g_score = {orig_node: 0.0}
    closed = set()

    while open_set:
        _, _, current = heapq.heappop(open_set)

        if current == dest_node:
            # Reconstruct path
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path

        if current in closed:
            continue
        closed.add(current)

        # Works for both Graph/DiGraph and MultiGraph/MultiDiGraph
        neighbors = graph[current]
        for neighbor, edge_data in neighbors.items():
            if neighbor in closed:
                continue

            # For MultiGraph/MultiDiGraph, edge_data is a dict of parallel edges;
            # pick the one with the lowest weight.
            if (
                isinstance(edge_data, dict)
                and all(isinstance(v, dict) for v in edge_data.values())
                and edge_data
                and not weight in edge_data
            ):
                edge_weight = min(d.get(weight, 1) for d in edge_data.values())
            else:
                edge_weight = edge_data.get(weight, 1)

            tentative_g = g_score[current] + edge_weight

            if tentative_g < g_score.get(neighbor, float("inf")):
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g
                f_score = tentative_g + heuristic(neighbor, dest_node)
                counter += 1
                heapq.heappush(open_set, (f_score, counter, neighbor))

    raise nx.NetworkXNoPath(f"No path between {orig_node} and {dest_node}.")
