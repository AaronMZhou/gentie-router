"""
Correctness checks for the C++ engine against independent implementations.

    python -m engine.test_engine

1. free-switching tree      == scikit-image MCP_Geometric.find_costs (x cell size), full 1M-cell grid
2. two-layer, no run limit  == scipy two-layer reference (engine/reference.py), random pairs, every heuristic x heap
3. run-length constrained   == a pure-Python search over (cell, mode, run) states on small random grids
4. on the real grid with the 450 m rule (avoid-fire weights, so routes do switch): A* (octile, ALT) == Dijkstra,
   every run >= 450 m when the route switches, and the path re-priced in float64 matches the reported cost
"""
import heapq
import math
import random
import time

import numpy as np
from skimage.graph import MCP_Geometric

import config as C
from backend.cost_model import build_cost_grids
from engine import Engine
from engine.reference import TwoLayerTree, build_graph, evaluate, layer_weights

CS = C.CELL_SIZE_M
T = C.TRANSITION_COST_USD
REL = 2e-5  # float32 accumulation along ~1,000-step paths


def close(a, b, rel=REL):
    return abs(a - b) <= rel * max(abs(a), abs(b)) + 1e-3


def brute_force(w, src, dst, transition, min_run, unit, cs):
    """Independent label-setting search over (cell, mode, run) - plus the switch-free case - in pure Python."""
    R, Cn = w[0].shape
    K = math.ceil(min_run / unit - 1e-9) if min_run > 0 else 0
    inc_o = round(cs / unit) if K else 0
    inc_d = math.floor(cs * math.sqrt(2) / unit) if K else 0

    def search(allow_switch, K):
        best = {}
        pq = [(0.0, src[0], src[1], m, 0) for m in (0, 1)]
        while pq:
            g, r, c, m, k = heapq.heappop(pq)
            if (r, c, m, k) in best:
                continue
            best[(r, c, m, k)] = g
            if (r, c) == dst and k == K:
                return g
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    nr, nc = r + dr, c + dc
                    if (dr or dc) and 0 <= nr < R and 0 <= nc < Cn:
                        diag = dr and dc
                        nk = min(k + (inc_d if diag else inc_o), K)
                        step = cs * (math.sqrt(2) if diag else 1)
                        heapq.heappush(pq, (g + (w[m][r, c] + w[m][nr, nc]) / 2 * step, nr, nc, m, nk))
            if allow_switch and k == K:
                heapq.heappush(pq, (g + transition, r, c, 1 - m, 0))
        return math.inf

    if K == 0:
        return search(True, 0)
    return min(search(True, K), search(False, 0))


def run_lengths_m(path, modes, cs):
    """True length of each run, measured along the steps taken in that mode."""
    step = np.hypot(*np.diff(path, axis=0).T) * cs
    runs, cur = [0.0], modes[1] if len(modes) > 1 else modes[0]
    for i, s in enumerate(step):
        m = modes[i + 1]
        if m != cur:
            runs.append(0.0)
            cur = m
        runs[-1] += s
    return runs


