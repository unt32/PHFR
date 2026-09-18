"""Health-check and root endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_map_service
from app.schemas import HealthResponse, RootResponse
from app.services.map_service import MapService

router = APIRouter(tags=["health"])


@router.get("/api/health", response_model=HealthResponse)
def health(map_service: MapService = Depends(get_map_service)) -> HealthResponse:
    return HealthResponse(
        status="loading" if map_service.is_loading() else "ok",
        map_loaded=map_service.has_graph(),
        error=map_service.load_error(),
    )


@router.get("/", response_model=RootResponse)
def root() -> RootResponse:
    return RootResponse(service="OSM Route Finder API", docs="/docs")
