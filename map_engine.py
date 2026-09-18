"""
map_engine.py
--------------
Core map & routing engine. Wraps `osmnx` (OSM data loading) and `networkx`
(graph algorithms) so the UI layer never has to touch either library
directly. Designed to be safely called from a background QThread.
"""

import logging
import math
import os

import numpy as np
import osmnx as ox
from scipy.spatial import cKDTree

from edge_weights import GraphAttributeAnnotator, InvalidEdgeWeightError
from pathfinding import (
    MissingCoordinatesError,
    RouteNotFoundError,
    astar_path,
)

# Quieter console + cache downloaded data between runs for speed.
ox.settings.log_console = False
ox.settings.use_cache = True

NETWORK_TYPE = "drive"
logger = logging.getLogger("route_finder")


class MapEngine:
    """Holds the current street-network graph and exposes loading/routing helpers.

    The graph is always kept unprojected (EPSG:4326, i.e. plain lon/lat as the
    node 'x'/'y' attributes). This keeps click-coordinates on the matplotlib
    canvas directly comparable to node coordinates without extra projection.
    """

    def __init__(self):
        self.graph = None  # networkx.MultiDiGraph
        self.graph_source = None  # human-readable description of how it was loaded
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

        lower = file_path.lower()
        if lower.endswith(".osm"):
            G = ox.graph_from_xml(file_path, simplify=True)
        elif lower.endswith(".pbf"):
            try:
                logger.info("Opening PBF with pyrosm")
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

        self._finalize_graph(
            G,
            f"File: {os.path.basename(file_path)}",
            include_travel_times=include_travel_times,
        )
        return self.graph

    @staticmethod
    def _load_pbf(file_path: str):
        """Build a graph from ways carrying the OSM ``highway`` tag only."""
        from pyrosm import OSM  # optional dependency, imported lazily

        osm = OSM(file_path)
        # Keep only drivable highway ways. This excludes OSM buildings, POIs,
        # relations, and pedestrian-only paths that cannot be routed by car.
        nodes, edges = osm.get_network(nodes=True, network_type=NETWORK_TYPE)
        logger.info("Converting selected highway data to a network graph")
        # Keeping all components avoids an expensive country-wide strongly
        # connected-component pass during startup. Routing still reports a
        # clean "no path" response for disconnected road islands.
        G = osm.to_graph(nodes, edges, graph_type="networkx", retain_all=True)
        return G

    def _finalize_graph(self, G, source_desc: str, include_travel_times: bool = True):
        """Annotate graph attributes, then publish the graph as ready.

        ``include_travel_times`` is ignored to keep the legacy signature while
        enforcing a single invariant: any graph held by MapEngine has edge
        ``length``, ``speed_kph``, and ``travel_time`` attributes.
        """
        self.graph = self._attribute_annotator.annotate(G)
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
        """Compute the best path between two nodes.

        The app exposes a single, unified routing algorithm (A*, weighted by
        estimated travel time in seconds) rather than letting the caller pick
        between a "fastest" and "shortest" mode - `weight` is kept as a
        parameter for internal flexibility/testing, but every caller in this
        codebase relies on the `travel_time` default.
        """
        if not self.has_graph():
            raise RuntimeError("No graph loaded yet.")
        if orig_node == dest_node:
            raise RouteNotFoundError("Start and end points are the same node.")
        try:
            route = astar_path(self.graph, orig_node, dest_node, weight=weight)
        except RouteNotFoundError:
            raise
        except (KeyError, MissingCoordinatesError, InvalidEdgeWeightError) as exc:
            raise RouteNotFoundError(str(exc)) from exc
        return route

    def route_stats(self, route):
        """Return a dict with distance_km, time_min and node_count for a route."""
        G = self.graph
        length_m = 0.0
        travel_time_s = 0.0
        for u, v in zip(route[:-1], route[1:]):
            # A MultiDiGraph may have several parallel edges between u and v;
            # take the shortest one, matching what shortest_path would use.
            candidates = G.get_edge_data(u, v).values()
            best = min(candidates, key=lambda d: d.get("length", float("inf")))
            length_m += best.get("length", 0.0)
            travel_time_s += best.get("travel_time", 0.0) or 0.0
        return {
            "distance_km": length_m / 1000.0,
            "time_min": travel_time_s / 60.0,
            "node_count": len(route),
        }
