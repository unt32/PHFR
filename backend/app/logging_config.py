"""Application logging setup.

Extracted from the old module-level ``logging.basicConfig(...)`` call in
``api.py`` so logging is configured explicitly by the app factory rather
than as an import side effect.
"""

from __future__ import annotations

import logging

LOGGER_NAME = "route_finder"


def configure_logging(level: str = "INFO") -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    return logging.getLogger(LOGGER_NAME)
