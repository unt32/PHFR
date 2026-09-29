# PHFR — Fastest Path Finding on OpenStreetMap

A high-performance full-stack web application for calculating fastest-driving routes over OpenStreetMap (OSM) road networks. The project is organized as a clean monorepo separating the FastAPI backend, Next.js frontend, and shared map data assets.

- **Backend** — FastAPI service utilizing a customized A* and Dijkstra pathfinding engine over a `networkx` graph built from an OSM `.pbf` extract or pre-computed `.pickle` graph cache.
- **Frontend** — Modern Next.js (App Router) interface using MapLibre GL for interactive map rendering, point selection, and GeoJSON route visualization.
- **Data** — Shared OpenStreetMap dataset and graph cache directory (`data/`) mounted in Docker or directly referenced in local development.

---

## Repository Structure

```
.
├── compose.yaml                # Multi-container Docker Compose definition
├── .dockerignore               # Root Docker build ignore patterns
├── .env.example                # Environment variables template
├── .gitignore                  # Git ignore rules for Python, Node, caches, and data
├── pytest.ini                  # Root pytest configuration for workspace-level test runs
├── data/                       # Shared map data directory
│   ├── .gitkeep
│   ├── moldova.osm.pbf         # Source OpenStreetMap PBF extract
│   └── moldova.osm.pbf.drive.fastest.graph.pickle  # Pre-computed road graph cache
├── backend/                    # Python / FastAPI Backend
│   ├── Dockerfile              # Production Dockerfile for API
│   ├── .dockerignore           # Backend Docker build ignore patterns
│   ├── pytest.ini              # Backend pytest configuration
│   ├── requirements-api.txt    # Core API runtime dependencies
│   ├── requirements-dev.txt    # Development and test dependencies
│   ├── app/                    # FastAPI application package
│   │   ├── main.py             # FastAPI entry point, lifespan, & router registration
│   │   ├── config.py           # Environment-driven configuration & path resolver
│   │   ├── logging_config.py   # Logging setup
│   │   ├── schemas.py          # Pydantic models for API request/response validation
│   │   ├── api/                # HTTP API layer
│   │   │   ├── deps.py         # Router dependencies
│   │   │   ├── error_handlers.py # Global exception handlers
│   │   │   └── routers/        # Endpoint routers (health, maps, nodes, routes)
│   │   ├── core/               # Domain routing logic
│   │   │   ├── map_engine.py   # Graph storage and route computation engine
│   │   │   ├── pathfinding.py  # Dijkstra and A* algorithms with Haversine heuristic
│   │   │   ├── edge_weights.py # Road speed classification and travel-time annotator
│   │   │   ├── loaders.py      # PBF/OSM loaders (pyrosm / OSMnx)
│   │   │   └── exceptions.py   # Domain exception definitions
│   │   └── services/           # Service orchestration layer
│   │       ├── map_service.py  # Background loading, locking, and pickle caching
│   │       └── geojson.py      # GeoJSON feature generation helpers
│   ├── scripts/                # Benchmark and utility scripts
│   │   └── benchmark_algorithms.py # Offline pathfinding algorithm benchmark harness
│   ├── tests/                  # Unit test suite
│   │   ├── test_edge_weights.py
│   │   └── test_pathfinding.py
│   └── results/                # Benchmark run output directory (JSON Lines)
└── frontend/                   # Next.js / TypeScript Frontend
    ├── Dockerfile              # Multi-stage Dockerfile for Next.js
    ├── .dockerignore           # Frontend Docker build ignore patterns
    ├── .gitignore              # Frontend Git ignore rules
    ├── next.config.mjs         # Next.js configuration and API reverse-proxy
    ├── package.json            # Node dependencies and build scripts
    ├── package-lock.json       # Exact dependency lockfile
    ├── tsconfig.json           # TypeScript configuration
    ├── app/                    # App Router pages and global styles
    │   ├── page.tsx            # Main interactive routing view
    │   ├── layout.tsx          # Root HTML layout and metadata
    │   └── globals.css         # Application stylesheet
    └── components/             # React UI components
        └── route-map.tsx       # MapLibre GL map component
```

---

## Getting Started

### Prerequisites

- **Docker & Docker Compose** (for containerized execution)
- **Python 3.11+** (for local backend development)
- **Node.js 18+ & npm** (for local frontend development)

---

### Option 1: Docker Deployment (Recommended)

To run the complete application stack (Backend API + Frontend Web):

1. **Configure Environment Variables** (optional):
   ```bash
   cp .env.example .env
   ```

