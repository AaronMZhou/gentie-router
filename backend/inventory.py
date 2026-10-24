"""
Inventory & long-lead procurement assessment for a computed route.

    assess(route_fc, voltage_kv=69, capacity_mw=100, need_by_weeks=104, market="current")

Pipeline
  1. DEMAND   route.properties.inventory (+ segment list) -> bill of materials (BOM)
  2. SUPPLY   data/inventory/catalog.json: suppliers, stock on hand, ship rate, lead-time bands
  3. FILL     for each BOM line, draw from stock first, then from production (fluid model:
              every supplier ships `rate` units/week once its lead time has elapsed)
  4. SIMULATE Monte Carlo over lead times, supplier delays, competitor stock draw-down
  5. REPORT   per-line ETA / on-time probability, critical path, and an order plan that puts
              special long-lead commodities first

Everything numeric in the catalog is a simulated or placeholder assumption; see its _readme.
The simulation is seeded from the request, so identical requests give identical answers.
"""
import json
import math
import random
import zlib

import numpy as np

import config as C

ASSUMPTIONS = {
    "tangent_share": 0.85,        # share of structures that are tangent; rest are angle / dead-end
    "extra_deadends": 2,          # terminal dead-end structures at the two ends
    "conductor_spare": 1.05,      # conductor cut/waste allowance
    "cable_spare": 1.03,          # cable cut-length allowance
    "terminals_per_route": {"breaker": 2, "disconnect": 4, "ctvt": 2, "relay": 2, "gsu": 1},
    "construction_weeks": "12 + 3 x overhead km + 6 x underground km",
}


# --------------------------------------------------------------------------- helpers
def load_catalog():
    return json.loads(C.INVENTORY_CATALOG.read_text())


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _pct(a, q):
    return float(np.percentile(a, q))


def _r(x, n=1):
    return None if x is None or not math.isfinite(x) else round(float(x), n)


def gsu_mva_for(capacity_mw):
    return int(math.ceil(capacity_mw / 0.9 / 5.0) * 5)


def item_spec(item, gsu_mva):
    """-> (display name, lead-time band, unit cost); tiered items (e.g. GSU lead time depends on MVA) pick their tier."""
    name, band, unit_cost = item["name"], item.get("lead_weeks"), item.get("unit_cost_usd")
    if "tiers" in item:
        tier = next(t for t in item["tiers"] if gsu_mva <= t["max_mva"])
        band, unit_cost = tier["lead_weeks"], tier["unit_cost_usd"]
        name = f"{name} ({gsu_mva} MVA)"
    return name, band, unit_cost


# --------------------------------------------------------------------------- 1. demand
def construction_weeks(inv):
    return max(12.0, 12 + 3 * inv["overhead_km"] + 6 * inv["underground_km"])


def bill_of_materials(inv, n_ug_segments, capacity_mw, include_terminals):
    """Route inventory -> {item_id: quantity}.  `inv` is route.properties.inventory."""
    A = ASSUMPTIONS
    towers = int(inv["tower_count"])
    dead = min(towers, math.ceil((1 - A["tangent_share"]) * towers) + A["extra_deadends"]) if towers else 0
    tang = towers - dead
    oh, ug = inv["overhead_km"], inv["underground_km"]
    if n_ug_segments is None:  # no segment list given: fall back to the transition count
        n_ug_segments = inv["transition_count"] if inv["transition_count"] else (1 if ug > 0 else 0)
    bom = {
        "pole_tangent": tang,
        "pole_deadend": dead,
        "foundations": towers,
        "conductor": inv["conductor_km_est"] * A["conductor_spare"],
        "opgw": oh * 1.03,
        "insulators": tang * 3 + dead * 6,
        "hardware": towers * 3,
        "hv_cable": inv["cable_km_est"] * A["cable_spare"],
        "hv_joints": inv["splice_vault_count_est"] * 3,
        "hv_terminations": n_ug_segments * 6,   # 3 phases at each end of every underground run
        "ductbank": ug,
    }
    if include_terminals:
        bom.update(A["terminals_per_route"])
    return {k: v for k, v in bom.items() if v > 1e-9}


