"""
Least-cost gen-tie routing on the combined cost grid.

1. route_through_array on CostGrids.routing (8-connected, geometric)
2. read the winning mode per path cell, merge runs shorter than MIN_SEGMENT_M
3. split into contiguous overhead / underground runs, simplify each, cost them
4. build a straight overhead baseline for comparison
"""
import time

import numpy as np
from pyproj import Transformer
from shapely.geometry import LineString
from skimage.draw import line as draw_line
from skimage.graph import route_through_array

import config as C
from backend.cost_model import MODE_NAMES, OVERHEAD, UNDERGROUND, CostGrids, build_cost_grids


class RouteError(ValueError):
    pass


def _runs(modes):
    """[(mode, start_idx, end_idx_exclusive), ...] for a 1-D mode array."""
    edges = np.flatnonzero(np.diff(modes)) + 1
    starts = np.r_[0, edges]
    ends = np.r_[edges, len(modes)]
    return [(int(modes[s]), int(s), int(e)) for s, e in zip(starts, ends)]


def smooth_modes(modes, cell_len, min_len):
    """Flip the shortest run below min_len until none remain (avoids OH/UG flicker)."""
    modes = modes.copy()
    while True:
        runs = _runs(modes)
        if len(runs) <= 1:
            return modes
        lengths = [cell_len[s:e].sum() for _, s, e in runs]
        i = int(np.argmin(lengths))
        if lengths[i] >= min_len:
            return modes
        _, s, e = runs[i]
        modes[s:e] = 1 - modes[s]