2. **Build and Start Containers**:
   ```bash
   docker compose up --build
   ```

3. **Access Services**:
   - **Frontend UI**: [http://localhost:3000](http://localhost:3000)
   - **Backend API**: [http://localhost:8000](http://localhost:8000)
   - **Interactive API Docs (Swagger)**: [http://localhost:8000/docs](http://localhost:8000/docs)

To stop the containers:
```bash
docker compose down
```

---

### Option 2: Local Development

#### 1. Backend Setup (FastAPI)

1. **Create and activate a virtual environment**:
   - Linux / macOS:
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     ```
   - Windows:
     ```bash
     python -m venv .venv
     .venv\Scripts\activate
     ```

2. **Install dependencies**:
   ```bash
   pip install -r backend/requirements-api.txt -r backend/requirements-dev.txt
   ```

3. **Run the FastAPI server**:
   - Running directly from the `backend/` directory:
     ```bash
     cd backend
     uvicorn app.main:app --reload --port 8000
     ```
   - Or running from the repository root:
     ```bash
     PYTHONPATH=backend uvicorn app.main:app --reload --port 8000
     ```

   The API will be available at [http://127.0.0.1:8000](http://127.0.0.1:8000).

4. **Run Unit Tests**:
   - From the repository root:
     ```bash
     pytest
     ```
   - Or from inside `backend/`:
     ```bash
     cd backend && pytest
     ```

5. **Run Pathfinding Benchmarks**:
   To benchmark Dijkstra vs. A* over sample pairs of nodes offline using the pre-computed graph cache:
   ```bash
   python backend/scripts/benchmark_algorithms.py data/moldova.osm.pbf.drive.fastest.graph.pickle --pairs 50 --output backend/results/bench.jsonl
   ```

#### 2. Frontend Setup (Next.js)

1. **Navigate to the frontend directory and install dependencies**:
   ```bash
   cd frontend
   npm install
   ```

2. **Start the Next.js development server**:
   ```bash
   npm run dev
   ```
   Open [http://localhost:3000](http://localhost:3000) in your browser.

   > **Note**: For access across a local network (e.g. testing from mobile), use:
   > ```bash
   > npm run dev:lan
   > ```

---

## Configuration Reference

Configuration options can be customized via environment variables or a `.env` file at the project root:

| Variable | Description | Default |
|:---|:---|:---|
| `OSM_PBF_PATH` | Path or filename of the OSM `.pbf` extract (searches `data/` and root) | `moldova.osm.pbf` |
| `CORS_ORIGINS` | Comma-separated list of allowed CORS origins | `http://localhost:3000,http://127.0.0.1:3000` |
| `LOG_LEVEL` | Python logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |
| `MAP_BOUNDS` | Bounding box coordinates `min_lon,min_lat,max_lon,max_lat` | `26.6,45.4,30.2,48.6` |
| `API_INTERNAL_URL` | Internal backend URL used by Next.js server rewrites proxy | `http://127.0.0.1:8000` |
| `NEXT_PUBLIC_API_URL` | Direct client-side backend URL (leave empty to use Next.js proxy) | `""` |

---

## HTTP API Overview

The FastAPI backend provides REST endpoints for health checks, map metadata, road geometries, and routing calculations:

| Method | Endpoint | Description |
|:---|:---|:---|
| `GET` | `/` | Service root and information banner |
| `GET` | `/api/health` | Health and map readiness probe |
| `GET` | `/api/maps/current` | Metadata of the currently loaded road network |
| `POST` | `/api/maps` | Trigger loading of a specified map file |
| `GET` | `/api/maps/roads` | GeoJSON FeatureCollection of road network edges |
| `POST` | `/api/nodes/nearest` | Snap coordinates `(lon, lat)` to the nearest graph node |
| `POST` | `/api/routes` | Compute the fastest route between origin and destination |

Interactive Swagger documentation is available at `/docs` and OpenAPI schema at `/openapi.json`.

---

## Graph Caching & Loading

Parsing raw OpenStreetMap `.pbf` files and computing topological road network graphs can take several minutes on larger maps.

1. **Pickle Graph Cache**: On startup, `MapService` checks for a pre-computed graph pickle (`<map_file>.drive.fastest.graph.pickle`). If found and newer than the `.pbf`, it is deserialized in seconds.
2. **Background Construction**: If no cache exists, the graph is constructed and annotated in a background thread while the service remains responsive. Endpoints requiring the graph return `503 Service Unavailable` with a descriptive message until graph loading completes.