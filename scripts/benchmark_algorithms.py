#!/usr/bin/env python3
"""
scripts/benchmark_algorithms.py
--------------------------------
Standalone benchmark harness for the routing algorithms in
``app.core.pathfinding`` (currently Dijkstra and A*).

Loads a graph, samples N random (origin, destination) node pairs, runs every
algorithm on every pair, and records timing, peak memory, graph size, and
path stats to a JSON Lines file for later analysis.

This script has no side effects on production code paths - MapEngine no
longer runs any of this on every ``compute_route`` call, so this is now the
only place benchmarking happens.

Usage
-----
    python scripts/benchmark_algorithms.py path/to/map.osm --pairs 20
    python scripts/benchmark_algorithms.py path/to/map.osm.pbf --pairs 50 \\
        --weight travel_time --output results/bench.jsonl --seed 42

    # Reuse the same pickled graph cache the API serves from - skips the
    # expensive .pbf parse entirely.
    python scripts/benchmark_algorithms.py moldova.osm.drive.fastest.graph.pickle --pairs 50

A ``.pickle``/``.pkl`` file is unpickled directly and published via
``MapEngine.load_from_graph`` (re-annotated the same way as any other
graph entering the engine, matching what ``MapService`` does on startup).
Any other extension goes through ``MapEngine.load_from_file`` -> the
regular ``.osm``/``.osm.pbf`` loaders in ``app.core.loaders``. This script
never writes a pickle cache itself; it only reads one if you point it at
an existing file.

Memory measurement
-------------------
Peak memory per algorithm run is measured with ``tracemalloc``, which is
stdlib-only and tracks *Python-level* allocations (i.e. the heapq frontier,
the came_from/cost dicts, etc.) - the part of memory usage that actually
varies between algorithms and graphs. If ``psutil`` is installed, the
process-wide RSS is also sampled before/after each run and included as a
secondary metric, but tracemalloc's peak is the primary number since RSS is
too noisy (GC timing, OS paging) to compare individual short calls fairly.
"""

from __future__ import annotations

import argparse
import json
import pickle
import random
import sys
import time
import tracemalloc
from pathlib import Path

# Make the project root importable when this script lives under scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.exceptions import (  # noqa: E402
    InvalidEdgeWeightError,
    MissingCoordinatesError,
    RouteNotFoundError,
)
from app.core.map_engine import MapEngine  # noqa: E402
from app.core.pathfinding import astar_path, dijkstra_path  # noqa: E402

try:
    import psutil

    _PROCESS = psutil.Process()
except ImportError:
    psutil = None
    _PROCESS = None

# Algorithms under test. Add new entries here (name -> callable) and they
# are automatically picked up by the benchmark loop below.
ALGORITHMS = (
    ("dijkstra", dijkstra_path),
    ("astar", astar_path),
)

ROUTING_ERRORS = (
    RouteNotFoundError,
    KeyError,
    MissingCoordinatesError,
    InvalidEdgeWeightError,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Benchmark routing algorithms on random node pairs."
    )
    parser.add_argument(
        "map_file",
        help=(
            "Path to a .osm, .osm.pbf, or pre-built .pickle/.pkl graph "
            "cache to load (a pickle skips the .pbf parse entirely)."
        ),
    )
    parser.add_argument(
        "--pairs", "-n", type=int, default=20,
        help="Number of random (origin, destination) pairs to sample (default: 20).",
    )
    parser.add_argument(
        "--weight", "-w", default="travel_time",
        help="Edge attribute to minimize (default: travel_time).",
    )
    parser.add_argument(
        "--network-type", default="drive",
        help="Network type passed to the loader for .pbf files (default: drive).",
    )
    parser.add_argument(
        "--output", "-o", default="benchmark_results.jsonl",
        help="Path to the output JSON Lines file (default: benchmark_results.jsonl).",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for reproducible node-pair sampling.",
    )
    return parser.parse_args()


PICKLE_SUFFIXES = (".pickle", ".pkl")


