# OSM Route Finder

## Web interface

The desktop UI remains available, and the same Python routing engine can now
be used through a Next.js interface. Start the API and frontend in separate
terminals:

```bash
cd PHFR
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-api.txt
.venv\Scripts\python -m uvicorn api:app --reload
```

Use an official CPython 3.13 x64 installation, not the MSYS2 Python runtime.

```bash
cd PHFR/web
npm install
npm run dev
```

Open `http://localhost:3000`. The browser uploads a local `.osm` or
`.osm.pbf` file to FastAPI; PBF parsing and route calculation stay in Python.
The API only sends road geometry and route results to the frontend.

A desktop application for loading OpenStreetMap data from a local file,
visualizing the street network, and finding the shortest path between two
points — built with `osmnx` + `networkx` for the routing engine and PyQt6 +
`matplotlib` for a dark-themed UI.

## Project structure

```
osm_route_finder/
├── main.py           # Application entry point
├── ui_main.py         # PyQt6 dashboard UI (sidebar + embedded matplotlib canvas)
├── map_engine.py       # OSM loading / graph building / routing logic (osmnx + networkx)
├── requirements.txt    # Python dependencies
└── README.md
```

The code is split so each file has one job: `map_engine.py` never imports
Qt, and `ui_main.py` never calls `osmnx`/`networkx` routing functions
directly — it only calls methods on a `MapEngine` instance. This keeps the
engine testable/reusable outside of the GUI.

## Requirements

- Python 3.10+
- No internet connection needed — maps are loaded from a local file.

## Installation

```bash
# 1. (recommended) create a virtual environment
python -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate

# 2. install dependencies
pip install -r requirements.txt
```

> **Optional:** if you want to load `.osm.pbf` files directly (instead of
> `.osm` XML files), also install `pyrosm`:
> ```bash
> pip install pyrosm
> ```

## Running the app

```bash
python main.py
```

## How to use it

1. **Load a map** (left sidebar, section 1):
   - Click *Load Local .osm / .osm.pbf File* and pick a file from disk.
   - The network is always built for driving (`drive`).
   - Parsing runs on a background thread, with an indeterminate progress
     bar shown while the UI stays responsive.

2. **Pick a start and end point** (section 2):
   - Click **Pick Start (green)**, then click anywhere on the map — the app
     snaps your click to the nearest graph node.
   - Click **Pick End (red)** and click on the map for the destination.
   - As soon as both points are set, the shortest route is found
     automatically.

3. **Route Details** panel shows the total distance (km), estimated time
   (min), and number of nodes/intersections traversed for the route, which
   is drawn on the map in neon blue.

4. **Errors** (invalid files, no path between points, etc.) are shown as Qt
   message boxes rather than crashing the app.

## Notes & tips

- The map view supports the standard matplotlib toolbar above it (pan,
  zoom, save-as-image) in addition to point picking.
- Distances/times are computed by summing the `length` / `travel_time`
  attributes of the edges along the route (not straight-line distance).
