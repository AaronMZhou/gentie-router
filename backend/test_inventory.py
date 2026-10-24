"""
Offline checks for the inventory simulation (no server, no routing needed).

    python -m backend.test_inventory            # prints a report + runs sanity checks
"""
import copy
import json
import time

import config as C
from backend.inventory import assess, bids, bill_of_materials


def load_demo():
    return json.loads(C.DEMO_ROUTE_PATH.read_text())


def underground_heavy():
    """The demo route re-cut as 12 km overhead + 8 km underground, to exercise the cable lines."""
    fc = copy.deepcopy(load_demo())
    p = fc["properties"]
    p["inventory"].update(overhead_km=12.0, underground_km=8.0, total_km=20.0, tower_count=40, transition_count=1,
                          splice_vault_count_est=14, conductor_km_est=37.08, cable_km_est=24.0, underground_in_road_km=3.0)
    fc["features"] = [{"type": "Feature", "properties": {"kind": "segment", "mode": "overhead", "length_km": 12.0}, "geometry": None},
                      {"type": "Feature", "properties": {"kind": "segment", "mode": "underground", "length_km": 8.0}, "geometry": None}]
    return fc


def show(title, r):
    s = r["summary"]
    print(f"\n=== {title} ===")
    print(f"verdict {s['verdict']}  P(on time)={s['p_materials_on_time']:.0%}  earliest start wk {s['earliest_construction_start_weeks']}  "
          f"material ${s['est_material_cost_usd']/1e6:.1f}M  long-lead lines {s['long_lead_lines']}  behind {s['lines_behind_schedule']}")
    print("drivers:", [(d["name"], d["share"]) for d in s["critical_path_drivers"]])
    for l in r["order_plan"][:8]:
        print(f"  {'LL ' if l['long_lead'] else '   '}{l['name'][:46]:46s} {l['status']:10s} {l['action']}")


def main():
    t = time.perf_counter()
    base = assess(load_demo(), need_by_weeks=104)
    print(f"assess() took {(time.perf_counter() - t) * 1000:.0f} ms")
    show("demo route, 100 MW, need by wk 104", base)

    # 1. quantities follow the route inventory
    q = {l["id"]: l["qty"] for l in base["lines"]}
    inv = load_demo()["properties"]["inventory"]
    assert q["foundations"] == inv["tower_count"]
    assert q["pole_tangent"] + q["pole_deadend"] == inv["tower_count"]
    assert "hv_cable" not in q, "all-overhead route must not need HV cable"

    # 2. an underground-heavy route pulls in the cable chain, and those are long-lead
    ug = assess(underground_heavy(), need_by_weeks=104)
    show("underground-heavy route", ug)
    ids = {l["id"]: l for l in ug["lines"]}
    assert ids["hv_cable"]["long_lead"] and ids["hv_terminations"]["qty"] == 6   # 3 phases x 2 ends x 1 UG run
    assert ug["order_plan"][0]["long_lead"], "long-lead commodities must be ordered first"

    # 3. determinism: same request, same answer
    assert assess(load_demo(), need_by_weeks=104)["summary"] == base["summary"]

    # 4. harder market => never better
    tight = assess(load_demo(), need_by_weeks=104, market="tight")
    crisis = assess(load_demo(), need_by_weeks=104, market="crisis")
    p = [r["summary"]["p_materials_on_time"] for r in (base, tight, crisis)]
    e = [r["summary"]["earliest_construction_start_weeks"]["p50"] for r in (base, tight, crisis)]
    print(f"\nmarket sweep  P(on time) {p}  earliest start p50 {e}")
    assert p[0] >= p[1] >= p[2] and e[0] <= e[1] <= e[2]

    # 5. a later need-by date never hurts
    late = assess(load_demo(), need_by_weeks=200)
    assert late["summary"]["p_materials_on_time"] >= base["summary"]["p_materials_on_time"]

    # 6. bigger project => bigger GSU => longer lead time
    small = {l["id"]: l for l in assess(load_demo(), capacity_mw=40)["lines"]}["gsu"]
    large = {l["id"]: l for l in assess(load_demo(), capacity_mw=250)["lines"]}["gsu"]
    assert large["lead_weeks"][1] > small["lead_weeks"][1]

    # 7. no qualified supplier (spec filter): 230 kV cable has no source in the catalog
    none = assess(underground_heavy(), voltage_kv=230)
    assert none["summary"]["verdict"] == "NOT VIABLE" and none["summary"]["unqualified"]
    print("230 kV underground:", none["summary"]["verdict"], none["summary"]["unqualified"])

    # 8. skipping terminal equipment drops the substation-side lines
    nt = {l["id"] for l in assess(load_demo(), include_terminals=False)["lines"]}
    assert not nt & {"breaker", "gsu", "relay", "ctvt", "disconnect"}

    # 9. mock bids: every catalog supplier of the item bids, deterministic, best-value first, totals add up
    cab = ids["hv_cable"]
    b = bids("hv_cable", cab["qty"], need_by_week=cab["need_by_week"])
    assert {"AHC", "EUR"} <= {x["supplier_id"] for x in b["bids"]}
    assert b == bids("hv_cable", cab["qty"], need_by_week=cab["need_by_week"])
    assert b["bids"][0]["supplier_id"] == b["best"]["value"]
    assert all(x["total_usd"] - x["material_usd"] - x["freight_usd"] in (-1, 0, 1) for x in b["bids"])
    print("hv_cable bids:", [(x["name"], x["score"]) for x in b["bids"]])
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
