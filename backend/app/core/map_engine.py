"""
map_engine.py
--------------
Core map & routing engine. Wraps `osmnx`/`pyrosm` (OSM data loading) and
`networkx` (graph algorithms) so callers never have to touch either library
directly. ``MapEngine`` itself holds no locks and knows nothing about HTTP -
concurrency and request/response concerns live in
``app.services.map_service.MapService``, which is the only thing that talks
to this class from the API layer.
"""

from __future__ import annotations

import math
import os

import numpy as np
from scipy.spatial import cKDTree

from app.core.edge_weights import GraphAttributeAnnotator
from app.core.exceptions import (
    InvalidEdgeWeightError,
    MissingCoordinatesError,
    RouteNotFoundError,
)
from app.core.loaders import load_graph
from app.core.pathfinding import astar_path

DEFAULT_NETWORK_TYPE = "drive"

# Re-exported for any code still importing RouteNotFoundError from here,
# matching the old `from map_engine import MapEngine, RouteNotFoundError`.
__all__ = ["MapEngine", "RouteNotFoundError"]


class MapEngine:
    """Holds the current street-network graph and exposes loading/routing helpers.

    The graph is always kept unprojected (EPSG:4326, i.e. plain lon/lat as the
    node 'x'/'y' attributes). This keeps click-coordinates directly
    comparable to node coordinates without extra projection.
    """

    def __init__(self, network_type: str = DEFAULT_NETWORK_TYPE):
        self.graph = None  # networkx.MultiDiGraph
        self.graph_source = None  # human-readable description of how it was loaded
        self.network_type = network_type
        self._nearest_node_ids = None
        self._nearest_node_tree = None
        self._longitude_scale = 1.0
        self._attribute_annotator = GraphAttributeAnnotator()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_from_file(self, file_path: str, include_travel_times: bool = True):
        """Load and annotate a graph from a local .osm or .osm.pbf file.

        ``include_travel_times`` is retained for caller compatibility; every
        graph entering the engine is now annotated for routing readiness.
        """
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        graph = load_graph(file_path, network_type=self.network_type)
        self._finalize_graph(
            graph,
            f"File: {os.path.basename(file_path)}",
            include_travel_times=include_travel_times,
        )
        return self.graph

    def load_from_graph(
        self, graph, source_desc: str, include_travel_times: bool = True
    ):
        """Publish an already-built graph (e.g. one read back from a pickle cache)."""
        self._finalize_graph(
            graph, source_desc, include_travel_times=include_travel_times
        )
        return self.graph

    def _finalize_graph(
        self, graph, source_desc: str, include_travel_times: bool = True
    ):
        """Annotate graph attributes, then publish the graph as ready.

        ``include_travel_times`` is ignored to keep the legacy signature while
        enforcing a single invariant: any graph held by MapEngine has edge
        ``length``, ``speed_kph``, and ``travel_time`` attributes.
        """
        self.graph = self._attribute_annotator.annotate(graph)
        self.graph_source = source_desc
        self._nearest_node_ids = None
        self._nearest_node_tree = None

    # ------------------------------------------------------------------
    # Geometry / lookups
    # ------------------------------------------------------------------

    def has_graph(self) -> bool:
        return self.graph is not None

    def nearest_node(self, lon: float, lat: float):
        """Return the graph node nearest to the given (lon, lat) coordinate."""
        if not self.has_graph():
            raise RuntimeError("No graph loaded yet.")
        if self._nearest_node_tree is None:
            self.prepare_spatial_index()
        _, index = self._nearest_node_tree.query([lon * self._longitude_scale, lat])
        return self._nearest_node_ids[index]

    def prepare_spatial_index(self):
        """Build a reusable nearest-node index for responsive map clicks."""
        if not self.has_graph():
            raise RuntimeError("No graph loaded yet.")
        if self._nearest_node_tree is not None:
            return

        nodes = list(self.graph.nodes(data=True))
        if not nodes:
            raise RuntimeError("The loaded graph has no nodes.")
        mean_lat = sum(data["y"] for _, data in nodes) / len(nodes)
        self._longitude_scale = math.cos(math.radians(mean_lat))
        coordinates = np.array(
            [[data["x"] * self._longitude_scale, data["y"]] for _, data in nodes]
        )
        self._nearest_node_ids = [node_id for node_id, _ in nodes]
        self._nearest_node_tree = cKDTree(coordinates)

    def node_xy(self, node_id):
        """Return (lon, lat) for a node id."""
        data = self.graph.nodes[node_id]
        return data["x"], data["y"]

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def compute_route(self, orig_node, dest_node, weight: str = "travel_time"):
        """Compute the best path between two nodes using A*."""
        if not self.has_graph():
            raise RuntimeError("No graph loaded yet.")
        if orig_node == dest_node:
            raise RouteNotFoundError("Start and end points are the same node.")

        try:
            route = astar_path(self.graph, orig_node, dest_node, weight=weight)
        except RouteNotFoundError:
            raise
        except (KeyError, MissingCoordinatesError, InvalidEdgeWeightError) as exc:
            raise RouteNotFoundError(f"Could not compute route: {exc}") from exc

        return route

    def route_stats(self, route):
        """Return a dict with distance_km, time_min and node_count for a route."""
        graph = self.graph
        length_m = 0.0
        travel_time_s = 0.0
        for u, v in zip(route[:-1], route[1:]):
            # A MultiDiGraph may have several parallel edges between u and v;
            # take the shortest one, matching what shortest_path would use.
            candidates = graph.get_edge_data(u, v).values()
            best = min(candidates, key=lambda d: d.get("length", float("inf")))
            length_m += best.get("length", 0.0)
            travel_time_s += best.get("travel_time", 0.0) or 0.0
        return {
            "distance_km": length_m / 1000.0,
            "time_min": travel_time_s / 60.0,
            "node_count": len(route),
        }
