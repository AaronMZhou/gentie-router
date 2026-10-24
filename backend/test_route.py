"""
Stage-2 smoke test: route a few project -> substation pairs without the API.

    python -m backend.test_route              # print routes
    python -m backend.test_route --save-demo  # also write frontend/demo_route.json
"""
import argparse
import json
import time

import numpy as np

import config as C
from backend.cost_model import build_cost_grids
from backend.router import Router, RouteError

CREELMAN_SUB = (-116.85305, 33.01807)  # SDG&E Creelman 69 kV (OSM)
JULIAN_SUB = (-116.58624, 33.00602)    # unnamed substation SE of Julian (OSM)

CASES = [
    ("DEMO  Santa Ysabel valley solar -> Creelman Sub", (-116.69, 33.10), CREELMAN_SUB),
    ("Ballena Valley solar -> Creelman Sub", (-116.75, 33.04), CREELMAN_SUB),
    ("Pine Hills site -> Julian-area Sub", (-116.63, 33.03), JULIAN_SUB),
    ("North of Mesa Grande Res. -> Creelman Sub", (-116.76, 33.16), CREELMAN_SUB),
]


def fmt_usd(x):
    return f"${x / 1e6:,.1f}M"


def show(name, fc):
    p = fc["properties"]
    b = p["baseline"]
    print(f"\n=== {name}")
    print(f"  route    {p['total_km']:.2f} km  {fmt_usd(p['total_cost_usd'])}  "
          f"(OH {p['overhead_km']:.2f} km, UG {p['underground_km']:.2f} km, {p['n_transitions']} transitions, "
          f"{p['tower_count']} towers)  [{p['compute_ms']} ms]")
    print(f"           high-fire {p['high_fire_km']:.2f} km (overhead in high fire {p['overhead_high_fire_km']:.2f} km), "
          f"protected cells {p['protected_cells_crossed']} ({p['protected_km']:.2f} km)")
    print(f"  baseline {b['length_km']:.2f} km  {fmt_usd(b['est_cost_usd'])} all-overhead "
          f"(best-mode {fmt_usd(b['best_mode_cost_usd'])}), high-fire {b['high_fire_km']:.2f} km, "
          f"protected {b['protected_km']:.2f} km, max slope {b['max_slope_deg']} deg")
    print(f"  savings vs baseline: {fmt_usd(p['savings_vs_baseline_usd'])}")
    print(f"  tribal   route {p['tribal_km']:.2f} km {[t['name'] for t in p['tribal_lands_crossed']]}, "
          f"baseline {b['tribal_km']:.2f} km {[t['name'] for t in b['tribal_lands_crossed']]}")
    for f in fc["features"]:
        q = f["properties"]
        if q["kind"] == "segment":
            print(f"    seg {q['index']:2d} {q['mode']:11s} {q['length_km']:6.2f} km  {fmt_usd(q['est_cost_usd']):>9s}  "
                  f"({fmt_usd(q['cost_per_mile_usd'])}/mi)  {len(f['geometry']['coordinates'])} vertices")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save-demo", action="store_true")
    args = ap.parse_args()

    t = time.perf_counter()
    grids = build_cost_grids()
    print(f"cost grids built in {time.perf_counter() - t:.2f}s, shape {grids.routing.shape}, "
          f"synthetic layers: {grids.meta['synthetic_layers'] or 'none'}")
    inside = grids.region == 1
    print(f"cells where underground wins: {grids.mode[inside].mean():.1%}")
    router = Router(grids)

    results = []
    for name, s, e in CASES:
        fc = router.route(s, e)
        show(name, fc)
        results.append(fc)
        # geometry sanity: segments chain end-to-start and modes alternate
        segs = [f for f in fc["features"] if f["properties"]["kind"] == "segment"]
        for a, b in zip(segs, segs[1:]):
            assert np.allclose(a["geometry"]["coordinates"][-1], b["geometry"]["coordinates"][0]), "segments not connected"
            assert a["properties"]["mode"] != b["properties"]["mode"], "adjacent segments share a mode"
        assert np.allclose(segs[0]["geometry"]["coordinates"][0], s, atol=1e-5)
        assert np.allclose(segs[-1]["geometry"]["coordinates"][-1], e, atol=1e-5)

    # tribal penalty must reduce (or keep at zero) tribal km vs. a run without it
    penalty = C.TRIBAL_PENALTY
    C.TRIBAL_PENALTY = 1.0
    no_pen = Router(build_cost_grids())
    C.TRIBAL_PENALTY = penalty
    print()
    for (name, s, e), fc in zip(CASES, results):
        without = no_pen.route(s, e)["properties"]["tribal_km"]
        with_ = fc["properties"]["tribal_km"]
        print(f"tribal km {name[:42]:42s} without penalty {without:5.2f} -> with x{penalty:g} {with_:5.2f}")
        assert with_ <= without + 0.05, "tribal penalty increased tribal crossing"

    try:
        router.route((-117.5, 33.0), CREELMAN_SUB)
        raise AssertionError("out-of-region start should fail")
    except RouteError as ex:
        print(f"\nout-of-region check OK: {ex}")

    if args.save_demo:
        demo = results[0]
        demo["properties"]["demo"] = True
        demo["properties"]["demo_label"] = CASES[0][0].replace("DEMO  ", "")
        C.DEMO_ROUTE_PATH.write_text(json.dumps(demo))
        print(f"saved demo route -> {C.DEMO_ROUTE_PATH}")
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