# --------------------------------------------------------------------------- 3. fill model
def fill(qty, offers):
    """Fill `qty` from sampled/expected offers.
    offers: [{id, stock, stock_eta, lead, rate, price}]  (weeks; lead already includes haul/delay)
    -> (eta_weeks | None, {supplier_id: {stock, made, eta}})
    """
    if not offers:
        return None, {}
    remaining, eta, alloc = qty, 0.0, {}
    for o in sorted(offers, key=lambda o: o["price"]):          # cheapest stock first
        take = min(o["stock"], remaining)
        if take > 1e-9:
            alloc[o["id"]] = {"stock": take, "made": 0.0, "eta": o["stock_eta"]}
            eta, remaining = max(eta, o["stock_eta"]), remaining - take
    if remaining > 1e-9:
        prod = sorted(offers, key=lambda o: o["lead"])
        active, left, t_done = 0.0, remaining, prod[-1]["lead"]
        for i, o in enumerate(prod):                            # suppliers switch on as their lead time elapses
            active += o["rate"]
            nxt = prod[i + 1]["lead"] if i + 1 < len(prod) else math.inf
            cap = active * (nxt - o["lead"])
            if left <= cap + 1e-12:
                t_done = o["lead"] + left / active
                break
            left -= cap
        for o in prod:
            made = o["rate"] * max(0.0, t_done - o["lead"])
            if made > 1e-9:
                a = alloc.setdefault(o["id"], {"stock": 0.0, "made": 0.0, "eta": 0.0})
                a["made"], a["eta"] = made, max(a["eta"], t_done)
        eta = max(eta, t_done)
    return eta, alloc


