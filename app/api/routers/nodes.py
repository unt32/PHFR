"""Coordinate-to-node lookup endpoint."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_map_service
from app.core.exceptions import MapNotLoadedError
from app.schemas import Coordinate, NearestNodeResponse
from app.services.map_service import MapService

logger = logging.getLogger("route_finder")
router = APIRouter(tags=["nodes"])


@router.post("/api/nodes/nearest", response_model=NearestNodeResponse)
def nearest_node(point: Coordinate, map_service: MapService = Depends(get_map_service)) -> dict:
    started_at = time.perf_counter()
    try:
        node_id, lon, lat = map_service.nearest_node(point.lon, point.lat)
    except MapNotLoadedError:
        # Let the registered exception handler turn this into a 409, same as
        # every other "no map loaded" case.
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info("Nearest node selected in %.3f seconds", time.perf_counter() - started_at)
    return {"node_id": node_id, "lon": lon, "lat": lat}
