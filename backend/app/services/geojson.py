"""Pure functions for converting graph data into GeoJSON.

Extracted from the module-level ``_edge_coordinates`` / ``_roads_geojson``
helpers in the old ``api.py``. They take a graph in, return plain dicts/
lists out, and touch no global state - easy to unit test without spinning
up FastAPI or loading a real map.
"""

from __future__ import annotations


def edge_coordinates(graph, u, v, data) -> list[list[float]]:
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


def roads_feature_collection(graph) -> dict:
    """Build the full road network as a GeoJSON FeatureCollection."""
    features = []
    for u, v, key, data in graph.edges(keys=True, data=True):
        features.append(
            {
                "type": "Feature",
                "properties": {"id": f"{u}-{v}-{key}"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": edge_coordinates(graph, u, v, data),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def build_route_coordinates(graph, nodes: list) -> list[list[float]]:
    """Stitch per-edge geometries along a node path into one continuous line."""
    coordinates: list[list[float]] = []
    for u, v in zip(nodes[:-1], nodes[1:]):
        edge = min(
            graph.get_edge_data(u, v).values(),
            key=lambda data: data.get("length", float("inf")),
        )
        segment = edge_coordinates(graph, u, v, edge)
        coordinates.extend(segment if not coordinates else segment[1:])
    return coordinates
