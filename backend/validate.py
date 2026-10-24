"""
Validation: can the router reproduce real power lines?

For each real OSM line we route between its endpoints with that line REMOVED
from the corridor layer (leave-one-out - otherwise the 0.6x "follow existing
lines" discount would just trace it), then measure how far our route strays.

    python -m backend.validate      # prints metrics, writes frontend/validation.json

Metrics (route sampled every 10 m against the real line, and vice versa):
  mean_dist_m            mean distance from our route to the real line
  mean_dist_real_m       mean distance from the real line to our route
  p90_dist_m / max_dist_m
  pct_within_{100,250,500}m   share of our route within that distance of the real line
"""
import json
from datetime import datetime, timezone

import geopandas as gpd
import numpy as np
from rasterio.features import rasterize
from shapely.geometry import LineString, Point, box, mapping
from shapely.ops import linemerge, transform, unary_union

import config as C
from backend.cost_model import build_cost_grids
from backend.router import Router

VALIDATION_PATH = C.FRONTEND_DIR / "validation.json"
CASES = [
    ("637", "SDG&E 69 kV line 637: Creelman Sub east toward Ballena"),
    ("635", "SDG&E 69 kV line 635: Creelman Sub south toward Barona (in-region part)"),
]
SAMPLE_M = 10.0


def load_power_lines():
    gdf = gpd.read_file(C.OVERLAY_DIR / "powerlines.geojson")
    gdf["tokens"] = gdf["name"].fillna("").str.split(";")
    return gdf


def real_line(gdf, line_id):
    """Merge the OSM ways for one line id, clip to (slightly inside) the bbox, keep the longest piece."""
    geoms = gdf[gdf["tokens"].apply(lambda t: line_id in t)].geometry
    if geoms.empty:
        raise ValueError(f"line {line_id} not in OSM data")
    merged = unary_union(list(geoms))
    if not isinstance(merged, LineString):
        merged = linemerge(merged)
    w, s, e, n = C.BBOX
    clipped = merged.intersection(box(w + 0.003, s + 0.003, e - 0.003, n - 0.003))
    parts = [g for g in getattr(clipped, "geoms", [clipped]) if isinstance(g, LineString)]
    line = max(parts, key=lambda g: g.length)
    # orient so the line ENDS at the substation end (start = "project", end = "substation" in the UI)
    subs = gpd.read_file(C.OVERLAY_DIR / "substations.geojson").geometry
    if len(subs):
        d0 = subs.distance(Point(line.coords[0])).min()
        d1 = subs.distance(Point(line.coords[-1])).min()
        if d0 < d1:
            line = LineString(line.coords[::-1])
    return line


def corridor_without(gdf, line_id, grids):
    """Corridor raster with roads as-is and every power line except `line_id`."""
    corridor = (grids.corridor & 1).astype(np.uint8)
    others = gdf[~gdf["tokens"].apply(lambda t: line_id in t)].to_crs(C.WORK_CRS)
    if len(others):
        corridor |= rasterize(((g.buffer(C.POWERLINE_BUFFER_M), 2) for g in others.geometry),
                              out_shape=corridor.shape, transform=grids.transform, fill=0,
                              dtype="uint8", all_touched=True)
    return corridor


def to_utm(router, geom):
    return transform(lambda x, y, z=None: router.to_utm.transform(x, y), geom)


def route_line(fc):
    coords = []
    for f in fc["features"]:
        if f["properties"]["kind"] == "segment":
            c = f["geometry"]["coordinates"]
            coords += c if not coords else c[1:]
    return LineString(coords)


def sample_distances(a, b):
    """Distances (m) from points every SAMPLE_M along line a to line b."""
    d = np.arange(0, a.length + SAMPLE_M, SAMPLE_M)
    return np.array([b.distance(a.interpolate(x)) for x in np.minimum(d, a.length)])