# --------------------------------------------------------------------------- main
def assess(route_fc, voltage_kv=69, capacity_mw=100.0, need_by_weeks=104.0, market="current",
           include_terminals=True, trials=None):
    cat = load_catalog()
    if market not in cat["market_scenarios"]:
        raise ValueError(f"unknown market scenario '{market}'")
    mk = cat["market_scenarios"][market]
    sup = cat["suppliers"]
    props = route_fc["properties"]
    inv = props["inventory"]
    segs = [f["properties"] for f in route_fc.get("features", []) if f["properties"].get("kind") == "segment"]
    n_ug = sum(1 for s in segs if s["mode"] == "underground") if segs else None
    cw = construction_weeks(inv)
    bom = bill_of_materials(inv, n_ug, capacity_mw, include_terminals)
    gsu_mva = gsu_mva_for(capacity_mw)
    site_lat, site_lon = props["start"][1], props["start"][0]   # materials are hauled to the project end
    trials = trials or C.INVENTORY_TRIALS
    seed = zlib.crc32(json.dumps([props["start"], props["end"], voltage_kv, capacity_mw, need_by_weeks, market,
                                  include_terminals], sort_keys=True).encode())
    rng = random.Random(seed)

    lines = []
    for item in cat["items"]:
        if item["id"] not in bom:
            continue
        qty = bom[item["id"]]
        name, band, unit_cost = item_spec(item, gsu_mva)
        heavy = 3.0 if item.get("heavy_haul") else 1.0
        qualified = []                                            # spec filter: supplier must serve this voltage class
        for off in item["offers"]:
            if "voltage_kv" in off and voltage_kv not in off["voltage_kv"]:
                continue
            s = sup[off["supplier"]]
            haul = 0.3 + heavy * haversine_km(s["lat"], s["lon"], site_lat, site_lon) / 1500.0
            qualified.append((off, s, haul))
        lines.append({"item": item, "name": name, "qty": qty, "band": band, "unit_cost": unit_cost,
                      "qualified": qualified, "need_by": need_by_weeks + item["needed_frac"] * cw,
                      "frac": item["needed_frac"]})

    # ---- Monte Carlo: one ETA array per line, sampled trial-by-trial so line risks combine correctly
    for L in lines:
        etas = []
        for _ in range(trials):
            offers = []
            for off, s, haul in L["qualified"]:
                f = off["lead_factor"] * mk["lead_mult"]
                lead = rng.triangular(L["band"][0] * f, L["band"][2] * f, L["band"][1] * f)  # (low, high, mode)
                if rng.random() > 1 - (1 - s["reliability"]) * mk["delay_mult"]:
                    lead *= 1 + rng.uniform(0.10, 0.35)          # supplier slips
                offers.append({"id": off["supplier"], "price": s["price_factor"],
                               "stock": off["stock"] * mk["stock_mult"] * rng.uniform(0.4, 1.0),  # competitors draw it down
                               "stock_eta": 1.0 + haul, "lead": lead + haul, "rate": off["rate"]})
            etas.append(fill(L["qty"], offers)[0])
        L["eta"] = None if any(e is None for e in etas) else np.array(etas)

        # expected (non-random) plan, used for the allocation shown to the user
        exp = []
        for off, s, haul in L["qualified"]:
            rel = 1 - (1 - s["reliability"]) * mk["delay_mult"]
            lead = L["band"][1] * off["lead_factor"] * mk["lead_mult"] * (1 + (1 - rel) * 0.225)
            exp.append({"id": off["supplier"], "price": s["price_factor"], "stock": off["stock"] * mk["stock_mult"] * 0.7,
                        "stock_eta": 1.0 + haul, "lead": lead + haul, "rate": off["rate"]})
        L["plan_eta"], L["plan"] = fill(L["qty"], exp)

    # ---- combine
    blocked = [L for L in lines if L["eta"] is None]
    if lines and not blocked:
        eta_mat = np.vstack([L["eta"] for L in lines])                       # lines x trials
        frac = np.array([L["frac"] for L in lines])[:, None]
        # construction can start once every item will have arrived by the time it is needed
        start_req = (eta_mat - frac * cw)
        crit = start_req.max(axis=0)
        argmax = start_req.argmax(axis=0)
        p_all = float((crit <= need_by_weeks).mean())
        earliest = {"p50": _r(_pct(crit, 50)), "p80": _r(_pct(crit, 80)), "p90": _r(_pct(crit, 90))}
        crit_share = np.bincount(argmax, minlength=len(lines)) / trials
    else:
        p_all, earliest, crit_share = 0.0, {"p50": None, "p80": None, "p90": None}, np.zeros(len(lines))

    out_lines, total_cost = [], 0.0
    for i, L in enumerate(lines):
        item, e = L["item"], L["eta"]
        stock_only = L["plan_eta"] is not None and all(a["made"] < 1e-9 for a in L["plan"].values())
        in_stock = sum(a["stock"] for a in L["plan"].values())
        long_lead = L["band"][1] >= C.LONG_LEAD_WEEKS
        cost = L["qty"] * (L["unit_cost"] or 0) * mk["price_mult"]
        total_cost += cost
        if e is None:
            status, p_on, eta_d, order_by = "NO QUALIFIED SUPPLIER", 0.0, {"p50": None, "p90": None}, None
        else:
            p_on = float((e <= L["need_by"]).mean())
            eta_d = {"p50": _r(_pct(e, 50)), "p90": _r(_pct(e, 90))}
            order_by = _r(L["need_by"] - eta_d["p90"])
            status = "IN STOCK" if stock_only else "ON TRACK" if p_on >= 0.9 else "AT RISK" if p_on >= 0.5 else "LATE"
        out_lines.append({
            "id": item["id"], "name": L["name"], "category": item["category"], "unit": item["unit"],
            "qty": _r(L["qty"], 2), "long_lead": bool(long_lead), "lead_weeks": L["band"],
            "source_quality": item["source"], "note": item.get("note"),
            "in_stock_qty": _r(in_stock, 2), "eta_weeks": eta_d, "need_by_week": _r(L["need_by"]),
            "p_on_time": _r(p_on, 3), "status": status, "order_by_week": order_by,
            "critical_path_share": _r(float(crit_share[i]), 3),
            "allocation": [{"supplier": k, "name": sup[k]["name"], "from_stock": _r(v["stock"], 2),
                            "to_produce": _r(v["made"], 2), "eta_weeks": _r(v["eta"])} for k, v in L["plan"].items()],
            "unit_cost_usd": L["unit_cost"], "est_cost_usd": round(cost),
            "suppliers": [q[1]["name"] for q in L["qualified"]],
        })

    # ---- order plan: special long-lead commodities first, then whoever has the least float
    def prio(l):
        return (not l["long_lead"], l["order_by_week"] if l["order_by_week"] is not None else -1e9)
    plan = []
    for l in sorted(out_lines, key=prio):
        if l["status"] == "NO QUALIFIED SUPPLIER":
            action = "No qualified supplier: change spec or find a new source"
        elif l["status"] == "IN STOCK":
            action = "Reserve stock now"
        elif l["order_by_week"] <= 0:
            action = f"ORDER NOW: already {abs(l['order_by_week']):.0f} wks behind"
        else:
            action = f"Order by week {l['order_by_week']:.0f}"
        plan.append({"id": l["id"], "name": l["name"], "long_lead": l["long_lead"], "action": action,
                     "order_by_week": l["order_by_week"], "status": l["status"]})

    verdict = ("NOT VIABLE" if blocked or p_all < 0.4 else "AT RISK" if p_all < 0.8 else "VIABLE")
    drivers = [{"id": l["id"], "name": l["name"], "share": l["critical_path_share"]}
               for l in sorted(out_lines, key=lambda l: -l["critical_path_share"])[:3] if l["critical_path_share"] > 0]
    used = {a["supplier"] for l in out_lines for a in l["allocation"]}
    return {
        "params": {"voltage_kv": voltage_kv, "capacity_mw": capacity_mw, "gsu_mva": gsu_mva, "need_by_weeks": need_by_weeks,
                   "market": market, "market_label": mk["label"], "include_terminals": include_terminals,
                   "construction_weeks_est": _r(cw), "trials": trials},
        "summary": {
            "verdict": verdict,
            "p_materials_on_time": _r(p_all, 3),
            "earliest_construction_start_weeks": earliest,
            "long_lead_lines": sum(1 for l in out_lines if l["long_lead"]),
            "lines_behind_schedule": sum(1 for l in out_lines if l["order_by_week"] is not None and l["order_by_week"] <= 0
                                         and l["status"] != "IN STOCK"),
            "critical_path_drivers": drivers,
            "unqualified": [l["name"] for l in out_lines if l["status"] == "NO QUALIFIED SUPPLIER"],
            "est_material_cost_usd": round(total_cost),
            "cost_note": "Placeholder unit prices; not reconciled with the router's construction-cost model.",
        },
        "lines": out_lines,
        "order_plan": plan,
        "suppliers": [{"id": k, "name": sup[k]["name"], "city": sup[k]["city"], "lat": sup[k]["lat"], "lon": sup[k]["lon"],
                       "reliability": sup[k]["reliability"]} for k in sup if k in used],
        "assumptions": ASSUMPTIONS,
    }


