"""Central mapping from domain exceptions to HTTP responses.

The old ``api.py`` had ``raise HTTPException(status_code=..., detail=...)``
repeated inline in nearly every route handler, each one re-deriving the
right status code for "no map loaded" (409), "still loading" (503), etc.
Registering one handler per exception type here means a route handler just
lets a ``MapNotLoadedError`` (for example) propagate, and it is turned into
the same ``409 {"detail": "..."}`` response the old inline code produced.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    InvalidEdgeWeightError,
    InvalidMapFileError,
    MapLoadFailedError,
    MapLoadingError,
    MapNotLoadedError,
    MissingCoordinatesError,
    NodeLookupError,
    RouteNotFoundError,
)

_STATUS_BY_EXCEPTION: dict[type[Exception], int] = {
    MapNotLoadedError: 409,
    MapLoadingError: 503,
    MapLoadFailedError: 500,
    InvalidMapFileError: 400,
    RouteNotFoundError: 422,
    MissingCoordinatesError: 422,
    InvalidEdgeWeightError: 422,
    NodeLookupError: 422,
}


def _make_handler(status_code: int):
    async def _handler(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    return _handler


def register_exception_handlers(app: FastAPI) -> None:
    for exc_type, status_code in _STATUS_BY_EXCEPTION.items():
        app.add_exception_handler(exc_type, _make_handler(status_code))