class Router:
    def __init__(self, grids: CostGrids | None = None):
        self.g = grids or build_cost_grids()
        self.to_utm = Transformer.from_crs("EPSG:4326", C.WORK_CRS, always_xy=True)
        self.to_ll = Transformer.from_crs(C.WORK_CRS, "EPSG:4326", always_xy=True)
        self.nrows, self.ncols = self.g.routing.shape
        self.cs = C.CELL_SIZE_M

    # ------------------------------------------------------------------ coords
    def lonlat_to_xy(self, lon, lat):
        return self.to_utm.transform(lon, lat)

    def xy_to_rc(self, x, y):
        t = self.g.transform
        return int((t.f - y) // self.cs), int((x - t.c) // self.cs)

    def rc_to_xy(self, rc):
        rc = np.asarray(rc, dtype=float)
        t = self.g.transform
        return t.c + (rc[..., 1] + 0.5) * self.cs, t.f - (rc[..., 0] + 0.5) * self.cs

    def xy_to_lonlat(self, xs, ys):
        lon, lat = self.to_ll.transform(np.asarray(xs), np.asarray(ys))
        return np.round(lon, 6), np.round(lat, 6)

    def _validate(self, lonlat, label):
        lon, lat = map(float, lonlat)
        w, s, e, n = C.BBOX
        if not (w <= lon <= e and s <= lat <= n):
            raise RouteError(f"{label} {lon:.5f},{lat:.5f} is outside the study region {C.BBOX}")
        x, y = self.lonlat_to_xy(lon, lat)
        r, c = self.xy_to_rc(x, y)
        if not (0 <= r < self.nrows and 0 <= c < self.ncols):
            raise RouteError(f"{label} is outside the grid")
        return (x, y), (r, c)

    def _line_feature(self, xs, ys, props):
        lon, lat = self.xy_to_lonlat(xs, ys)
        return {"type": "Feature", "properties": props,
                "geometry": {"type": "LineString", "coordinates": [[float(a), float(b)] for a, b in zip(lon, lat)]}}

    # ------------------------------------------------------------------ route
    def route(self, start, end):
        t0 = time.perf_counter()
        (sx, sy), s_rc = self._validate(start, "start")
        (ex, ey), e_rc = self._validate(end, "end")
        if s_rc == e_rc:
            raise RouteError("start and end fall in the same grid cell")

        path, _ = route_through_array(self.g.routing, s_rc, e_rc, fully_connected=True, geometric=True)
        path = np.asarray(path)
        rows, cols = path[:, 0], path[:, 1]

        # length represented by each path cell = half of its in-step + half of its out-step
        step = np.hypot(*np.diff(path, axis=0).T) * self.cs
        cell_len = np.r_[step, 0] / 2 + np.r_[0, step] / 2

        modes = smooth_modes(self.g.mode[rows, cols].astype(np.int8), cell_len, C.MIN_SEGMENT_M)
        cost_by_mode = np.where(modes == UNDERGROUND, self.g.underground[rows, cols], self.g.overhead[rows, cols])
        fire = self.g.fire[rows, cols]
        prot = self.g.protected[rows, cols]

        xs, ys = self.rc_to_xy(path)
        xs[0], ys[0], xs[-1], ys[-1] = sx, sy, ex, ey  # snap ends to the clicked points

        features, towers = [], []
        scaled_len = np.zeros_like(cell_len)  # per-cell length rescaled to the simplified geometry
        totals = dict(total_km=0.0, overhead_km=0.0, underground_km=0.0, high_fire_km=0.0,
                      overhead_high_fire_km=0.0, protected_km=0.0, segment_cost_usd=0.0)
        runs = _runs(modes)
        for i, (mode, s, e) in enumerate(runs):
            # include boundary points shared with neighbouring runs so segments touch
            lo, hi = max(s - 1, 0), min(e + 1, len(path))
            seg_x, seg_y = xs[lo:hi].copy(), ys[lo:hi].copy()
            if s > 0:
                seg_x[0], seg_y[0] = (xs[s - 1] + xs[s]) / 2, (ys[s - 1] + ys[s]) / 2
            if e < len(path):
                seg_x[-1], seg_y[-1] = (xs[e - 1] + xs[e]) / 2, (ys[e - 1] + ys[e]) / 2
            geom = LineString(zip(seg_x, seg_y)).simplify(C.SIMPLIFY_TOLERANCE_M, preserve_topology=False)

            raw_len = cell_len[s:e].sum()
            length_m = geom.length
            scale = length_m / raw_len if raw_len > 0 else 1.0  # stair-step -> simplified length
            scaled_len[s:e] = cell_len[s:e] * scale
            cost = cost_by_mode[s:e] @ cell_len[s:e] * scale
            hf_km = cell_len[s:e][fire[s:e] >= C.HIGH_FIRE_LEVEL].sum() * scale / 1000
            pr_km = cell_len[s:e][prot[s:e] == 2].sum() * scale / 1000

            km = length_m / 1000
            totals["total_km"] += km
            totals[f"{MODE_NAMES[mode]}_km"] += km
            totals["high_fire_km"] += hf_km
            totals["protected_km"] += pr_km
            totals["segment_cost_usd"] += cost
            if mode == OVERHEAD:
                totals["overhead_high_fire_km"] += hf_km
                n = max(int(length_m // C.TOWER_SPACING_M), 1)
                towers += [geom.interpolate(d) for d in np.linspace(0, length_m, n + 1)]

            gx, gy = geom.xy
            features.append(self._line_feature(gx, gy, {
                "kind": "segment", "index": i, "mode": MODE_NAMES[mode],
                "length_km": round(km, 3), "est_cost_usd": round(float(cost)),
                "cost_per_mile_usd": round(float(cost / (km / 1.609344))) if km > 0 else 0,
                "high_fire_km": round(hf_km, 3),
            }))

        n_transitions = len(runs) - 1
        transition_cost = n_transitions * C.TRANSITION_COST_USD
        total_cost = totals["segment_cost_usd"] + transition_cost

        if towers:
            tlon, tlat = self.xy_to_lonlat([p.x for p in towers], [p.y for p in towers])
            features.append({"type": "Feature", "properties": {"kind": "towers", "count": len(towers)},
                             "geometry": {"type": "MultiPoint",
                                          "coordinates": [[float(a), float(b)] for a, b in zip(tlon, tlat)]}})

        baseline = self.baseline((sx, sy), (ex, ey), s_rc, e_rc)
        features.append(self._line_feature([sx, ex], [sy, ey], {"kind": "baseline", **baseline}))

        # elevation profile along the route (<= 200 samples)
        dist = np.r_[0, np.cumsum(step)]
        dist *= totals["total_km"] * 1000 / dist[-1]  # stair-step -> simplified length
        idx = np.unique(np.linspace(0, len(path) - 1, min(len(path), 200)).astype(int))
        profile = [[round(float(dist[k] / 1000), 3), round(float(self.g.elevation[rows[k], cols[k]]), 1),
                    MODE_NAMES[int(modes[k])]] for k in idx]

        props = {
            "start": [float(start[0]), float(start[1])], "end": [float(end[0]), float(end[1])],
            "total_km": round(totals["total_km"], 3),
            "total_cost_usd": round(float(total_cost)),
            "overhead_km": round(totals["overhead_km"], 3),
            "underground_km": round(totals["underground_km"], 3),
            "n_segments": len(runs),
            "n_transitions": n_transitions,
            "transition_cost_usd": transition_cost,
            "high_fire_km": round(totals["high_fire_km"], 3),
            "overhead_high_fire_km": round(totals["overhead_high_fire_km"], 3),
            "protected_cells_crossed": int((prot == 2).sum()),
            "protected_km": round(totals["protected_km"], 3),
            **self.land_crossings(rows, cols, scaled_len),
            "tower_count": len(towers),
            "baseline": baseline,
            "savings_vs_baseline_usd": round(float(baseline["est_cost_usd"] - total_cost)),
            "profile": profile,
            "synthetic_layers": self.g.meta.get("synthetic_layers", []),
            "compute_ms": round((time.perf_counter() - t0) * 1000),
        }
        return {"type": "FeatureCollection", "properties": props, "features": features}

    # ------------------------------------------------------------------ land
    def land_crossings(self, rows, cols, lengths_m):
        """Km by owner category plus the named PAD-US units (incl. tribal lands) a line crosses."""
        owner = self.g.owner[rows, cols]
        unit = self.g.landunit[rows, cols]
        units = self.g.meta.get("land_units", [])
        by_owner = {name: round(float(lengths_m[owner == code].sum() / 1000), 3) for code, name in C.OWNER_NAMES.items()}
        crossed = []
        for uid in np.unique(unit[unit > 0]):
            u = units[uid - 1]
            crossed.append({"name": u["name"], "owner": u["owner"], "protected": u["protected"],
                            "km": round(float(lengths_m[unit == uid].sum() / 1000), 3)})
        crossed.sort(key=lambda u: -u["km"])
        return {
            "land_km_by_owner": by_owner,
            "federal_km": by_owner["federal"],
            "tribal_km": by_owner["tribal"],
            "tribal_lands_crossed": [{"name": u["name"], "km": u["km"]} for u in crossed if u["owner"] == "tribal"],
            "land_units_crossed": crossed,
        }

    # ------------------------------------------------------------------ baseline
    def baseline(self, s_xy, e_xy, s_rc, e_rc):
        """Straight line, built entirely overhead (the 'napkin' route)."""
        rr, cc = draw_line(*s_rc, *e_rc)
        length_m = float(np.hypot(e_xy[0] - s_xy[0], e_xy[1] - s_xy[1]))
        per_cell = length_m / len(rr)
        fire = self.g.fire[rr, cc]
        prot = self.g.protected[rr, cc]
        oh_cost = float(self.g.overhead[rr, cc].sum() * per_cell)
        best_cost = float(self.g.dollar[rr, cc].sum() * per_cell)
        land = self.land_crossings(rr, cc, np.full(len(rr), per_cell))
        return {
            "mode": "overhead",
            "length_km": round(length_m / 1000, 3),
            "est_cost_usd": round(oh_cost),
            "best_mode_cost_usd": round(best_cost),  # same line if each cell could pick OH/UG freely
            "high_fire_km": round(float((fire >= C.HIGH_FIRE_LEVEL).sum() * per_cell / 1000), 3),
            "protected_cells_crossed": int((prot == 2).sum()),
            "protected_km": round(float((prot == 2).sum() * per_cell / 1000), 3),
            "max_slope_deg": round(float(self.g.slope[rr, cc].max()), 1),
            "federal_km": land["federal_km"],
            "tribal_km": land["tribal_km"],
            "tribal_lands_crossed": land["tribal_lands_crossed"],
        }
