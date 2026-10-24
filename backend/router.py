"""
Least-cost gen-tie routing on the combined cost grid.

1. route_through_array on CostGrids.routing (8-connected, geometric)
2. read the winning mode per path cell, merge runs shorter than MIN_SEGMENT_M
3. split into contiguous overhead / underground runs, simplify each, cost them
4. build a straight overhead baseline for comparison
"""
import json
import time

import numpy as np
from pyproj import Transformer
from shapely.geometry import LineString
from skimage.draw import line as draw_line
from skimage.graph import MCP_Geometric, route_through_array

import config as C
from backend.cost_model import MODE_NAMES, OVERHEAD, UNDERGROUND, CostGrids, build_cost_grids


SCHEMA_VERSION = "1.1"


class RouteError(ValueError):
    pass


def permitting_triggers(inventory, land, high_fire_km):
    """Review/consent processes the crossings trigger (flags only - durations are up to the caller)."""
    names = lambda units: ", ".join(u["name"] for u in units)
    t = [{"code": "CEQA", "reason": "California project: CEQA review by the lead agency (county for a merchant "
                                    "gen-tie; CPUC under GO 131-D if utility-built at 50-200 kV)"}]
    if land["federal_km"] > 0:
        t.append({"code": "FEDERAL_ROW_NEPA", "reason": f"Crosses {land['federal_km']:.2f} km of federal land "
                  f"({names(land['federal_units'])}): federal ROW grant/special-use permit + NEPA review"})
    if land["tribal_km"] > 0:
        t.append({"code": "TRIBAL_CONSENT", "reason": f"Crosses {land['tribal_km']:.2f} km of tribal land "
                  f"({names(land['tribal_units'])}): tribal consent for ROW (25 CFR 169) + NHPA Section 106 consultation"})
    if land["protected_km"] > 0:
        t.append({"code": "PROTECTED_AREA", "reason": f"Crosses {land['protected_km']:.2f} km of protected land "
                  f"({names(land['protected_units'])}): only plausible inside an existing easement"})
    if land["state_km"] > 0:
        t.append({"code": "STATE_LANDS", "reason": f"Crosses {land['state_km']:.2f} km of state land: state agency ROW/lease"})
    if high_fire_km > 0:
        t.append({"code": "HFTD", "reason": f"{high_fire_km:.2f} km in CPUC High Fire-Threat District: GO 95 HFTD "
                  "construction rules and wildfire-mitigation review"})
    if inventory["underground_in_road_km"] > 0:
        t.append({"code": "ENCROACHMENT", "reason": f"{inventory['underground_in_road_km']:.2f} km of cable in road "
                  "right-of-way: county/Caltrans encroachment permits"})
    return t


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
    def __init__(self, grids: CostGrids | None = None, precompute=True):
        self.g = grids or build_cost_grids()
        self.to_utm = Transformer.from_crs("EPSG:4326", C.WORK_CRS, always_xy=True)
        self.to_ll = Transformer.from_crs(C.WORK_CRS, "EPSG:4326", always_xy=True)
        self.nrows, self.ncols = self.g.routing.shape
        self.cs = C.CELL_SIZE_M
        self.window_fallbacks = 0  # searches re-run on the full grid (see least_cost_path)
        self.tree_hits = 0         # requests answered from a precomputed substation tree
        self.trees = self._substation_trees() if precompute else {}

    def _substation_trees(self):
        """One single-source least-cost tree per known substation (full-grid Dijkstra, ~0.1 s each).
        Every gen-tie ends at a substation, so most requests become a traceback instead of a search."""
        path = C.OVERLAY_DIR / "substations.geojson"
        if not path.exists():
            return {}
        trees = {}
        for f in json.loads(path.read_text())["features"]:
            lon, lat = f["geometry"]["coordinates"]
            w, s, e, n = C.BBOX
            if not (w <= lon <= e and s <= lat <= n):
                continue
            rc = self.xy_to_rc(*self.lonlat_to_xy(lon, lat))
            mcp = MCP_Geometric(self.g.routing, fully_connected=True)
            mcp.find_costs([rc])
            trees[rc] = mcp
        return trees

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

        path = self.straighten(self.least_cost_path(s_rc, e_rc))
        rows, cols = path[:, 0], path[:, 1]

        # length represented by each path cell = half of its in-step + half of its out-step
        step = np.hypot(*np.diff(path, axis=0).T) * self.cs
        cell_len = np.r_[step, 0] / 2 + np.r_[0, step] / 2

        modes = smooth_modes(self.g.mode[rows, cols].astype(np.int8), cell_len, C.MIN_SEGMENT_M)
        cost_by_mode = np.where(modes == UNDERGROUND, self.g.underground[rows, cols], self.g.overhead[rows, cols])
        fire = self.g.fire[rows, cols]
        risk = self.g.fire_risk[rows, cols]
        prot = self.g.protected[rows, cols]

        xs, ys = self.rc_to_xy(path)
        xs[0], ys[0], xs[-1], ys[-1] = sx, sy, ex, ey  # snap ends to the clicked points

        features, towers = [], []
        scaled_len = np.zeros_like(cell_len)  # per-cell length rescaled to the simplified geometry
        totals = dict(total_km=0.0, overhead_km=0.0, underground_km=0.0, high_fire_km=0.0,
                      overhead_high_fire_km=0.0, high_risk_km=0.0, overhead_high_risk_km=0.0,
                      protected_km=0.0, segment_cost_usd=0.0)
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
            hr_km = cell_len[s:e][risk[s:e] >= C.HIGH_RISK_THRESHOLD].sum() * scale / 1000
            pr_km = cell_len[s:e][prot[s:e] == 2].sum() * scale / 1000

            km = length_m / 1000
            totals["total_km"] += km
            totals[f"{MODE_NAMES[mode]}_km"] += km
            totals["high_fire_km"] += hf_km
            totals["high_risk_km"] += hr_km
            totals["protected_km"] += pr_km
            totals["segment_cost_usd"] += cost
            if mode == OVERHEAD:
                totals["overhead_high_fire_km"] += hf_km
                totals["overhead_high_risk_km"] += hr_km
                n = max(int(length_m // C.TOWER_SPACING_M), 1)
                towers += [geom.interpolate(d) for d in np.linspace(0, length_m, n + 1)]

            gx, gy = geom.xy
            features.append(self._line_feature(gx, gy, {
                "kind": "segment", "index": i, "mode": MODE_NAMES[mode],
                "length_km": round(km, 3), "est_cost_usd": round(float(cost)),
                "cost_per_mile_usd": round(float(cost / (km / 1.609344))) if km > 0 else 0,
                "high_fire_km": round(hf_km, 3),
                "high_risk_km": round(hr_km, 3),
                "mean_fire_risk": round(float(np.average(risk[s:e], weights=cell_len[s:e] + 1e-9)), 3),
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

        land = self.land_crossings(rows, cols, scaled_len)
        ug_lengths_m = [f["properties"]["length_km"] * 1000 for f in features
                        if f["properties"].get("kind") == "segment" and f["properties"]["mode"] == "underground"]
        ug_road_km = scaled_len[(modes == UNDERGROUND) & ((self.g.corridor[rows, cols] & 1) > 0)].sum() / 1000
        inventory = {
            "total_km": round(totals["total_km"], 3),
            "overhead_km": round(totals["overhead_km"], 3),
            "underground_km": round(totals["underground_km"], 3),
            "underground_in_road_km": round(float(ug_road_km), 3),
            "tower_count": len(towers),
            "transition_count": n_transitions,
            "splice_vault_count_est": int(sum(max(0, int(np.ceil(L / C.SPLICE_VAULT_SPACING_M)) - 1) for L in ug_lengths_m)),
            "conductor_km_est": round(totals["overhead_km"] * C.PHASES * C.SAG_FACTOR, 2),
            "cable_km_est": round(totals["underground_km"] * C.PHASES, 2),
        }
        land_group = {
            "km_by_owner": land["land_km_by_owner"],
            "protected_km": round(totals["protected_km"], 3),
            "protected_cells_crossed": int((prot == 2).sum()),
            "federal_km": land["federal_km"],
            "state_km": land["land_km_by_owner"]["state"],
            "tribal_km": land["tribal_km"],
            "private_km": land["land_km_by_owner"]["private"],
            "protected_units": [{"name": u["name"], "km": u["km"]} for u in land["land_units_crossed"] if u["protected"] == 2],
            "federal_units": [{"name": u["name"], "km": u["km"]} for u in land["land_units_crossed"] if u["owner"] == "federal"],
            "tribal_units": land["tribal_lands_crossed"],
            "units_crossed": land["land_units_crossed"],
        }
        row = self.row_inventory(rows, cols, scaled_len)

        props = {
            "schema_version": SCHEMA_VERSION,
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
            "high_risk_km": round(totals["high_risk_km"], 3),
            "overhead_high_risk_km": round(totals["overhead_high_risk_km"], 3),
            "mean_fire_risk": round(float(np.average(risk, weights=scaled_len + 1e-9)), 3),
            "protected_cells_crossed": int((prot == 2).sum()),
            "protected_km": round(totals["protected_km"], 3),
            **land,
            "tower_count": len(towers),
            "inventory": inventory,
            "land": land_group,
            "row": row,
            "permitting_triggers": permitting_triggers(inventory, land_group, totals["high_fire_km"]),
            "baseline": baseline,
            "savings_vs_baseline_usd": round(float(baseline["est_cost_usd"] - total_cost)),
            "profile": profile,
            "synthetic_layers": self.g.meta.get("synthetic_layers", []),
            "compute_ms": round((time.perf_counter() - t0) * 1000),
        }
        return {"type": "FeatureCollection", "properties": props, "features": features}

    # ------------------------------------------------------------------ search
    def least_cost_path(self, s_rc, e_rc, window=True, use_trees=True):
        """Least-cost path from s_rc to e_rc (grid cells).

        1. an endpoint is a known substation -> traceback in its precomputed tree (~ms)
        2. otherwise search inside a window around the endpoints (a 6 km route doesn't need the
           whole 31 x 30 km grid); if the path touches the window edge the window may have
           constrained it, so re-run on the full grid.
        Move costs are symmetric, so a tree rooted at either end gives the same optimal cost."""
        if use_trees and e_rc in self.trees:
            self.tree_hits += 1
            return np.asarray(self.trees[e_rc].traceback(s_rc))[::-1]
        if use_trees and s_rc in self.trees:
            self.tree_hits += 1
            return np.asarray(self.trees[s_rc].traceback(e_rc))
        if window:
            margin = int(max(C.WINDOW_MIN_MARGIN_M / self.cs,
                             C.WINDOW_MARGIN_FRAC * np.hypot(s_rc[0] - e_rc[0], s_rc[1] - e_rc[1])))
            r0, r1 = max(min(s_rc[0], e_rc[0]) - margin, 0), min(max(s_rc[0], e_rc[0]) + margin + 1, self.nrows)
            c0, c1 = max(min(s_rc[1], e_rc[1]) - margin, 0), min(max(s_rc[1], e_rc[1]) + margin + 1, self.ncols)
        else:
            r0, r1, c0, c1 = 0, self.nrows, 0, self.ncols
        sub = self.g.routing[r0:r1, c0:c1]
        path, _ = route_through_array(sub, (s_rc[0] - r0, s_rc[1] - c0), (e_rc[0] - r0, e_rc[1] - c0),
                                      fully_connected=True, geometric=True)
        path = np.asarray(path) + [r0, c0]
        touches_edge = ((r0 > 0 and (path[:, 0] == r0).any()) or (r1 < self.nrows and (path[:, 0] == r1 - 1).any()) or
                        (c0 > 0 and (path[:, 1] == c0).any()) or (c1 < self.ncols and (path[:, 1] == c1 - 1).any()))
        if window and touches_edge:
            self.window_fallbacks += 1
            return self.least_cost_path(s_rc, e_rc, window=False, use_trees=False)
        return path

    # ------------------------------------------------------------------ straighten
    def straighten(self, path):
        """String-pulling: replace grid zig-zags with straight spans wherever a straight span
        costs no more than the path it replaces (within STRAIGHTEN_TOL).

        8-connected least-cost paths are non-unique on near-uniform cost and tend to dog-leg
        (go diagonal, then straight) - up to ~1 km off the true shortest line over 10 km.
        """
        cost = self.g.routing
        step = np.hypot(*np.diff(path, axis=0).T) * self.cs
        c = cost[path[:, 0], path[:, 1]]
        cum = np.r_[0, np.cumsum((c[:-1] + c[1:]) / 2 * step)]  # cost along the path up to vertex k
        n = len(path)

        def ok(i, j):
            rr, cc = draw_line(*path[i], *path[j])
            span = np.hypot(*(path[j] - path[i])) * self.cs
            return cost[rr, cc].mean() * span <= (cum[j] - cum[i]) * (1 + C.STRAIGHTEN_TOL)

        anchors, i = [0], 0
        while i < n - 1:
            best, hop = i + 1, 1
            while True:  # gallop forward...
                j = min(i + hop, n - 1)
                if not ok(i, j):
                    break
                best = j
                if j == n - 1:
                    break
                hop *= 2
            lo, hi = best + 1, j - 1  # ...then binary-search the farthest acceptable vertex
            while lo <= hi:
                mid = (lo + hi) // 2
                if ok(i, mid):
                    best, lo = mid, mid + 1
                else:
                    hi = mid - 1
            anchors.append(best)
            i = best

        cells = [path[0][None]]
        for a, b in zip(anchors, anchors[1:]):
            rr, cc = draw_line(*path[a], *path[b])
            cells.append(np.c_[rr, cc][1:])
        return np.vstack(cells)

    # ------------------------------------------------------------------ right-of-way
    def row_inventory(self, rows, cols, lengths_m):
        """Ordered landholder runs along the route = right-of-way negotiations.

        Each public run is one agreement with that agency/tribe; private runs are split into an
        estimated number of parcels (real parcel ids if a parcel file was provided).
        """
        units = self.g.meta.get("land_units", [])
        owner = self.g.owner[rows, cols]
        key = np.where(owner > 0, self.g.landunit[rows, cols], 0)  # 0 = private / not in PAD-US
        segs = []
        for k, s, e in _runs(key):
            m = float(lengths_m[s:e].sum())
            if segs and (m < C.MIN_ROW_SEGMENT_M or segs[-1]["key"] == k):  # merge rasterization slivers
                segs[-1]["end"] = e
                continue
            segs.append({"key": k, "start": s, "end": e})

        out, dist_km, real = [], 0.0, self.g.parcel_id is not None
        for seq, sg in enumerate(segs):
            s, e, k = sg["start"], sg["end"], sg["key"]
            km = float(lengths_m[s:e].sum() / 1000)
            if k == 0:
                if real:
                    ids = self.g.parcel_id[rows[s:e], cols[s:e]]
                    parcels = max(int(len(np.unique(ids[ids > 0]))), 1)
                else:
                    parcels = max(int(round(float((self.g.parcel_density[rows[s:e], cols[s:e]] * lengths_m[s:e]).sum() / 1000))), 1)
                holder = {"owner": "private", "unit": None, "parcels": parcels}
            else:
                u = units[k - 1]
                holder = {"owner": u["owner"], "unit": u["name"], "parcels": 1}
            out.append({"seq": seq, **holder, "start_km": round(dist_km, 3), "km": round(km, 3)})
            dist_km += km

        private_parcels = sum(r["parcels"] for r in out if r["owner"] == "private")
        public_units = {r["unit"] for r in out if r["owner"] != "private"}
        return {
            "segment_count": len(out),
            "private_parcels": private_parcels,
            "private_parcels_method": "real parcel polygons" if real else "estimated from building density",
            "public_agreements": len(public_units),
            "landholder_negotiations_est": private_parcels + len(public_units),
            "segments": out,
        }

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
        risk = self.g.fire_risk[rr, cc]
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
            "high_risk_km": round(float((risk >= C.HIGH_RISK_THRESHOLD).sum() * per_cell / 1000), 3),
            "mean_fire_risk": round(float(risk.mean()), 3),
            "protected_cells_crossed": int((prot == 2).sum()),
            "protected_km": round(float((prot == 2).sum() * per_cell / 1000), 3),
            "max_slope_deg": round(float(self.g.slope[rr, cc].max()), 1),
            "federal_km": land["federal_km"],
            "tribal_km": land["tribal_km"],
            "tribal_lands_crossed": land["tribal_lands_crossed"],
        }
