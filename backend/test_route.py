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
    ("Inside Mesa Grande Res. -> Creelman Sub", (-116.7531, 33.0880), CREELMAN_SUB),
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
    print(f"  fire     route mean risk {p['mean_fire_risk']:.2f}, high-hazard {p['high_risk_km']:.2f} km "
          f"(overhead {p['overhead_high_risk_km']:.2f} km) | baseline mean risk {b['mean_fire_risk']:.2f}, "
          f"high-hazard {b['high_risk_km']:.2f} km (all overhead)")
    inv, row = p["inventory"], p["row"]
    print(f"  inventory towers {inv['tower_count']}, transitions {inv['transition_count']}, vaults {inv['splice_vault_count_est']}, "
          f"conductor {inv['conductor_km_est']} km, cable {inv['cable_km_est']} km")
    print(f"  ROW      {row['segment_count']} segments, {row['private_parcels']} private parcels ({row['private_parcels_method']}), "
          f"{row['public_agreements']} public agreements -> {row['landholder_negotiations_est']} negotiations; "
          f"land {p['land']['km_by_owner']}")
    print(f"  triggers {[t['code'] for t in p['permitting_triggers']]}")
    print(f"  tribal   route {p['tribal_km']:.2f} km {[t['name'] for t in p['tribal_lands_crossed']]}, "
          f"baseline {b['tribal_km']:.2f} km {[t['name'] for t in b['tribal_lands_crossed']]}")
    for f in fc["features"]:
        q = f["properties"]
        if q["kind"] == "segment":
            print(f"    seg {q['index']:2d} {q['mode']:11s} {q['length_km']:6.2f} km  {fmt_usd(q['est_cost_usd']):>9s}  "
                  f"({fmt_usd(q['cost_per_mile_usd'])}/mi)  {len(f['geometry']['coordinates'])} vertices")


def check_schema(fc):
    """Planning/inventory sections exist and agree with each other."""
    p = fc["properties"]
    inv, land, row = p["inventory"], p["land"], p["row"]
    total = p["total_km"]
    assert p["schema_version"] == "1.1"
    assert abs(inv["overhead_km"] + inv["underground_km"] - total) < 0.01, "OH + UG != total"
    assert abs(sum(s["km"] for s in row["segments"]) - total) < 0.02 * total + 0.01, "ROW segments don't sum to total"
    assert abs(sum(land["km_by_owner"].values()) - total) < 0.02 * total + 0.01, "land km don't sum to total"
    assert inv["tower_count"] == p["tower_count"] and inv["transition_count"] == p["n_transitions"]
    assert row["landholder_negotiations_est"] == row["private_parcels"] + row["public_agreements"]
    codes = {t["code"] for t in p["permitting_triggers"]}
    assert "CEQA" in codes
    assert ("TRIBAL_CONSENT" in codes) == (land["tribal_km"] > 0)
    assert ("FEDERAL_ROW_NEPA" in codes) == (land["federal_km"] > 0)
    seqs = [s["seq"] for s in row["segments"]]
    assert seqs == list(range(len(seqs)))


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
        check_schema(fc)
        # the router should never string more overhead line through high hazard than the straight line does
        p = fc["properties"]
        assert p["overhead_high_risk_km"] <= p["baseline"]["high_risk_km"] + 0.1, "more OH high-hazard km than baseline"

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

    # strategy presets: balanced == default; presets search different paths, priced on the balanced grid
    S, CREELMAN = CASES[0][1], CREELMAN_SUB
    r = router
    base = r.route(S, CREELMAN)
    base["properties"].pop("compute_ms")
    again = r.route(S, CREELMAN, preset="balanced")
    again["properties"].pop("compute_ms")
    assert base == again
    assert base["properties"]["preset"] == "balanced"
    alts = {p: r.route(S, CREELMAN, preset=p) for p in C.PRESETS}
    geoms = {p: json.dumps([f["geometry"] for f in fc["features"] if f["properties"].get("kind") == "segment"]) for p, fc in alts.items()}
    print()
    for p, fc in alts.items():
        q = fc["properties"]
        print(f"preset {p:12s} {q['total_km']:6.2f} km  {fmt_usd(q['total_cost_usd'])}  OH {q['overhead_km']:.2f} UG {q['underground_km']:.2f}  "
              f"OH high-hazard {q['overhead_high_risk_km']:.2f} km")
        check_schema(fc)
    assert len(set(geoms.values())) >= 3, "presets must give visibly different routes"
    # pricing is always on the balanced grid: re-pricing the balanced route gives its own cost
    assert abs(alts["balanced"]["properties"]["total_cost_usd"] - base["properties"]["total_cost_usd"]) <= 1
    try: r.route(S, CREELMAN, preset="nope"); raise AssertionError("unknown preset accepted")
    except RouteError: pass

    if args.save_demo:
        demo = results[0]
        demo["properties"]["demo"] = True
        demo["properties"]["demo_label"] = CASES[0][0].replace("DEMO  ", "")
        demo["properties"]["demo_title"] = "Santa Ysabel Valley solar → Creelman Substation"
        demo["properties"]["demo_blurb"] = (
            "A hypothetical solar project site in the Santa Ysabel Valley (San Diego County backcountry), "
            "connected by a new gen-tie line to SDG&E's existing Creelman 69 kV substation in Ramona. "
            "It is not a real proposed project — it's a worked example of how the router trades off "
            "construction cost, wildfire hazard and protected or tribal land.")
        C.DEMO_ROUTE_PATH.write_text(json.dumps(demo))
        print(f"saved demo route -> {C.DEMO_ROUTE_PATH}")
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
