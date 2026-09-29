"""Map loading and road-network endpoints."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.api.deps import get_map_service
from app.schemas import MapMetadataResponse
from app.services.map_service import MapService

logger = logging.getLogger("route_finder")
router = APIRouter(tags=["maps"])

_ALLOWED_SUFFIXES = {".osm", ".osm.pbf"}


@router.get("/api/maps/current", response_model=MapMetadataResponse)
def current_map(map_service: MapService = Depends(get_map_service)) -> dict:
    # Do not wait on the graph-building lock. The frontend polls this
    # endpoint while startup work is running and needs an immediate
    # loading response.
    return map_service.current_map_status()


@router.post("/api/maps")
async def upload_map(
    file: UploadFile = File(...), map_service: MapService = Depends(get_map_service)
) -> dict:
    filename = file.filename or ""
    suffix = ".osm.pbf" if filename.lower().endswith(".osm.pbf") else Path(filename).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="Upload an .osm or .osm.pbf file.")

    temp_path = None
    try:
        logger.info("Received map upload: %s", filename)
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_path = temp_file.name
            shutil.copyfileobj(file.file, temp_file)
        logger.info("Upload saved temporarily: %.1f MB", os.path.getsize(temp_path) / 1024**2)
        return await run_in_threadpool(map_service.load_map_from_path, temp_path)
    except HTTPException:
        raise
    except Exception as exc:  # Surface parser errors to the UI without a traceback.
        logger.exception("Map loading failed")
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await file.close()
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
            logger.info("Temporary upload deleted")


@router.get("/api/maps/roads")
def map_roads(map_service: MapService = Depends(get_map_service)) -> dict:
    return map_service.roads_geojson()