def metrics(ours, real):
    d = sample_distances(ours, real)
    d_back = sample_distances(real, ours)
    return {
        "route_km": round(ours.length / 1000, 2),
        "real_km": round(real.length / 1000, 2),
        "mean_dist_m": round(float(d.mean())),
        "mean_dist_real_m": round(float(d_back.mean())),
        "median_dist_m": round(float(np.median(d))),
        "p90_dist_m": round(float(np.percentile(d, 90))),
        "max_dist_m": round(float(max(d.max(), d_back.max()))),
        "pct_within_100m": round(float((d <= 100).mean() * 100), 1),
        "pct_within_250m": round(float((d <= 250).mean() * 100), 1),
        "pct_within_500m": round(float((d <= 500).mean() * 100), 1),
    }


def main():
    gdf = load_power_lines()
    full = build_cost_grids()
    with_line_router = Router(full)
    out = []
    for line_id, label in CASES:
        real = real_line(gdf, line_id)
        start, end = list(real.coords[0]), list(real.coords[-1])

        loo = Router(build_cost_grids(corridor_override=corridor_without(gdf, line_id, full)))
        fc = loo.route(start, end)
        real_u = to_utm(loo, real)
        m = metrics(to_utm(loo, route_line(fc)), real_u)
        base = metrics(LineString([loo.lonlat_to_xy(*start), loo.lonlat_to_xy(*end)]), real_u)
        circ = metrics(to_utm(loo, route_line(with_line_router.route(start, end))), real_u)

        # diagnostic: same leave-one-out run, but preserves passable (penalty x1)
        penalty, C.PROTECTED_PENALTY = C.PROTECTED_PENALTY, 1.0
        relaxed_router = Router(build_cost_grids(corridor_override=corridor_without(gdf, line_id, full)))
        C.PROTECTED_PENALTY = penalty
        relaxed = metrics(to_utm(loo, route_line(relaxed_router.route(start, end))), real_u)

        # what the real line itself crosses
        pts = [real_u.interpolate(d) for d in np.arange(0, real_u.length, C.CELL_SIZE_M)]
        rc = np.array([loo.xy_to_rc(q.x, q.y) for q in pts])
        real_land = loo.land_crossings(rc[:, 0], rc[:, 1], np.full(len(rc), C.CELL_SIZE_M))
        real_protected = [{"name": u["name"], "km": u["km"]} for u in real_land["land_units_crossed"] if u["protected"] == 2]
        real_risk = round(float(loo.g.fire_risk[rc[:, 0], rc[:, 1]].mean()), 3)

        print(f"\n=== line {line_id}: {label}")
        print(f"    real {m['real_km']} km  {start} -> {end}")
        print(f"    real line crosses preserves: {real_protected or 'none'}; mean fire risk {real_risk}")
        for name, mm in [("router (leave-one-out)", m), ("straight line", base),
                         ("router, line left in", circ), ("LOO, preserves passable", relaxed)]:
            print(f"    {name:24s} {mm['route_km']:5.2f} km | mean {mm['mean_dist_m']:4d} m (real->ours {mm['mean_dist_real_m']:4d} m), "
                  f"p90 {mm['p90_dist_m']:4d} m, max {mm['max_dist_m']:4d} m | within 100/250/500 m: "
                  f"{mm['pct_within_100m']:5.1f}% / {mm['pct_within_250m']:5.1f}% / {mm['pct_within_500m']:5.1f}%")

        fc["properties"]["validation"] = {"line_id": line_id, "label": label}
        out.append({
            "line_id": line_id, "label": label, "voltage_kv": 69,
            "real_line": {"type": "Feature", "properties": {"kind": "real_line", "name": f"Line {line_id}"},
                          "geometry": mapping(LineString([(round(x, 6), round(y, 6)) for x, y in real.coords]))},
            "route": fc, "metrics": m, "baseline_metrics": base, "with_line_in_corridor_metrics": circ,
            "preserves_passable_metrics": relaxed,
            "real_line_protected_crossed": real_protected, "real_line_mean_fire_risk": real_risk,
        })

    VALIDATION_PATH.write_text(json.dumps({
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "Leave-one-out: each real line is removed from the corridor layer before routing "
                  "between its endpoints. Distances sampled every 10 m.",
        "cases": out,
    }))
    print(f"\nwrote {VALIDATION_PATH}")


if __name__ == "__main__":
    main()
