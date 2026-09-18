"""Domain-level exceptions shared across the routing backend.

Previously ``RouteNotFoundError``/``MissingCoordinatesError`` lived in
``pathfinding.py`` and ``InvalidEdgeWeightError`` lived in
``edge_weights.py``, so catching "any routing problem" meant importing from
two different modules. They're consolidated here, and the two new
map-lifecycle exceptions (``MapNotLoadedError``, ``MapLoadingError``,
``MapLoadFailedError``) replace the ad-hoc ``HTTPException`` calls that used
to be scattered through every route handler in ``api.py``. A single
exception-handler registration (see ``app/api/error_handlers.py``) now maps
each of these to the same HTTP status code that handler used to raise
inline.
"""

from __future__ import annotations


class RouteFinderError(Exception):
    """Base class for all domain errors raised by this backend."""


class RouteNotFoundError(RouteFinderError):
    """Raised when no path exists between the requested nodes."""


class MissingCoordinatesError(RouteFinderError):
    """Raised when a node lacks the lon/lat data the A* heuristic needs."""


class InvalidEdgeWeightError(RouteFinderError, ValueError):
    """Raised when an edge has no usable value for a requested route weight.

    Inherits from ``ValueError`` too, so existing ``except ValueError``
    call sites keep working unchanged.
    """


class MapNotLoadedError(RouteFinderError):
    """Raised when an operation needs a graph but none has been loaded yet."""


class MapLoadingError(RouteFinderError):
    """Raised when the default map is still loading in the background."""


class MapLoadFailedError(RouteFinderError):
    """Raised when the background default-map load failed."""


class InvalidMapFileError(RouteFinderError, ValueError):
    """Raised when a map file has an unsupported extension/format."""


class NodeLookupError(RouteFinderError):
    """Raised when a coordinate cannot be resolved to a graph node."""
