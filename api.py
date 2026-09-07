"""HTTP API for the web route-finder interface."""

import os
import pickle
import shutil
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
import logging

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from map_engine import MapEngine, RouteNotFoundError


engine = MapEngine()
engine_lock = threading.RLock()
map_loading = False
map_load_error = None
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("route_finder")
DEFAULT_MAP_FILE = Path(
    os.getenv("OSM_PBF_PATH", Path(__file__).with_name("moldova.osm.pbf"))
)
DEFAULT_GRAPH_CACHE = DEFAULT_MAP_FILE.with_suffix(".drive.fastest.graph.pickle")
# Fixed bounds keep startup from scanning every node in the 1+ GB cached graph.
MOLDOVA_BOUNDS = [[26.6, 45.4], [30.2, 48.6]]


class Coordinate(BaseModel):
    lon: float
    lat: float


class RouteRequest(BaseModel):
    start_node: Any
    end_node: Any
    mode: Literal["shortest", "fastest"] = "fastest"


def _edge_coordinates(graph, u, v, data):
    """Return an edge geometry in its travel direction as [lon, lat] pairs."""
    geometry = data.get("geometry")
    if geometry is not None:
        coordinates = [[float(x), float(y)] for x, y in geometry.coords]
    else:
        coordinates = [
            [float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"])],
            [float(graph.nodes[v]["x"]), float(graph.nodes[v]["y"])],
        ]

    source = [float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"])]
    if coordinates and coordinates[0] != source:
        coordinates.reverse()
    return coordinates


def _roads_geojson():
    with engine_lock:
        if not engine.has_graph():
            raise HTTPException(status_code=409, detail="Load a map first.")

        graph = engine.graph
        features = []
        for u, v, key, data in graph.edges(keys=True, data=True):
            features.append(
                {
                    "type": "Feature",
                    "properties": {"id": f"{u}-{v}-{key}"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": _edge_coordinates(graph, u, v, data),
                    },
                }
            )
        return {"type": "FeatureCollection", "features": features}


def _load_map(path: str, include_travel_times: bool = True):
    started_at = time.perf_counter()
    with engine_lock:
        logger.info("Starting road-graph construction from %s", Path(path).name)
        logger.info("Reading PBF and selecting drivable highway ways")
        engine.load_from_file(path, include_travel_times=include_travel_times)
        logger.info(
            "Graph created: %s nodes, %s edges",
            f"{engine.graph.number_of_nodes():,}",
            f"{engine.graph.number_of_edges():,}",
        )
        logger.info("Building nearest-node index for map clicks")
        engine.prepare_spatial_index()
        logger.info("Map initialization complete in %.1f seconds", time.perf_counter() - started_at)
        return {
            "source": engine.graph_source,
            "nodes": engine.graph.number_of_nodes(),
            "edges": engine.graph.number_of_edges(),
            "bounds": MOLDOVA_BOUNDS,
        }


def _map_metadata(check_ready: bool = True):
    if check_ready and map_loading:
        raise HTTPException(status_code=503, detail="Moldova map is loading.")
    if map_load_error:
        raise HTTPException(status_code=500, detail=f"Moldova map failed to load: {map_load_error}")
    if not engine.has_graph():
        raise HTTPException(status_code=503, detail="Moldova map is still loading.")
    return {
        "source": engine.graph_source,
        "nodes": engine.graph.number_of_nodes(),
        "edges": engine.graph.number_of_edges(),
        "bounds": MOLDOVA_BOUNDS,
    }


def _load_default_map():
    """Load Moldova from the PBF once, then reuse the on-disk graph cache."""
    with engine_lock:
        cache_is_current = (
            DEFAULT_GRAPH_CACHE.is_file()
            and DEFAULT_GRAPH_CACHE.stat().st_mtime >= DEFAULT_MAP_FILE.stat().st_mtime
        )
        if cache_is_current:
            logger.info("Loading cached Moldova road graph: %s", DEFAULT_GRAPH_CACHE.name)
            with DEFAULT_GRAPH_CACHE.open("rb") as cache_file:
                graph = pickle.load(cache_file)
            engine._finalize_graph(
                graph,
                f"Cached file: {DEFAULT_MAP_FILE.name}",
                include_travel_times=False,
            )
            logger.info("Cached Moldova graph is ready")
            return _map_metadata(check_ready=False)

        metadata = _load_map(str(DEFAULT_MAP_FILE), include_travel_times=True)
        try:
            temporary_cache = DEFAULT_GRAPH_CACHE.with_suffix(".pickle.tmp")
            logger.info("Saving road graph cache: %s", DEFAULT_GRAPH_CACHE.name)
            with temporary_cache.open("wb") as cache_file:
                pickle.dump(engine.graph, cache_file, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(temporary_cache, DEFAULT_GRAPH_CACHE)
        except OSError:
            logger.exception("Could not save the road graph cache")
        return metadata


def _initialize_default_map():
    global map_loading, map_load_error
    map_loading = True
    map_load_error = None
    try:
        _load_default_map()
    except Exception as exc:  # Keep the HTTP server alive to expose the error.
        map_load_error = str(exc)
        logger.exception("Default Moldova map initialization failed")
    finally:
        map_loading = False


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not DEFAULT_MAP_FILE.is_file():
        raise RuntimeError(f"Default Moldova PBF file was not found: {DEFAULT_MAP_FILE}")
    logger.info("Starting default Moldova map initialization: %s", DEFAULT_MAP_FILE)
    threading.Thread(target=_initialize_default_map, name="moldova-map-loader", daemon=True).start()
    yield


app = FastAPI(title="OSM Route Finder API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://172.20.10.2:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {
        "status": "loading" if map_loading else "ok",
        "map_loaded": engine.has_graph(),
        "error": map_load_error,
    }


@app.get("/api/maps/current")
def current_map():
    # Do not wait on the graph-building lock. The frontend polls this endpoint
    # while startup work is running and needs an immediate loading response.
    if map_loading:
        # The graph itself is usable as soon as it has been read from cache.
        # The spatial index may still be warming up in the background.
        if engine.has_graph():
            return _map_metadata(check_ready=False)
        raise HTTPException(status_code=503, detail="Moldova map is loading.")
    with engine_lock:
        return _map_metadata()


@app.get("/")
def root():
    return {"service": "OSM Route Finder API", "docs": "/docs"}


@app.post("/api/maps")
async def upload_map(file: UploadFile = File(...)):
    filename = file.filename or ""
    suffix = ".osm.pbf" if filename.lower().endswith(".osm.pbf") else Path(filename).suffix.lower()
    if suffix not in {".osm", ".osm.pbf"}:
        raise HTTPException(status_code=400, detail="Upload an .osm or .osm.pbf file.")

    temp_path = None
    try:
        logger.info("Received map upload: %s", filename)
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_path = temp_file.name
            shutil.copyfileobj(file.file, temp_file)
        logger.info("Upload saved temporarily: %.1f MB", os.path.getsize(temp_path) / 1024**2)
        return await run_in_threadpool(_load_map, temp_path)
    except HTTPException:
        raise
    except Exception as exc:  # Surface parser errors to the UI without a traceback.
        logger.exception("Map loading failed")
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await file.close()
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
            logger.info("Temporary upload deleted")


@app.get("/api/maps/roads")
def map_roads():
    return _roads_geojson()


@app.post("/api/nodes/nearest")
def nearest_node(point: Coordinate):
    started_at = time.perf_counter()
    with engine_lock:
        if not engine.has_graph():
            raise HTTPException(status_code=409, detail="Load a map first.")
        try:
            node_id = engine.nearest_node(point.lon, point.lat)
            lon, lat = engine.node_xy(node_id)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info("Nearest node selected in %.3f seconds", time.perf_counter() - started_at)
    return {"node_id": node_id, "lon": lon, "lat": lat}


@app.post("/api/routes")
def route(request: RouteRequest):
    started_at = time.perf_counter()
    with engine_lock:
        if not engine.has_graph():
            raise HTTPException(status_code=409, detail="Load a map first.")
        try:
            weight = "travel_time" if request.mode == "fastest" else "length"
            nodes = engine.compute_route(request.start_node, request.end_node, weight=weight)
            stats = engine.route_stats(nodes)
        except RouteNotFoundError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        coordinates = []
        for u, v in zip(nodes[:-1], nodes[1:]):
            edge = min(
                engine.graph.get_edge_data(u, v).values(),
                key=lambda data: data.get("length", float("inf")),
            )
            segment = _edge_coordinates(engine.graph, u, v, edge)
            coordinates.extend(segment if not coordinates else segment[1:])

    logger.info("Route built in %.3f seconds (%s nodes)", time.perf_counter() - started_at, len(nodes))
    return {
        "route": {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coordinates}},
        "stats": stats,
        "node_count": len(nodes),
    }
