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
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent

if (BACKEND_DIR / ".env").is_file():
    load_dotenv(BACKEND_DIR / ".env")
elif (ROOT_DIR / ".env").is_file():
    load_dotenv(ROOT_DIR / ".env")
else:
    load_dotenv()

# Fixed bounds keep startup from scanning every node in the 1+ GB cached
# graph just to compute a bounding box.
_DEFAULT_BOUNDS: list[list[float]] = [[26.6, 45.4], [30.2, 48.6]]

_DEFAULT_CORS_ORIGINS: list[str] = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]


def _default_map_file() -> Path:
    raw = os.getenv("OSM_PBF_PATH")
    if raw:
        path = Path(raw)
        if path.is_file():
            return path
        for base in (BACKEND_DIR, ROOT_DIR, ROOT_DIR / "data", BACKEND_DIR / "data"):
            candidate = base / path
            if candidate.is_file():
                return candidate
        return path

    for candidate in (
        ROOT_DIR / "data" / "moldova.osm.pbf",
        BACKEND_DIR / "data" / "moldova.osm.pbf",
        ROOT_DIR / "moldova.osm.pbf",
        BACKEND_DIR / "moldova.osm.pbf",
    ):
        if candidate.is_file():
            return candidate
    return ROOT_DIR / "data" / "moldova.osm.pbf"


def _cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS")
    if raw:
        origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
        if origins:
            return origins
    return list(_DEFAULT_CORS_ORIGINS)


def _bounds() -> list[list[float]]:
    raw = os.getenv("MAP_BOUNDS")
    if raw:
        try:
            coords = [float(c.strip()) for c in raw.split(",")]
            if len(coords) == 4:
                return [[coords[0], coords[1]], [coords[2], coords[3]]]
        except ValueError:
            pass
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
            self.map_file.with_name(self.map_file.name + ".drive.fastest.graph.pickle"),
        )


settings = Settings()
