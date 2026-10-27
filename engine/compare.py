"""
Fair comparison: the production pipeline vs the engine's exact optimum under the same rules.

    python -m engine.compare --n 300

Both sides search the preset's weights, both obey the 450 m minimum run, both pay $750k per transition.
  production = free-switching search -> string-pulling -> per-cell cheaper mode -> flip runs < 450 m
  engine     = exact search over (cell, mode, run length), A* with ALT bounds (engine/gentie_search.cpp)
Scored on the same grid model (reference.evaluate): the preset's search objective (what both minimise,
penalties included) and plain dollars on the balanced cost model (how production prices every preset).

Writes engine/compare_results.json; engine/RESULTS.md quotes it.
"""
import argparse
import json
import platform
import time
from pathlib import Path

import numpy as np

import config as C
from backend.cost_model import build_cost_grids
from backend.router import Router, smooth_modes
from engine import Engine
from engine.reference import evaluate, layer_weights
from engine.study import cell_lengths, run_lengths, sample_sites

HERE = Path(__file__).resolve().parent
LANDMARKS = [(5, 5), (5, 1020), (995, 5), (995, 1020), (500, 515)]  # corners + centre; substations added below


def production(router, sg, s_rc, e_rc, preset, straighten=True):
    path = router.least_cost_path(s_rc, e_rc, preset=preset)
    if straighten:
        path = router.straighten(path, sg.routing)
    modes = smooth_modes(sg.mode[path[:, 0], path[:, 1]].astype(np.int8), cell_lengths(path), C.MIN_SEGMENT_M)
    return path, modes


