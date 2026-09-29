"""File-format specific graph loaders (OSM XML / PBF).

This used to be two branches and a lazily-imported ``pyrosm`` inline inside
``MapEngine.load_from_file`` / ``MapEngine._load_pbf``. Pulling it out keeps
``MapEngine`` focused on graph *operations* (routing, spatial index) rather
than file parsing, and makes it trivial to add a new format later (e.g. a
GeoJSON or Overpass loader) without touching the engine at all.
"""

from __future__ import annotations

import logging
from pathlib import Path

import osmnx as ox

from app.core.exceptions import InvalidMapFileError

logger = logging.getLogger("route_finder")


def load_from_osm_xml(file_path: str | Path):
    """Build a graph from a plain ``.osm`` XML export."""
    return ox.graph_from_xml(str(file_path), simplify=True)


def load_from_pbf(file_path: str | Path, network_type: str = "drive"):
    """Build a graph from ways carrying the OSM ``highway`` tag only."""
    try:
        from pyrosm import OSM  # optional dependency, imported lazily
    except ImportError as exc:
        raise ImportError(
            "Loading .osm.pbf files requires the optional 'pyrosm' package.\n"
            "Install it with:  pip install pyrosm"
        ) from exc

    logger.info("Opening PBF with pyrosm")
    osm = OSM(str(file_path))
    # Keep only drivable highway ways. This excludes OSM buildings, POIs,
    # relations, and pedestrian-only paths that cannot be routed by car.
    nodes, edges = osm.get_network(nodes=True, network_type=network_type)
    logger.info("Converting selected highway data to a network graph")
    # Keeping all components avoids an expensive country-wide strongly
    # connected-component pass during startup. Routing still reports a
    # clean "no path" response for disconnected road islands.
    return osm.to_graph(nodes, edges, graph_type="networkx", retain_all=True)


def load_graph(file_path: str | Path, network_type: str = "drive"):
    """Dispatch to the correct loader based on the file's extension."""
    path = Path(file_path)
    name = path.name.lower()
    if name.endswith(".osm"):
        return load_from_osm_xml(path)
    if name.endswith(".pbf"):
        return load_from_pbf(path, network_type=network_type)
    raise InvalidMapFileError(
        "Unsupported file type. Please choose a .osm or .osm.pbf file."
    )
