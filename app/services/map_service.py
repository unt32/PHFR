"""Application-level orchestration around ``MapEngine``.

Everything in here used to be module-level globals and free functions in
``api.py``: ``engine``, ``engine_lock``, ``map_loading``, ``map_load_error``,
``_load_map``, ``_map_metadata``, ``_load_default_map``,
``_initialize_default_map``. That made the engine's state process-global and
impossible to isolate in a test. ``MapService`` wraps all of it in one
object so locking discipline lives in exactly one place, the FastAPI app
owns exactly one instance of it (``app.state.map_service``), and tests can
construct their own instance pointed at a fixture file.
"""

from __future__ import annotations

import logging
import os
import pickle
import threading
import time
from pathlib import Path

from app.core.exceptions import MapLoadFailedError, MapLoadingError, MapNotLoadedError
from app.core.map_engine import MapEngine
from app.services.geojson import build_route_coordinates, roads_feature_collection

logger = logging.getLogger("route_finder")


class MapService:
    """Owns the single MapEngine instance and coordinates concurrent access."""

    def __init__(
        self,
        map_file: Path,
        graph_cache_file: Path,
        bounds: list[list[float]],
        network_type: str = "drive",
    ) -> None:
        self.engine = MapEngine(network_type=network_type)
        self._lock = threading.RLock()
        self._loading = False
        self._error: str | None = None
        self.map_file = Path(map_file)
        self.graph_cache_file = Path(graph_cache_file)
        self.bounds = bounds

    # -- status ---------------------------------------------------------

    def is_loading(self) -> bool:
        return self._loading

    def load_error(self) -> str | None:
        return self._error

    def has_graph(self) -> bool:
        return self.engine.has_graph()

    def metadata(self, check_ready: bool = True) -> dict:
        if check_ready and self._loading:
            raise MapLoadingError("Moldova map is loading.")
        if self._error:
            raise MapLoadFailedError(f"Moldova map failed to load: {self._error}")
        if not self.engine.has_graph():
            raise MapLoadingError("Moldova map is still loading.")
        return {
            "source": self.engine.graph_source,
            "nodes": self.engine.graph.number_of_nodes(),
            "edges": self.engine.graph.number_of_edges(),
            "bounds": self.bounds,
        }

    def current_map_status(self) -> dict:
        """Status for the polling endpoint. Never blocks on the graph lock:
        the frontend polls this while startup work is running and needs an
        immediate loading response, not one queued behind a multi-minute
        graph build.
        """
        if self._loading:
            # The graph itself is usable as soon as it has been read from
            # cache. The spatial index may still be warming up in the
            # background.
            if self.engine.has_graph():
                return self.metadata(check_ready=False)
            raise MapLoadingError("Moldova map is loading.")
        with self._lock:
            return self.metadata()

    # -- loading ----------------------------------------------------------

    def load_map_from_path(self, path: str, include_travel_times: bool = True) -> dict:
        started_at = time.perf_counter()
        with self._lock:
            logger.info("Starting road-graph construction from %s", Path(path).name)
            logger.info("Reading PBF and selecting drivable highway ways")
            self.engine.load_from_file(path, include_travel_times=include_travel_times)
            logger.info(
                "Graph created: %s nodes, %s edges",
                f"{self.engine.graph.number_of_nodes():,}",
                f"{self.engine.graph.number_of_edges():,}",
            )
            logger.info("Building nearest-node index for map clicks")
            self.engine.prepare_spatial_index()
            logger.info(
                "Map initialization complete in %.1f seconds",
                time.perf_counter() - started_at,
            )
            return {
                "source": self.engine.graph_source,
                "nodes": self.engine.graph.number_of_nodes(),
                "edges": self.engine.graph.number_of_edges(),
                "bounds": self.bounds,
            }

    def load_default_map(self) -> dict:
        """Load the default map from disk once, then reuse the graph cache."""
        with self._lock:
            cache_is_current = (
                self.graph_cache_file.is_file()
                and self.graph_cache_file.stat().st_mtime >= self.map_file.stat().st_mtime
            )
            if cache_is_current:
                logger.info("Loading cached road graph: %s", self.graph_cache_file.name)
                with self.graph_cache_file.open("rb") as cache_file:
                    graph = pickle.load(cache_file)
                self.engine.load_from_graph(
                    graph,
                    f"Cached file: {self.map_file.name}",
                    include_travel_times=True,
                )
                logger.info("Cached road graph is ready")
                return self.metadata(check_ready=False)

            metadata = self.load_map_from_path(str(self.map_file), include_travel_times=True)
            self._save_graph_cache()
            return metadata

    def _save_graph_cache(self) -> None:
        try:
            temporary_cache = self.graph_cache_file.with_suffix(".pickle.tmp")
            logger.info("Saving road graph cache: %s", self.graph_cache_file.name)
            with temporary_cache.open("wb") as cache_file:
                pickle.dump(self.engine.graph, cache_file, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(temporary_cache, self.graph_cache_file)
        except OSError:
            logger.exception("Could not save the road graph cache")

    def start_background_load(self) -> None:
        """Kick off default-map loading on a daemon thread and return immediately."""
        self._loading = True
        self._error = None
        thread = threading.Thread(
            target=self._run_background_load, name="map-loader", daemon=True
        )
        thread.start()

    def _run_background_load(self) -> None:
        try:
            self.load_default_map()
        except Exception as exc:  # Keep the HTTP server alive to expose the error.
            self._error = str(exc)
            logger.exception("Default map initialization failed")
        finally:
            self._loading = False

    # -- routing / read operations ----------------------------------------

    def ensure_ready(self) -> None:
        if not self.engine.has_graph():
            raise MapNotLoadedError("Load a map first.")

    def nearest_node(self, lon: float, lat: float) -> tuple:
        with self._lock:
            self.ensure_ready()
            node_id = self.engine.nearest_node(lon, lat)
            lon_out, lat_out = self.engine.node_xy(node_id)
            return node_id, lon_out, lat_out

    def compute_route(self, start_node, end_node) -> tuple:
        with self._lock:
            self.ensure_ready()
            nodes = self.engine.compute_route(start_node, end_node)
            stats = self.engine.route_stats(nodes)
            coordinates = build_route_coordinates(self.engine.graph, nodes)
            return nodes, stats, coordinates

    def roads_geojson(self) -> dict:
        with self._lock:
            self.ensure_ready()
            return roads_feature_collection(self.engine.graph)
