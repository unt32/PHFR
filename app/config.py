"""Centralized, environment-driven configuration.

Every value here used to be a module-level constant or a hardcoded literal
inside ``api.py``. Pulling them into one ``Settings`` object means the same
knobs (map file location, CORS origins, log level) can be changed per
environment (dev / staging / prod) without touching code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Project root: the parent of this ``app`` package, i.e. where
# moldova.osm.pbf, start.sh and requirements-api.txt live.
BASE_DIR = Path(__file__).resolve().parent.parent

# Fixed bounds keep startup from scanning every node in the 1+ GB cached
# graph just to compute a bounding box.
_DEFAULT_BOUNDS: list[list[float]] = [[26.6, 45.4], [30.2, 48.6]]

_DEFAULT_CORS_ORIGINS: list[str] = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://172.20.10.2:3000",
]


def _default_map_file() -> Path:
    return Path(os.getenv("OSM_PBF_PATH", BASE_DIR / "moldova.osm.pbf"))


def _cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS")
    if raw:
        origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
        if origins:
            return origins
    return list(_DEFAULT_CORS_ORIGINS)


def _bounds() -> list[list[float]]:
    return [list(pair) for pair in _DEFAULT_BOUNDS]


@dataclass(frozen=True)
class Settings:
    """Immutable, process-wide configuration resolved once at import time."""

    map_file: Path = field(default_factory=_default_map_file)
    bounds: list[list[float]] = field(default_factory=_bounds)
    cors_origins: list[str] = field(default_factory=_cors_origins)
    network_type: str = "drive"
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    graph_cache_file: Path = field(init=False)

    def __post_init__(self) -> None:
        # Mirrors the old DEFAULT_GRAPH_CACHE derivation exactly, so an
        # already-built cache on disk is still picked up after this refactor.
        object.__setattr__(
            self,
            "graph_cache_file",
            self.map_file.with_suffix(".drive.fastest.graph.pickle"),
        )


settings = Settings()
