"""
How far is the production pipeline (free-switching search, then mode smoothing + transition charges)
from the exact two-layer optimum?  One preset's search weights, random sites routed to the three substations.

    python -m engine.study --n 1000 [--preset underground]

Prints, per route set: lower bound (free switching, what the production search minimises) <=
exact optimum <= production result, all scored by reference.evaluate under the same model.
"""
import argparse
import json
import random
import time

import numpy as np

import config as C
from backend.cost_model import build_cost_grids
from backend.router import Router, RouteError, smooth_modes
from engine.reference import TwoLayerTree, build_graph, evaluate, layer_weights


def cell_lengths(path, cs=C.CELL_SIZE_M):
    step = np.hypot(*np.diff(path, axis=0).T) * cs
    return np.r_[step, 0] / 2 + np.r_[0, step] / 2


def run_lengths(modes, cell_len):
    edges = np.flatnonzero(np.diff(modes)) + 1
    return [cell_len[s:e].sum() for s, e in zip(np.r_[0, edges], np.r_[edges, len(modes)])]


def sample_sites(router, n, seed):
    rng = random.Random(seed)
    subs = [f["geometry"]["coordinates"] for f in json.loads((C.OVERLAY_DIR / "substations.geojson").read_text())["features"]]
    out = []
    while len(out) < n:
        s = (rng.uniform(C.BBOX[0], C.BBOX[2]), rng.uniform(C.BBOX[1], C.BBOX[3]))
        e = tuple(rng.choice(subs))
        try:
            (_, s_rc), (_, e_rc) = router._validate(s, "start"), router._validate(e, "end")
        except RouteError:
            continue
        if s_rc != e_rc:
            out.append((s_rc, e_rc))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--preset", default="balanced", choices=list(C.PRESETS))
    args = ap.parse_args()

    router = Router(build_cost_grids())
    g = router._search_grid(args.preset)  # the preset's search weights (production prices on balanced; here both sides use these)
    T = C.TRANSITION_COST_USD
    W = layer_weights(g)                                              # routing weights (with penalties)
    D = [g.overhead.astype(np.float64), g.underground.astype(np.float64)]  # plain dollars
    t = time.perf_counter()
    graph = build_graph(W, T)
    print(f"two-layer graph: {graph.shape[0]:,} nodes, {graph.nnz:,} edges, built in {time.perf_counter() - t:.1f}s")

    sites = sample_sites(router, args.n, args.seed)
    trees = {}
    for e_rc in sorted({e for _, e in sites}):
        t = time.perf_counter()
        trees[e_rc] = TwoLayerTree(graph, g.routing.shape, e_rc)
        print(f"  two-layer tree at {e_rc}: {time.perf_counter() - t:.2f}s")

    wmin = np.minimum(W[0], W[1])
    rows = []
    for s_rc, e_rc in sites:
        old = router.least_cost_path(s_rc, e_rc, preset=args.preset)                       # free-switching optimum (tree traceback)
        cl = cell_lengths(old)
        old_modes = smooth_modes(g.mode[old[:, 0], old[:, 1]].astype(np.int8), cl, C.MIN_SEGMENT_M)
        lb = float(((wmin[old[:-1, 0], old[:-1, 1]] + wmin[old[1:, 0], old[1:, 1]]) / 2
                    * np.hypot(*np.diff(old, axis=0).T) * C.CELL_SIZE_M).sum())
        old_obj, old_tr = evaluate(old, old_modes, W, T)
        old_usd, _ = evaluate(old, old_modes, D, T)

        new, new_modes = trees[e_rc].path(s_rc)
        new_obj, new_tr = evaluate(new, new_modes, W, T)
        new_usd, _ = evaluate(new, new_modes, D, T)
        opt = trees[e_rc].cost(s_rc)
        assert abs(new_obj - opt) <= 1e-6 * opt + 1, (new_obj, opt)
        assert lb <= opt * (1 + 1e-9) + 1 and opt <= old_obj * (1 + 1e-9) + 1, (lb, opt, old_obj)
        short = [L for L in run_lengths(new_modes, cell_lengths(new))[1:-1] if L < C.MIN_SEGMENT_M] if new_tr else []
        ends_short = new_tr and min(run_lengths(new_modes, cell_lengths(new))[0], run_lengths(new_modes, cell_lengths(new))[-1]) < C.MIN_SEGMENT_M
        rows.append(dict(lb=lb, opt=opt, old=old_obj, old_usd=old_usd, new_usd=new_usd, old_tr=old_tr, new_tr=new_tr,
                         short_inner=len(short), ends_short=bool(ends_short)))

    a = {k: np.array([r[k] for r in rows], dtype=float) for k in rows[0]}
    gap = a["old"] / a["opt"] - 1
    print(f"\n{len(rows)} routes ({args.preset} preset)")
    print(f"  production objective vs exact optimum: mean +{gap.mean():.2%}, median +{np.median(gap):.2%}, "
          f"p90 +{np.quantile(gap, .9):.2%}, max +{gap.max():.2%}")
    print(f"  routes where the optimum is >= 1% / 5% cheaper: {(gap >= .01).mean():.0%} / {(gap >= .05).mean():.0%}")
    print(f"  total objective: production {a['old'].sum() / 1e9:.3f}B, optimum {a['opt'].sum() / 1e9:.3f}B, "
          f"free-switching bound {a['lb'].sum() / 1e9:.3f}B")
    print(f"  plain dollars: production ${a['old_usd'].sum() / 1e6:,.0f}M vs optimum ${a['new_usd'].sum() / 1e6:,.0f}M "
          f"({a['new_usd'].sum() / a['old_usd'].sum() - 1:+.2%})")
    print(f"  transitions per route: production {a['old_tr'].mean():.2f}, optimum {a['new_tr'].mean():.2f}")
    print(f"  optimum routes with an inner run < {C.MIN_SEGMENT_M:g} m: {(a['short_inner'] > 0).mean():.1%}; "
          f"with a short first/last run: {a['ends_short'].mean():.1%}")


if __name__ == "__main__":
    main()
