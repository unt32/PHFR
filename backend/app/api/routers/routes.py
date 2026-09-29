"""Route computation endpoint."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_map_service
from app.core.exceptions import MapNotLoadedError
from app.schemas import RouteRequest, RouteResponse
from app.services.map_service import MapService

logger = logging.getLogger("route_finder")
router = APIRouter(tags=["routes"])


@router.post("/api/routes", response_model=RouteResponse)
def create_route(
    request: RouteRequest, map_service: MapService = Depends(get_map_service)
) -> dict:
    started_at = time.perf_counter()
    try:
        # A single, unified routing algorithm - always the fastest route
        # by estimated travel time. There is no shortest/fastest choice.
        nodes, stats, coordinates = map_service.compute_route(
            request.start_node, request.end_node
        )
    except MapNotLoadedError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    logger.info(
        "Route built in %.3f seconds (%s nodes)", time.perf_counter() - started_at, len(nodes)
    )
    return {
        "route": {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coordinates}},
        "stats": stats,
        "node_count": len(nodes),
    }
