"""Graph edge speed and travel-time annotation utilities."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any


DEFAULT_SPEED_BY_HIGHWAY_KPH: dict[str, float] = {
    "motorway": 110.0,
    "motorway_link": 80.0,
    "trunk": 90.0,
    "trunk_link": 70.0,
    "primary": 60.0,
    "primary_link": 50.0,
    "secondary": 50.0,
    "secondary_link": 40.0,
    "tertiary": 40.0,
    "tertiary_link": 30.0,
    "unclassified": 20.0,
    "residential": 20.0,
    "living_street": 10.0,
    "service": 10.0,
    "track": 5.0,
    "path": 3.0,
}

UNIVERSAL_DEFAULT_SPEED_KPH = 30.0
MAX_PLAUSIBLE_SPEED_KPH = 130.0
MAX_PLAUSIBLE_SPEED_MPS = MAX_PLAUSIBLE_SPEED_KPH / 3.6

_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")


class InvalidEdgeWeightError(ValueError):
    """Raised when an edge has no usable value for a requested route weight."""


class GraphAttributeAnnotator:
    """Populate derived routing attributes on graph edges.

    The annotator is the single place that knows how to interpret OSM speed
    tags, apply highway-class speed fallbacks, normalize missing lengths, and
    compute travel-time weights used by routing.
    """

    def __init__(
        self,
        speed_by_highway_kph: Mapping[str, float] | None = None,
        universal_default_speed_kph: float = UNIVERSAL_DEFAULT_SPEED_KPH,
    ) -> None:
        self.speed_by_highway_kph = dict(
            speed_by_highway_kph or DEFAULT_SPEED_BY_HIGHWAY_KPH
        )
        self.universal_default_speed_kph = universal_default_speed_kph

    def annotate(self, graph):
        """Attach normalized ``length``, ``speed_kph`` and ``travel_time`` in-place."""
        for _, _, data in graph.edges(data=True):
            length_m = self.length_m_for_edge(data)
            speed_kph = self.speed_kph_for_edge(data)
            speed_mps = speed_kph * 1000.0 / 3600.0

            data["length"] = length_m
            data["speed_kph"] = speed_kph
            data["travel_time"] = length_m / speed_mps if speed_mps > 0 else 0.0
        return graph

    @staticmethod
    def parse_maxspeed_kph(raw: Any) -> float | None:
        """Parse an OSM ``maxspeed`` tag into km/h, returning ``None`` if unusable."""
        if raw is None:
            return None

        if isinstance(raw, float) and math.isnan(raw):
            return None

        if isinstance(raw, (int, float)):
            value = float(raw)
            return value if value > 0 else None

        if isinstance(raw, (list, tuple, set)):
            parsed = [
                GraphAttributeAnnotator.parse_maxspeed_kph(item) for item in raw
            ]
            parsed = [value for value in parsed if value is not None]
            return min(parsed) if parsed else None

        if isinstance(raw, str):
            text = raw.strip().lower()
            if not text or text in {"none", "signals", "walk", "variable"}:
                return None
            match = _NUMBER_RE.search(text)
            if not match:
                return None
            value = float(match.group(1))
            if "mph" in text:
                value *= 1.60934
            return value if value > 0 else None

        return None

    def speed_kph_for_edge(self, edge_data: Mapping[str, Any]) -> float:
        """Resolve speed from ``maxspeed``, highway defaults, then universal fallback."""
        parsed = self.parse_maxspeed_kph(edge_data.get("maxspeed"))
        if parsed:
            return parsed

        highway = edge_data.get("highway")
        highways = highway if isinstance(highway, (list, tuple, set)) else [highway]
        for highway_type in highways:
            if highway_type in self.speed_by_highway_kph:
                return self.speed_by_highway_kph[highway_type]

        return self.universal_default_speed_kph

    @staticmethod
    def length_m_for_edge(edge_data: Mapping[str, Any]) -> float:
        """Return a non-negative edge length in metres, defaulting missing values to 0."""
        raw_length = edge_data.get("length")
        if raw_length is None:
            return 0.0
        try:
            length = float(raw_length)
        except (TypeError, ValueError):
            return 0.0
        if math.isnan(length) or length < 0:
            return 0.0
        return length


def annotate_graph(graph):
    """Annotate a graph with routing attributes using the default annotator."""
    return GraphAttributeAnnotator().annotate(graph)


def validate_edge_weight(edge_data: Mapping[str, Any], weight: str, u: Any, v: Any) -> float:
    """Return a finite edge weight or raise a clear domain exception."""
    if weight not in edge_data:
        raise InvalidEdgeWeightError(
            f"Edge {u!r} -> {v!r} has no '{weight}' attribute. "
            "Load the graph through MapEngine so routing attributes are initialized."
        )
    try:
        value = float(edge_data[weight])
    except (TypeError, ValueError) as exc:
        raise InvalidEdgeWeightError(
            f"Edge {u!r} -> {v!r} has invalid '{weight}' value: "
            f"{edge_data[weight]!r}."
        ) from exc
    if math.isnan(value) or value < 0:
        raise InvalidEdgeWeightError(
            f"Edge {u!r} -> {v!r} has invalid '{weight}' value: {value!r}."
        )
    return value