def main():
    rng = random.Random(11)
    g = build_cost_grids()
    W = layer_weights(g)
    W32 = [w.astype(np.float32) for w in W]

    # 1. free-switching tree vs scikit-image
    eng1 = Engine(g.routing)
    root = (712, 116)
    t = time.perf_counter(); mine = eng1.tree(root); t_mine = time.perf_counter() - t
    t = time.perf_counter()
    mcp = MCP_Geometric(g.routing, fully_connected=True)
    ref, _ = mcp.find_costs([root])
    t_ref = time.perf_counter() - t
    ref = ref * CS
    rel = np.abs(mine - ref) / np.maximum(ref, 1)
    assert rel.max() < REL, rel.max()
    print(f"1. tree == scikit-image on {g.routing.size:,} cells (max rel err {rel.max():.1e}); "
          f"engine {t_mine * 1000:.0f} ms, MCP_Geometric {t_ref * 1000:.0f} ms")
    del eng1

    # 2. two-layer, no run limit, vs scipy reference
    graph = build_graph(W, T)
    eng = Engine(W32[0], W32[1])
    eng.set_landmarks([(369, 681), (712, 116), (754, 947), (5, 5), (5, 1020), (995, 5), (995, 1020), (500, 515)])
    roots = [(369, 681), (754, 947)]
    trees = {r: TwoLayerTree(graph, g.routing.shape, r) for r in roots}
    n = 0
    for _ in range(40):
        dst = rng.choice(roots)
        src = (rng.randrange(g.routing.shape[0]), rng.randrange(g.routing.shape[1]))
        want = trees[dst].cost(src)
        for h in ("dijkstra", "octile", "alt"):
            for heap in ("radix", "binary"):
                got = eng.route(src, dst, T, heuristic=h, heap=heap)
                assert close(got.cost, want), (src, dst, h, heap, got.cost, want)
                obj, _ = evaluate(got.path, got.modes, W, T)
                assert close(obj, want), (obj, want)
                n += 1
    del graph, trees
    print(f"2. two-layer search == scipy reference: {n} searches (40 pairs x 3 heuristics x 2 heaps)")

    # 3. constrained, small random grids vs pure-Python label setting
    n = 0
    for trial in range(60):
        R, Cn = rng.randint(4, 14), rng.randint(4, 14)
        npr = np.random.default_rng(trial)
        w = [npr.uniform(500, 9000, (R, Cn)).astype(np.float32), npr.uniform(500, 9000, (R, Cn)).astype(np.float32)]
        e = Engine(w[0], w[1])
        e.set_landmarks([(0, 0), (R - 1, Cn - 1)])
        for _ in range(4):
            src = (rng.randrange(R), rng.randrange(Cn))
            dst = (rng.randrange(R), rng.randrange(Cn))
            trans = rng.choice([0.0, 20_000.0, 150_000.0])
            min_run = rng.choice([0.0, 60.0, 100.0, 200.0])
            want = brute_force([x.astype(np.float64) for x in w], src, dst, trans, min_run, 10.0, CS)
            for h in ("dijkstra", "alt"):
                got = e.route(src, dst, trans, min_run, 10.0, heuristic=h)
                assert close(got.cost, want), (trial, src, dst, trans, min_run, h, got.cost, want)
                n += 1
    print(f"3. constrained search == pure-Python label setting on random grids: {n} searches")

    # 4. real grid, 450 m rule: heuristics agree, runs are long enough, cost re-prices.  Avoid-fire weights:
    # balanced routes almost never switch mode, which would leave the run-length check untested.
    del eng
    W = layer_weights(build_cost_grids(overrides=C.PRESETS["avoid_fire"]["overrides"]))
    eng = Engine(W[0].astype(np.float32), W[1].astype(np.float32))
    eng.set_landmarks([(369, 681), (712, 116), (754, 947), (5, 5), (5, 1020), (995, 5), (995, 1020), (500, 515)])
    worst_run, n, switching = math.inf, 0, 0
    for i in range(25):
        dst = rng.choice([(369, 681), (712, 116), (754, 947)])
        src = (rng.randrange(150, 850), rng.randrange(150, 880))
        # constrained Dijkstra explores ~20M states (~12 s), so the three-way cross-check runs on the first 5
        hs = ("dijkstra", "octile", "alt") if i < 5 else ("alt",)
        res = {h: eng.route(src, dst, T, C.MIN_SEGMENT_M, heuristic=h) for h in hs}
        base = res[hs[0]].cost
        assert all(close(r.cost, base) for r in res.values()), {h: r.cost for h, r in res.items()}
        r = res["alt"]
        assert close(evaluate(r.path, r.modes, W, T)[0], r.cost)
        runs = run_lengths_m(r.path, r.modes, CS)
        if len(runs) > 1:
            switching += 1
            worst_run = min(worst_run, min(runs))
            assert min(runs) >= C.MIN_SEGMENT_M - 1e-6, runs
        assert "dijkstra" not in res or res["alt"].expanded <= res["dijkstra"].expanded
        n += 1
    assert switching >= 10, f"only {switching} of {n} routes switch mode: the run-length check barely ran"
    print(f"4. 450 m rule on the real grid: {n} routes (A* == Dijkstra on 5); {switching} switch mode, "
          f"shortest run {worst_run:.0f} m")
    print("\nALL ENGINE CHECKS PASSED")


if __name__ == "__main__":
    main()
