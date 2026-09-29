"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Request

from app.services.map_service import MapService


def get_map_service(request: Request) -> MapService:
    """Retrieve the single MapService instance owned by the running app."""
    return request.app.state.map_service
