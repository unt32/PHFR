"""Application factory and the FastAPI app instance.

Run with:

    uvicorn app.main:app --reload

(``uvicorn api:app`` from the project root still works too - see the
top-level ``api.py`` compatibility shim.)
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.error_handlers import register_exception_handlers
from app.api.routers import health, maps, nodes, routes
from app.config import settings
from app.logging_config import configure_logging
from app.services.map_service import MapService

logger = configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.map_file.is_file():
        raise RuntimeError(f"Default map PBF file was not found: {settings.map_file}")
    logger.info("Starting default map initialization: %s", settings.map_file)
    app.state.map_service.start_background_load()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="OSM Route Finder API", lifespan=lifespan)

    app.state.map_service = MapService(
        map_file=settings.map_file,
        graph_cache_file=settings.graph_cache_file,
        bounds=settings.bounds,
        network_type=settings.network_type,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(maps.router)
    app.include_router(nodes.router)
    app.include_router(routes.router)

    return app


app = create_app()
