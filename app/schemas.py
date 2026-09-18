"""Pydantic request/response schemas for the HTTP API.

``Coordinate`` and ``RouteRequest`` existed in the old ``api.py``. The
response models are new: they document the exact response shape in
``/docs`` and give FastAPI something to validate against, without changing
any field name or nesting that the frontend already relies on.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Coordinate(BaseModel):
    lon: float
    lat: float


class RouteRequest(BaseModel):
    start_node: Any
    end_node: Any


class HealthResponse(BaseModel):
    status: str
    map_loaded: bool
    error: str | None = None


class MapMetadataResponse(BaseModel):
    source: str | None
    nodes: int
    edges: int
    bounds: list[list[float]]


class RootResponse(BaseModel):
    service: str
    docs: str


class NearestNodeResponse(BaseModel):
    node_id: Any
    lon: float
    lat: float


class RouteStats(BaseModel):
    distance_km: float
    time_min: float
    node_count: int


class GeoJSONGeometry(BaseModel):
    type: str
    coordinates: list[list[float]]


class GeoJSONFeature(BaseModel):
    type: str
    geometry: GeoJSONGeometry


class RouteResponse(BaseModel):
    route: GeoJSONFeature
    stats: RouteStats
    node_count: int