def high_hazard_oh_km(g, path, modes):
    """km of overhead line through WHP high-hazard cells (step i is in modes[i + 1])."""
    step = np.hypot(*np.diff(path, axis=0).T) * C.CELL_SIZE_M
    hz = g.fire_risk[path[1:, 0], path[1:, 1]] >= C.HIGH_RISK_THRESHOLD
    return float(step[(modes[1:] == 0) & hz].sum() / 1000)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--presets", default="avoid_fire,underground,balanced")
    args = ap.parse_args()

    router = Router(build_cost_grids())
    bal = router.g
    D = [bal.overhead.astype(np.float64), bal.underground.astype(np.float64)]  # balanced dollars
    T = C.TRANSITION_COST_USD
    sites = sample_sites(router, args.n, args.seed)
    subs = sorted({e for _, e in sites})
    out = {"n": len(sites), "seed": args.seed, "presets": {}, "python": platform.python_version(),
           "cpu": platform.processor()}

    for preset in args.presets.split(","):
        sg = router._search_grid(preset)
        W = layer_weights(sg)
        eng = Engine(W[0].astype(np.float32), W[1].astype(np.float32), C.CELL_SIZE_M)
        eng.set_landmarks(subs + LANDMARKS)
        rows, t_eng, t_prod = [], [], []
        for s_rc, e_rc in sites:
            t = time.perf_counter()
            p_path, p_modes = production(router, sg, s_rc, e_rc, preset)
            t_prod.append(time.perf_counter() - t)
            r_path, r_modes = production(router, sg, s_rc, e_rc, preset, straighten=False)
            t = time.perf_counter()
            r = eng.route(s_rc, e_rc, T, C.MIN_SEGMENT_M, heuristic="alt")
            t_eng.append(time.perf_counter() - t)
            runs = run_lengths(r.modes, cell_lengths(r.path))
            rows.append({
                "prod_obj": evaluate(p_path, p_modes, W, T)[0], "raw_obj": evaluate(r_path, r_modes, W, T)[0],
                "eng_obj": r.cost,
                "prod_usd": evaluate(p_path, p_modes, D, T)[0], "eng_usd": evaluate(r.path, r.modes, D, T)[0],
                "raw_usd": evaluate(r_path, r_modes, D, T)[0],
                "prod_tr": int((np.diff(p_modes) != 0).sum()), "eng_tr": int((np.diff(r.modes) != 0).sum()),
                "raw_tr": int((np.diff(r_modes) != 0).sum()),
                "prod_hz": high_hazard_oh_km(bal, p_path, p_modes), "eng_hz": high_hazard_oh_km(bal, r.path, r.modes),
                "raw_hz": high_hazard_oh_km(bal, r_path, r_modes),
                "eng_min_run_m": float(min(runs)) if len(runs) > 1 else None, "expanded": r.expanded,
            })
        del eng
        a = {k: np.array([x[k] for x in rows], dtype=float) for k in rows[0] if k != "eng_min_run_m"}
        gap = a["prod_obj"] / a["eng_obj"] - 1
        gap_raw = a["raw_obj"] / a["eng_obj"] - 1
        mins = [x["eng_min_run_m"] for x in rows if x["eng_min_run_m"] is not None]
        # production measures runs with half-cell lengths, the engine conservatively (diagonals as 40 m), so a
        # production route with a run just over 450 m can be feasible for it and not for the engine
        res = {
            "routes_production_cheaper": int((gap < -1e-4).sum()),
            "objective_gap_mean": float(gap.mean()), "objective_gap_median": float(np.median(gap)),
            "objective_gap_p90": float(np.quantile(gap, .9)), "objective_gap_max": float(gap.max()),
            "objective_gap_mean_unstraightened": float(gap_raw.mean()),
            "share_ge_1pct": float((gap >= .01).mean()), "share_ge_5pct": float((gap >= .05).mean()),
            "usd_production": float(a["prod_usd"].sum()), "usd_engine": float(a["eng_usd"].sum()),
            "usd_change": float(a["eng_usd"].sum() / a["prod_usd"].sum() - 1),
            "usd_change_median_route": float(np.median(a["eng_usd"] / a["prod_usd"] - 1)),
            # like-for-like: production before string-pulling is a grid path, as the engine's is
            "usd_unstraightened_production": float(a["raw_usd"].sum()),
            "usd_change_vs_unstraightened": float(a["eng_usd"].sum() / a["raw_usd"].sum() - 1),
            "usd_change_vs_unstraightened_median_route": float(np.median(a["eng_usd"] / a["raw_usd"] - 1)),
            "transitions_unstraightened_production": float(a["raw_tr"].mean()),
            "oh_high_hazard_km_unstraightened_production": float(a["raw_hz"].sum()),
            "transitions_production": float(a["prod_tr"].mean()), "transitions_engine": float(a["eng_tr"].mean()),
            "oh_high_hazard_km_production": float(a["prod_hz"].sum()), "oh_high_hazard_km_engine": float(a["eng_hz"].sum()),
            "engine_shortest_run_m": min(mins) if mins else None,
            "engine_ms_median": float(np.median(t_eng) * 1000), "engine_ms_p95": float(np.quantile(t_eng, .95) * 1000),
            "production_ms_median": float(np.median(t_prod) * 1000),
            "engine_expanded_median": float(np.median(a["expanded"])),
        }
        out["presets"][preset] = res
        print(f"\n{preset} ({len(rows)} routes)")
        print(f"  search objective: production +{res['objective_gap_mean']:.1%} mean / +{res['objective_gap_median']:.1%} median "
              f"/ +{res['objective_gap_p90']:.1%} p90 above the exact optimum (unstraightened: +{res['objective_gap_mean_unstraightened']:.1%})")
        print(f"  routes >= 1% / 5% cheaper: {res['share_ge_1pct']:.0%} / {res['share_ge_5pct']:.0%}; "
              f"production cheaper on {res['routes_production_cheaper']} (run-length measure differs, see above)")
        print(f"  balanced dollars: ${res['usd_production'] / 1e6:,.0f}M -> ${res['usd_engine'] / 1e6:,.0f}M "
              f"({res['usd_change']:+.1%}; median route {res['usd_change_median_route']:+.1%})")
        print(f"  like-for-like (production before string-pulling): ${res['usd_unstraightened_production'] / 1e6:,.0f}M -> "
              f"${res['usd_engine'] / 1e6:,.0f}M ({res['usd_change_vs_unstraightened']:+.1%}; median route "
              f"{res['usd_change_vs_unstraightened_median_route']:+.1%}); transitions {res['transitions_unstraightened_production']:.2f} "
              f"-> {res['transitions_engine']:.2f}; OH high-hazard km {res['oh_high_hazard_km_unstraightened_production']:.0f} "
              f"-> {res['oh_high_hazard_km_engine']:.0f}")
        print(f"  transitions/route {res['transitions_production']:.2f} -> {res['transitions_engine']:.2f}; "
              f"OH km in high hazard {res['oh_high_hazard_km_production']:.0f} -> {res['oh_high_hazard_km_engine']:.0f}; "
              f"shortest engine run {res['engine_shortest_run_m']}")
        print(f"  time/route: engine {res['engine_ms_median']:.0f} ms median ({res['engine_ms_p95']:.0f} p95, "
              f"{res['engine_expanded_median']:,.0f} states), production {res['production_ms_median']:.0f} ms")
    (HERE / "compare_results.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
