# Backend Refactor Notes

## What changed, and why

The old backend was three flat modules plus a FastAPI file that mixed HTTP
handling, global mutable state, locking, caching, and domain logic all
together (`engine`, `engine_lock`, `map_loading`, `map_load_error` as
module-level globals in `api.py`, with route handlers reaching into them
directly). That made it hard to test anything without booting the whole
app, and hard to change one concern (e.g. swap the cache strategy) without
touching HTTP code.

The refactor introduces three layers, each only depending on the one
below it:

```
app/api/       FastAPI routers, request/response models, HTTP status codes.
                  Zero domain logic. Depends on services.
app/services/  Orchestration: MapService owns the MapEngine instance, the
                  lock, background loading, and on-disk graph caching.
                  Depends on core.
app/core/      Pure domain logic: MapEngine, A* pathfinding, edge-weight
                  annotation, file loaders. No FastAPI import anywhere in
                  this package - it's usable from a script or a test with
                  no HTTP server involved.
```

### File-by-file mapping (old -> new)

| Old file | New location(s) |
|---|---|
| `api.py` (module globals: `engine`, `engine_lock`, `map_loading`, `map_load_error`) | `app/services/map_service.py` (`MapService`) |
| `api.py` (`_edge_coordinates`, `_roads_geojson`) | `app/services/geojson.py` |
| `api.py` (route handlers) | `app/api/routers/{health,maps,nodes,routes}.py` |
| `api.py` (`Coordinate`, `RouteRequest`) | `app/schemas.py` |
| `api.py` (FastAPI app + CORS + lifespan) | `app/main.py` |
| `map_engine.py` (`MapEngine`) | `app/core/map_engine.py` |
| `map_engine.py` (`_load_pbf`) | `app/core/loaders.py` |
| `pathfinding.py` (`astar_path`, `haversine_distance_m`) | `app/core/pathfinding.py` |
| `pathfinding.py` (`RouteNotFoundError`, `MissingCoordinatesError`) | `app/core/exceptions.py` (re-exported from `pathfinding.py` too) |
| `edge_weights.py` (`GraphAttributeAnnotator`, speed tables) | `app/core/edge_weights.py` |
| `edge_weights.py` (`InvalidEdgeWeightError`) | `app/core/exceptions.py` (re-exported from `edge_weights.py` too) |

The old top-level `map_engine.py`, `pathfinding.py`, and `edge_weights.py`
are superseded by their `app/core/` equivalents - delete them from your
repo when you apply this refactor (their logic is unchanged, just moved
and given proper import paths).

## HTTP contract: unchanged

Every endpoint, path, method, request body, response shape, and status
code is identical to before:

- `GET /api/health`
- `GET /`
- `GET /api/maps/current`
- `POST /api/maps`
- `GET /api/maps/roads`
- `POST /api/nodes/nearest`
- `POST /api/routes`

Status codes for the "no map loaded" (409), "still loading" (503), "load
failed" (500), and "no route found" (422) cases are now produced by a
single table in `app/api/error_handlers.py` instead of being re-derived
inline in every handler - but they resolve to the exact same codes and
`{"detail": "..."}` body shape as before, so the frontend needs zero
changes.

## Concurrency model: same locking, one owner

The original `engine_lock` (a `threading.RLock`) is preserved exactly -
same reentrancy, same scope around graph mutation and route computation.
It now lives inside `MapService` instead of being a bare module global, so
every access to the graph goes through one object instead of trusting each
route handler to remember to acquire the lock.

## What's genuinely new

- **Typed settings** (`app/config.py`): the default map path, CORS
  origins, and log level are all environment-overridable
  (`OSM_PBF_PATH`, `CORS_ORIGINS`, `LOG_LEVEL`) instead of hardcoded
  constants. See `.env.example`.
- **Centralized exception -> HTTP status mapping**
  (`app/api/error_handlers.py`) instead of repeated inline
  `HTTPException` calls.
- **Response models** (`app/schemas.py`) for every endpoint, so `/docs`
  documents the real response shape.
- **Unit tests** (`tests/`) for the pure-logic pieces (maxspeed parsing,
  edge annotation, A* correctness) that need no OSM data and no running
  server.
- **`MapEngine.load_from_graph`**: loading a pickled cache no longer
  reaches into a private `_finalize_graph` method from outside the class;
  it's a proper public entry point now.
- A **backward-compatible root `api.py`** shim, so `uvicorn api:app` still
  works if any external tooling (deploy scripts, systemd units) still
  points at it. Prefer `uvicorn app.main:app` going forward.

## Running it

```bash
pip install -r requirements-api.txt
uvicorn app.main:app --reload
# or, unchanged:
uvicorn api:app --reload
```

Tests (no OSM data or network required):

```bash
pip install -r requirements-dev.txt
pytest
```