def load_engine(map_file: str, network_type: str) -> MapEngine:
    """Load the graph via MapEngine so it gets the same annotation (length,
    speed_kph, travel_time) that production routing relies on.

    ``.pickle``/``.pkl`` files are unpickled directly and published with
    ``load_from_graph`` - the same cache ``MapService`` reads from on
    startup, so a benchmark run doesn't have to re-parse the .pbf every
    time. Anything else goes through the normal ``.osm``/``.osm.pbf``
    loaders via ``load_from_file``.
    """
    engine = MapEngine(network_type=network_type)
    path = Path(map_file)

    if path.suffix.lower() in PICKLE_SUFFIXES:
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {map_file}")
        with open(path, "rb") as f:
            graph = pickle.load(f)
        engine.load_from_graph(graph, f"Pickle cache: {path.name}")
    else:
        engine.load_from_file(map_file)

    return engine


def sample_node_pairs(graph, n: int, rng: random.Random):
    """Pick n distinct (orig, dest) node pairs, orig != dest for each pair."""
    node_ids = list(graph.nodes)
    if len(node_ids) < 2:
        raise RuntimeError("Graph has fewer than 2 nodes; cannot sample pairs.")

    pairs = []
    for _ in range(n):
        orig, dest = rng.sample(node_ids, 2)
        pairs.append((orig, dest))
    return pairs


def run_one(engine: MapEngine, algorithm, orig_node, dest_node, weight: str):
    """Run a single algorithm on a single node pair, measuring time and peak
    memory. Never raises - failures are captured in the returned record.
    """
    graph = engine.graph

    rss_before = _PROCESS.memory_info().rss if _PROCESS else None

    tracemalloc.start()
    start = time.perf_counter_ns()
    try:
        route = algorithm(graph, orig_node, dest_node, weight=weight)
    except ROUTING_ERRORS as exc:
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss_after = _PROCESS.memory_info().rss if _PROCESS else None
        return {
            "status": "error",
            "error": str(exc),
            "error_type": type(exc).__name__,
            "elapsed_ms": elapsed_ms,
            "peak_memory_bytes": peak_bytes,
            "rss_delta_bytes": (
                rss_after - rss_before
                if rss_before is not None and rss_after is not None
                else None
            ),
        }

    elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = _PROCESS.memory_info().rss if _PROCESS else None

    stats = engine.route_stats(route)
    return {
        "status": "ok",
        "elapsed_ms": elapsed_ms,
        "peak_memory_bytes": peak_bytes,
        "rss_delta_bytes": (
            rss_after - rss_before
            if rss_before is not None and rss_after is not None
            else None
        ),
        "path_node_count": stats["node_count"],
        "distance_km": stats["distance_km"],
        "time_min": stats["time_min"],
    }


def main():
    args = parse_args()
    rng = random.Random(args.seed)

    print(f"Loading graph from {args.map_file} ...")
    engine = load_engine(args.map_file, args.network_type)
    graph = engine.graph
    graph_nodes = graph.number_of_nodes()
    graph_edges = graph.number_of_edges()
    print(f"Graph loaded: {graph_nodes} nodes, {graph_edges} edges.")

    pairs = sample_node_pairs(graph, args.pairs, rng)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(
        f"Running {len(ALGORITHMS)} algorithm(s) on {len(pairs)} pair(s), "
        f"weight={args.weight!r} ..."
    )

    written = 0
    with open(output_path, "a", encoding="utf-8") as f:
        for pair_index, (orig_node, dest_node) in enumerate(pairs):
            for algo_name, algo_fn in ALGORITHMS:
                result = run_one(engine, algo_fn, orig_node, dest_node, args.weight)
                record = {
                    "timestamp": time.time(),
                    "map_file": args.map_file,
                    "pair_index": pair_index,
                    "orig_node": orig_node,
                    "dest_node": dest_node,
                    "algorithm": algo_name,
                    "weight": args.weight,
                    "graph_nodes": graph_nodes,
                    "graph_edges": graph_edges,
                    "psutil_available": psutil is not None,
                    **result,
                }
                f.write(json.dumps(record) + "\n")
                written += 1

                status_flag = "OK " if result["status"] == "ok" else "ERR"
                print(
                    f"[{status_flag}] pair={pair_index:>3} algo={algo_name:<10} "
                    f"time={result['elapsed_ms']:>8.3f}ms "
                    f"peak_mem={result['peak_memory_bytes']:>10} bytes"
                )

    print(f"\nWrote {written} records to {output_path}")


if __name__ == "__main__":
    main()
