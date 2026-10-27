"""
Engine performance on the real 1,001 x 1,030 grid (balanced preset weights).

    python -m engine.bench_engine

1. single-source tree on the free-switching grid: engine (radix heap, binary heap) vs scikit-image MCP_Geometric
2. point-to-point mode-aware search, without and with the 450 m run rule: Dijkstra vs A* (octile) vs A* (ALT),
   for routes ending at a substation (a landmark sits there) and between two arbitrary cells (it doesn't)

Writes engine/bench_results.json; engine/RESULTS.md quotes it.
"""
import json
import platform
import random
import statistics
import time
from pathlib import Path

import numpy as np
from skimage.graph import MCP_Geometric

import config as C
from backend.cost_model import build_cost_grids
from engine import Engine
from engine.reference import layer_weights

HERE = Path(__file__).resolve().parent
SUBS = [(369, 681), (712, 116), (754, 947)]
LANDMARKS = SUBS + [(5, 5), (5, 1020), (995, 5), (995, 1020), (500, 515)]
T = C.TRANSITION_COST_USD


def ms(fn, reps):
    out = []
    for _ in range(reps):
        t = time.perf_counter()
        r = fn()
        out.append((time.perf_counter() - t) * 1000)
    return statistics.median(out), r


def main():
    g = build_cost_grids()
    W = [w.astype(np.float32) for w in layer_weights(g)]
    res = {"grid": list(g.routing.shape), "python": platform.python_version(), "cpu": platform.processor(),
           "os": platform.platform(), "tree": {}, "route": []}

    print("1. single-source tree (free-switching grid, 1.03M cells)")
    single = Engine(g.routing)
    root = SUBS[1]
    for heap in ("radix", "binary"):
        res["tree"][f"engine_{heap}"] = ms(lambda: single.tree(root, heap), 7)[0]
    res["tree"]["skimage_mcp_geometric"] = ms(lambda: MCP_Geometric(g.routing, fully_connected=True).find_costs([root]), 7)[0]
    for k, v in res["tree"].items():
        print(f"   {k:24s} {v:8.1f} ms")
    del single

    eng = Engine(W[0], W[1])
    eng.set_landmarks(LANDMARKS)
    rng = random.Random(5)
    eng.route((500, 500), SUBS[0], T, C.MIN_SEGMENT_M)  # allocate the state buffers outside the timings
    pairs = {"to substation": [((rng.randrange(150, 850), rng.randrange(150, 880)), rng.choice(SUBS)) for _ in range(12)],
             "arbitrary cells": [((rng.randrange(150, 850), rng.randrange(150, 880)),
                                  (rng.randrange(150, 850), rng.randrange(150, 880))) for _ in range(12)]}
    print("\n2. point-to-point mode-aware search (median over pairs)")
    for kind, ps in pairs.items():
        for rule in (0.0, C.MIN_SEGMENT_M):
            for h, heap in (("dijkstra", "binary"), ("dijkstra", "radix"), ("octile", "radix"), ("alt", "radix")):
                sample = ps if not (rule and h == "dijkstra") else ps[:4]  # constrained Dijkstra: ~10 s each
                times, exp, costs = [], [], []
                for s, e in sample:
                    t, r = ms(lambda: eng.route(s, e, T, rule, heuristic=h, heap=heap), 1)
                    times.append(t)
                    exp.append(r.expanded)
                    costs.append(r.cost)
                row = {"pairs": kind, "min_run_m": rule, "heuristic": h, "heap": heap, "n": len(sample),
                       "ms_median": statistics.median(times), "expanded_median": statistics.median(exp)}
                res["route"].append(row)
                print(f"   {kind:15s} run>={rule:3.0f} m  {h:8s} {heap:6s}  {row['ms_median']:9.1f} ms  "
                      f"{row['expanded_median']:>12,.0f} states  (n={len(sample)})")
    (HERE / "bench_results.json").write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
