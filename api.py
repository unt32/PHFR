"""HTTP API for the web route-finder interface."""

import os
import shutil
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from map_engine import MapEngine, RouteNotFoundError


engine = MapEngine()
engine_lock = threading.RLock()


class Coordinate(BaseModel):
    lon: float
    lat: float


class RouteRequest(BaseModel):
    start_node: Any
    end_node: Any


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


def _load_map(path: str):
    with engine_lock:
        # The web UI currently routes by distance only, so skip the costly
        # speed/travel-time enrichment used by the desktop application.
        engine.load_from_file(path, include_travel_times=False)
        longitudes = [data["x"] for _, data in engine.graph.nodes(data=True)]
        latitudes = [data["y"] for _, data in engine.graph.nodes(data=True)]
        return {
            "source": engine.graph_source,
            "nodes": engine.graph.number_of_nodes(),
            "edges": engine.graph.number_of_edges(),
            "bounds": [[min(longitudes), min(latitudes)], [max(longitudes), max(latitudes)]],
        }


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(title="OSM Route Finder API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok", "map_loaded": engine.has_graph()}


@app.post("/api/maps")
async def upload_map(file: UploadFile = File(...)):
    filename = file.filename or ""
    suffix = ".osm.pbf" if filename.lower().endswith(".osm.pbf") else Path(filename).suffix.lower()
    if suffix not in {".osm", ".osm.pbf"}:
        raise HTTPException(status_code=400, detail="Upload an .osm or .osm.pbf file.")

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_path = temp_file.name
            shutil.copyfileobj(file.file, temp_file)
        return await run_in_threadpool(_load_map, temp_path)
    except HTTPException:
        raise
    except Exception as exc:  # Surface parser errors to the UI without a traceback.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await file.close()
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


@app.get("/api/maps/roads")
def map_roads():
    return _roads_geojson()


@app.post("/api/nodes/nearest")
def nearest_node(point: Coordinate):
    with engine_lock:
        if not engine.has_graph():
            raise HTTPException(status_code=409, detail="Load a map first.")
        try:
            node_id = engine.nearest_node(point.lon, point.lat)
            lon, lat = engine.node_xy(node_id)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"node_id": node_id, "lon": lon, "lat": lat}


@app.post("/api/routes")
def route(request: RouteRequest):
    with engine_lock:
        if not engine.has_graph():
            raise HTTPException(status_code=409, detail="Load a map first.")
        try:
            nodes = engine.compute_route(request.start_node, request.end_node)
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

    return {
        "route": {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coordinates}},
        "stats": stats,
        "node_count": len(nodes),
    }