# --------------------------------------------------------------------------- supplier bids (mock)
BID_WEIGHTS = {"price": 0.40, "delivery": 0.35, "reliability": 0.25}   # best-value score, 0-100
PAYMENT_TERMS = ["Net 30", "Net 45", "Net 60", "30% deposit, balance on delivery", "Milestones 20 / 50 / 30"]
PRICE_BASIS = ["Firm fixed price", "Firm fixed price", "Escalation tied to metals index", "Firm for 90 days, then indexed"]
WARRANTY_YEARS = {"structures": (5, 10), "overhead": (2, 5), "underground": (2, 5), "terminals": (2, 5)}
BID_EXCEPTIONS = ["Partial shipments required", "Excludes on-site offloading", "Delivery to nearest rail siding only",
                  "Factory witness test billed separately", "Subject to final drawing approval", "Price excludes sales tax"]


def bids(item_id, qty, voltage_kv=69, capacity_mw=100.0, market="current", need_by_week=None, site=None):
    """Simulated bids from suppliers for one BOM line.

    Bidders are the catalog suppliers that offer the item plus the mock `bidders` whose categories (and `items`, if listed)
    include it.
    Seeded from the request, so the same item and quantity always get the same bids.
    site: [lon, lat] the material is delivered to (defaults to the middle of the study region).
    """
    cat = load_catalog()
    item = next((i for i in cat["items"] if i["id"] == item_id), None)
    if item is None:
        raise KeyError(item_id)
    if market not in cat["market_scenarios"]:
        raise ValueError(f"unknown market scenario '{market}'")
    mk = cat["market_scenarios"][market]
    name, band, unit_cost = item_spec(item, gsu_mva_for(capacity_mw))
    unit_cost = (unit_cost or 0) * mk["price_mult"]
    if site is None:
        w, s, e, n = C.BBOX
        site = [(w + e) / 2, (s + n) / 2]
    heavy = 3.0 if item.get("heavy_haul") else 1.0
    rng = random.Random(zlib.crc32(json.dumps([item_id, round(qty, 2), voltage_kv, capacity_mw, market]).encode()))
    # mock bidders inherit the item's voltage classes (if it has any spec)
    kv_classes = sorted({kv for o in item["offers"] for kv in o.get("voltage_kv", [])}) or None
    typical_rate = float(np.median([o["rate"] for o in item["offers"]]))

    bidders = [(o["supplier"], cat["suppliers"][o["supplier"]], o, "catalog") for o in item["offers"]]
    long_lead = band[1] >= C.LONG_LEAD_WEEKS                     # nobody keeps long-lead gear on the shelf
    bidders += [(k, b, {"stock": 0 if long_lead else qty * rng.choice([0, 0, 0.25, 0.5]), "rate": typical_rate * rng.uniform(0.5, 1.3),
                        "lead_factor": rng.uniform(0.9, 1.25), **({"voltage_kv": kv_classes} if kv_classes else {})}, "mock")
                for k, b in cat.get("bidders", {}).items()
                if item["category"] in b["categories"] and item_id in b.get("items", [item_id])]

    out = []
    for sid, s, off, origin in bidders:
        dist = haversine_km(s["lat"], s["lon"], site[1], site[0])
        haul = 0.3 + heavy * dist / 1500.0
        lead = band[1] * off["lead_factor"] * mk["lead_mult"] * rng.uniform(0.9, 1.1)
        stock = min(qty, off["stock"] * mk["stock_mult"] * 0.7)
        eta, _ = fill(qty, [{"id": sid, "price": 1, "stock": stock, "stock_eta": 1.0 + haul, "lead": lead + haul,
                             "rate": off["rate"]}])
        unit_price = unit_cost * s["price_factor"] * rng.uniform(0.93, 1.10)
        material = unit_price * qty
        freight = material * (0.01 + 0.03 * dist / 4000) * heavy
        meets_spec = "voltage_kv" not in off or voltage_kv in off["voltage_kv"]
        exceptions = rng.sample(BID_EXCEPTIONS, rng.choice([0, 1, 1, 2]))
        if not meets_spec:
            exceptions.insert(0, f"Not qualified for {voltage_kv} kV (offers {', '.join(map(str, off['voltage_kv']))} kV)")
        lo, hi = WARRANTY_YEARS.get(item["category"], (2, 5))
        out.append({
            "supplier_id": sid, "name": s["name"], "city": s["city"], "origin": origin,
            "unit_price_usd": round(unit_price), "material_usd": round(material), "freight_usd": round(freight),
            "total_usd": round(material + freight),
            "quoted_lead_weeks": _r(lead, 0), "from_stock_qty": _r(stock, 2), "delivery_week": _r(eta),
            "meets_need_by": None if need_by_week is None else bool(eta <= need_by_week),
            "on_time_pct": round(100 * (1 - (1 - s["reliability"]) * mk["delay_mult"])),
            "distance_km": round(dist), "warranty_years": rng.randint(lo, hi),
            "payment_terms": rng.choice(PAYMENT_TERMS), "price_basis": rng.choice(PRICE_BASIS),
            "valid_days": rng.choice([30, 45, 60, 90]), "domestic": "import" not in s["name"].lower(),
            "meets_spec": meets_spec, "exceptions": exceptions,
        })

    ok = [b for b in out if b["meets_spec"]]
    if ok:
        lo_price, lo_eta = min(b["total_usd"] for b in ok), min(b["delivery_week"] for b in ok)
        for b in ok:
            w = BID_WEIGHTS
            score = (w["price"] * lo_price / b["total_usd"] + w["delivery"] * lo_eta / b["delivery_week"]
                     + w["reliability"] * b["on_time_pct"] / 100)
            if b["meets_need_by"] is False:
                score *= 0.85                                     # late against the schedule
            b["score"] = round(100 * score)
    for b in out:
        b.setdefault("score", None)
    out.sort(key=lambda b: -(b["score"] if b["score"] is not None else -1))
    pick = lambda key: min(ok, key=lambda b: b[key])["supplier_id"] if ok else None
    return {
        "item": {"id": item_id, "name": name, "category": item["category"], "unit": item["unit"], "qty": _r(qty, 2),
                 "lead_weeks": band, "note": item.get("note"), "need_by_week": _r(need_by_week),
                 "est_unit_cost_usd": round(unit_cost)},
        "params": {"voltage_kv": voltage_kv, "capacity_mw": capacity_mw, "market": market, "market_label": mk["label"],
                   "site": site},
        "bids": out,
        "best": {"value": out[0]["supplier_id"] if ok else None, "price": pick("total_usd"), "delivery": pick("delivery_week")},
        "weights": BID_WEIGHTS,
        "note": "Mock bids generated for the demo: supplier names, prices and terms are fictional.",
    }
