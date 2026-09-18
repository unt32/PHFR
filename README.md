# PHFR — Fastest Path Finding on OpenStreetMap

A small full-stack app for computing fastest-driving routes over an
OpenStreetMap graph of Moldova:

- **Backend** — FastAPI + custom A* over a `networkx` graph built from a
  `.osm.pbf` extract (or a pre-built `.pickle` cache).
- **Frontend** — Next.js app in `web/` that renders the graph and routes.

The repository ships with `moldova.osm.pbf` and a pre-built
`moldova.osm.drive.fastest.graph.pickle` so the backend can start serving
routes without any preprocessing.

---

## Quick start (Windows)

### Backend

```bash
cd PHFR
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-api.txt
.venv\Scripts\python -m uvicorn app.main:app --reload
```

To expose the API on the LAN (needed if you run the web UI on another
device):

```bash
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd PHFR/web
npm install
npm run dev        # localhost only
npm run dev:lan    # bind to 0.0.0.0, use together with the --host 0.0.0.0 backend
```

### Tests

```bash
cd PHFR
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

Tests are pure-logic only — no OSM data and no running server required.

---

## HTTP API

All endpoints are unchanged from the original implementation. Response
models are declared in `app/schemas.py`, so `/docs` shows the real shapes.

| Method | Path                    | Purpose                                    |
|-------:|-------------------------|--------------------------------------------|
| GET    | `/`                     | Service banner                             |
| GET    | `/api/health`           | Liveness + map-load status                 |
| GET    | `/api/maps/current`     | Metadata about the currently loaded map    |
| POST   | `/api/maps`             | Load a map (path or use the default)       |
| GET    | `/api/maps/roads`       | GeoJSON of all edges (for the map UI)      |
| POST   | `/api/nodes/nearest`    | Snap a coordinate to the nearest graph node|
| POST   | `/api/routes`           | Compute the fastest route between two points|

Common error codes:

| Status | Meaning                                  |
|-------:|------------------------------------------|
| 409    | No map is currently loaded               |
| 503    | Map is still loading                     |
| 500    | Map failed to load                       |
| 422    | No route found between the given points  |

All errors use the standard `{"detail": "..."}` body.

---

## Configuration

Settings live in `app/config.py` and are environment-overridable:

| Variable       | Purpose                              | Default                        |
|----------------|--------------------------------------|--------------------------------|
| `OSM_PBF_PATH` | Default `.osm.pbf` / `.pickle` path  | `moldova.osm.drive.fastest…`   |
| `CORS_ORIGINS` | Comma-separated allowed origins      | dev defaults                   |
| `LOG_LEVEL`    | Python log level                     | `INFO`                         |

See `.env.example` for a template.

---

## Backend architecture

The backend is split into three layers, each depending only on the one
below it:

```
app/api/       FastAPI routers, request/response models, HTTP status codes.
               Zero domain logic. Depends on services.

app/services/  Orchestration: MapService owns the MapEngine instance, the
               lock, background loading, and on-disk graph caching.
               Depends on core.

app/core/      Pure domain logic: MapEngine, A* pathfinding, edge-weight
               annotation, file loaders. No FastAPI import anywhere in
               this package — usable from a script or a test with no
               HTTP server involved.
```

### Why this layout

The original backend was three flat modules (`map_engine.py`,
`pathfinding.py`, `edge_weights.py`) plus an `api.py` that mixed HTTP
handling, global mutable state, locking, caching, and domain logic all
together. That made it hard to test anything without booting the whole
app, and hard to change one concern (e.g. swap the cache strategy)
without touching HTTP code.

The refactor keeps behaviour identical and only moves responsibilities
to the right layer.

### File-by-file mapping (old → new)

| Old                                        | New                                                     |
|--------------------------------------------|---------------------------------------------------------|
| `api.py` module globals (`engine`, `engine_lock`, `map_loading`, `map_load_error`) | `app/services/map_service.py` (`MapService`) |
| `api.py` (`_edge_coordinates`, `_roads_geojson`) | `app/services/geojson.py`                          |
| `api.py` route handlers                    | `app/api/routers/{health,maps,nodes,routes}.py`         |
| `api.py` (`Coordinate`, `RouteRequest`)    | `app/schemas.py`                                        |
| `api.py` (FastAPI app + CORS + lifespan)   | `app/main.py`                                           |
| `map_engine.py` (`MapEngine`)              | `app/core/map_engine.py`                                |
| `map_engine.py` (`_load_pbf`)              | `app/core/loaders.py`                                   |
| `pathfinding.py` (`astar_path`, `haversine_distance_m`) | `app/core/pathfinding.py`                  |
| `pathfinding.py` (`RouteNotFoundError`, `MissingCoordinatesError`) | `app/core/exceptions.py` (re-exported)   |
| `edge_weights.py` (`GraphAttributeAnnotator`, speed tables) | `app/core/edge_weights.py`              |
| `edge_weights.py` (`InvalidEdgeWeightError`)| `app/core/exceptions.py` (re-exported)                 |

### Concurrency

The original `engine_lock` (a `threading.RLock`) is preserved exactly —
same reentrancy, same scope around graph mutation and route computation.
It now lives inside `MapService` instead of being a bare module global, so
every access to the graph goes through one object instead of trusting each
route handler to remember to acquire the lock.

### Backward compatibility

`uvicorn api:app` still works via the compatibility shim in the root
`api.py`. Prefer `uvicorn app.main:app` going forward — the shim exists
only for external tooling (deploy scripts, systemd units).

---

## Repository layout

```
.
├── api.py                       # compat shim → app.main:app
├── app/                         # backend package (see architecture above)
│   ├── api/                     # routers + error handlers
│   ├── core/                    # pure domain logic
│   ├── services/                # MapService, GeoJSON helpers
│   ├── config.py
│   ├── logging_config.py
│   ├── main.py
│   └── schemas.py
├── tests/                       # unit tests (no OSM data required)
├── web/                         # Next.js frontend
├── moldova.osm.pbf              # OSM extract (source data)
├── moldova.osm.drive.fastest.graph.pickle  # prebuilt graph cache
├── requirements-api.txt
├── requirements-dev.txt
├── pyproject.toml
└── start.sh
```

---

## Notes on the graph cache

Loading the `.osm.pbf` and building the drivable graph is expensive
(minutes). On startup `MapService` will:

1. Use the pickled graph (`moldova.osm.drive.fastest.graph.pickle`) if
   it exists and is newer than the `.pbf`.
2. Otherwise parse the `.pbf`, annotate edge weights (maxspeed,
   oneway, etc.), and write a fresh pickle.

The load runs in a background thread, guarded by the same `RLock` used
for routing. During load, endpoints that need the graph return `503`;
if loading fails they return `500` until a new load is triggered.