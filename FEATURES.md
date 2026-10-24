# Feature Status

Snapshot of every feature in Gen-tie Router and how finished it is. Last checked 2026-10-24 against commit `d7b994e`.

**Legend**

| Status | Meaning |
|---|---|
| ✅ Done | Works, tested or verified, nothing needed for the demo |
| 🔧 Needs tweaks | Works, but has a known gap, rough edge or inconsistency |
| ⚠️ Works, weak result | Code is correct, but the output isn't convincing yet |
| ⬜ Not started | Referenced or implied, but no code exists |

**Verification:** `python -m backend.test_route` → ALL CHECKS PASSED. `python -m backend.test_api` → ALL API CHECKS PASSED (both re-run on `d7b994e`). An earlier headless-Chromium run exercised click-to-route, overlays, demo route, validation and the out-of-region toast with no page errors. That run predates the 3D/view-mode commits, so those features (marked † below) are verified in code only.

---

## Summary

| Area | ✅ | 🔧 | ⚠️ | ⬜ |
|---|---|---|---|---|
| Data pipeline | 8 | 1 | 0 | 0 |
| Cost model & routing | 7 | 0 | 1 | 0 |
| Route outputs (schema v1.1) | 6 | 1 | 0 | 1 |
| API & performance | 5 | 1 | 0 | 0 |
| Frontend | 16 | 4 | 0 | 0 |
| Validation | 1 | 0 | 1 | 0 |
| HackOhio outline (planned) | 0 | 0 | 0 | 7 |

---

## 1. Data pipeline (`data/prepare_data.py`)

| Feature | Status | Notes |
|---|---|---|
| Download → clip → reproject → rasterize to one 30 m UTM grid (1030 × 1001) | ✅ Done | `meta.json` shows `synthetic_layers: []`, so every layer is real data. |
| Elevation + slope (USGS 3DEP) | ✅ Done | |
| Land status, ownership, named units (PAD-US 3.0) | ✅ Done | Allotted/fee tribal land outside reservations isn't in PAD-US, so it isn't flagged. |
| Wildfire: continuous WHP 2024 + HFTD/FHSZ tiers, with fallback | ✅ Done | WHP reflects 2020 fuels; FHSZ SRA layer is from 2007. |
| Corridors: CEC transmission lines (8 SDG&E 69/138 kV lines, ~85 km) merged with OSM lines + major roads | ✅ Done | Upstream replaced the OSM-only data, which had ~30 km of lines. |
| Substations (CEC: Creelman, Santa Ysabel, Boulder Creek, merged with OSM) | ✅ Done | Used for click-to-snap and the precomputed routing trees. |
| Synthetic-placeholder fallback per layer + amber UI badge | ✅ Done | |
| Basemap context (`data/prepare_basemap.py`) | ✅ Done | Natural Earth borders + metros, OSM cities/towns; falls back to Natural Earth only if Overpass is down. |
| Real parcel polygons (SanGIS) | 🔧 Needs tweaks | Code path exists, but no parcel file is provided (`data/raw/` is absent), so parcel counts are **estimated from building density**. |

## 2. Cost model & routing (`backend/cost_model.py`, `backend/router.py`)

| Feature | Status | Notes |
|---|---|---|
| Per-cell overhead / underground cost grids (slope, fire, corridors, land) | ✅ Done | All weights in `config.py`; restart the server to apply changes. |
| Least-cost path (8-connected, geometric) | ✅ Done | ~4–9 ms per route in the smoke test. |
| Search window with full-grid fallback | ✅ Done | |
| Precomputed per-substation Dijkstra trees | ✅ Done | One tree per known substation. |
| String-pulling (removes grid dog-legs, 1% tolerance) | ✅ Done | Came out of validation; fixed up to 1.3 km of drift. |
| Overhead/underground segmentation with min run length (450 m) + transition cost | ✅ Done | |
| **Overhead vs underground choice** | ⚠️ Works, weak result | At $10M/mi underground, almost no route buries any line. At $5M/mi, 84% of routes use some underground. Decide which number to demo, or add a UI slider. |
| Tribal land (×10 penalty) and protected land (×1000, ×3 inside an existing easement) | ✅ Done | Smoke test confirms tribal avoidance (1.79 km → 0 km). |

## 3. Route outputs (`POST /route`, schema v1.1)

| Feature | Status | Notes |
|---|---|---|
| Segments, towers, straight-line baseline features | ✅ Done | |
| `inventory` (towers, transitions, vaults, conductor/cable km) | ✅ Done | |
| `land` (km by owner, named units crossed) | ✅ Done | |
| `row` (landholder runs, parcels, agreements) | ✅ Done | Parcel count is reported but **not priced**, so routes can favour subdivided valleys. |
| `permitting_triggers` (CEQA, NEPA, tribal, protected, state, HFTD, encroachment) | ✅ Done | Flags only, no durations. |
| Elevation profile (≤ 200 samples) | ✅ Done | |
| Legacy v1.0 flat fields | 🔧 Needs tweaks | Kept for the frontend, which still reads them instead of the v1.1 groups. Migrate the frontend, then they can be dropped in 2.0. |
| **Permitting timeline / durations** | ⬜ Not started | The README refers to "the timeline feature," but nothing implements it. See the deployment calendar in section 7. |

## 4. API & performance (`backend/app.py`, `backend/worker.py`, `loadtest/`)

| Feature | Status | Notes |
|---|---|---|
| `POST /route` with 400 on out-of-region and 422 on malformed input | ✅ Done | Verified by `test_api`. |
| `GET /bounds`, `/overlays/{name}`, `/health`, `/stats` | ✅ Done | |
| Process pool (default 8 workers) with grids loaded once per worker | ✅ Done | `/health` stays at ≤ 5 ms while all workers are busy. |
| LRU response cache + single-flight for identical requests | ✅ Done | 20 identical concurrent requests → 1 compute. |
| `GET /assumptions` | 🔧 Needs tweaks | The README says "every cost constant", but it omits `PROTECTED_IN_CORRIDOR_PENALTY`, `OUTSIDE_REGION_PENALTY`, `HIGH_FIRE_LEVEL`, `STRAIGHTEN_TOL` and the inventory constants. |
| Load-test harness + results (Locust + `run_bench.py`) | ✅ Done | `loadtest/RESULTS.md`: 7.4 → 646 req/s (8 workers, substation routes), p95 63 ms; 2,746 req/s with cache. Arbitrary (non-substation) routes top out at ~80 req/s. |

## 5. Frontend (`frontend/`)

| Feature | Status | Notes |
|---|---|---|
| Cesium 3D terrain viewer, framed on the study region | ✅ Done | Falls back to flat terrain + OSM imagery without a token. |
| Click project site → click substation → route; substation snapping | ✅ Done | |
| Route drawing: orange overhead, dashed cyan underground, tower glyphs, baseline | ✅ Done | |
| Results panel: cost, length, fire, protected, tribal, transitions | ✅ Done | |
| Right-of-way & permitting section | ✅ Done | |
| Route vs straight-line comparison table + notes | ✅ Done | |
| Elevation profile (SVG, coloured by mode) | ✅ Done | |
| Demo mode: "Load demo route" works with no backend | ✅ Done | `demo_route.json` is on schema 1.1. |
| "About this demo" explainer card | ✅ Done † | Says the route is hypothetical, with cost breakdown and reasoning. |
| View modes: Enhanced (terrain ×1.5, sun/shadows, 3D poles + sagging conductors < 9 km) and Top View (flat, fast) | ✅ Done † | Choice is remembered; switching keeps the camera. |
| ▶ Fly along route | ✅ Done † | 20–60 s fly-through; click or Esc stops it. |
| Render performance (FXAA, render-on-demand, no quality switching while dragging) | ✅ Done † | ~63 fps on Intel UHD vs 12 fps before (per README). |
| Elevation contours (100 m) toggle + elevation readout on hover | ✅ Done † | |
| City & town labels (zoom-tiered: 8 metros, 14 cities ≥ 50k, 7 local towns) | ✅ Done | `data/prepare_basemap.py` → `frontend/basemap/cities.geojson`. Works in demo mode. Verified in headless Chromium. |
| Country (US–Mexico) and state borders | ✅ Done | Natural Earth 10m, simplified ~300 m. Verified in headless Chromium. |
| Mouse controls (right-drag look-around, Ctrl/middle-drag tilt) | ✅ Done † | |
| Overlay toggles (hazard, HFTD, protected, tribal, power lines, baseline) | 🔧 Needs tweaks | The backend serves a `roads` overlay, but there's no toggle for it. |
| Panel depth vs API | 🔧 Needs tweaks | Not shown: `row.segments` (landholder-by-landholder list), `land.units_crossed`, `km_by_owner`, conductor/cable km. |
| Cost legend text | 🔧 Needs tweaks | "$2M/mi overhead, $10M/mi underground" is hardcoded in `index.html`. It goes stale if `config.py` changes; read it from `/assumptions` instead. |
| Cesium ion token handling | 🔧 Needs tweaks | `config.js` is git-ignored (good), but the token is visible to anyone who loads the page. Restrict allowed URLs in the Cesium dashboard before deploying. |

## 6. Validation (`backend/validate.py`, "Compare to real line")

| Feature | Status | Notes |
|---|---|---|
| Leave-one-out validation against 2 real SDG&E 69 kV lines + UI comparison | ✅ Done | `frontend/validation.json` has 2 cases. |
| **Validation result** | ⚠️ Works, weak result | With the real line hidden, the router misses by 1.4–1.5 km on average, and a straight line predicts the real lines better. Most of the gap is explained by the preserve penalty (line 637), and line 635 has an equal-cost alternative corridor. Two lines is a sanity check, not a statistical validation. |

## 7. HackOhio outline features (planned, no code yet)

From the gap analysis against the HackOhio project outline.

| Feature | Status | Notes |
|---|---|---|
| Deployment calendar / permitting timeline | ⬜ Not started | Natural first build: `permitting_triggers` already lists which reviews apply, so this only needs durations per trigger. |
| Inventory availability (towers, cable, conductor) | ⬜ Not started | Quantities already exist in `inventory`; there's no supply data to compare them against. |
| Suppliers & staging yards | ⬜ Not started | |
| Geoapify integration | ⬜ Not started | |
| Open-Meteo weather integration | ⬜ Not started | |
| Population / market-share context | ⬜ Not started | |
| Non-fire hazards (flood, seismic, etc.) | ⬜ Not started | Only wildfire is modelled today. |

---

## Suggested next steps (priority order)

1. **Pick the underground cost for the demo** (or add a slider). Right now the "picks overhead or underground per segment" headline rarely shows underground at $10M/mi.
2. **Build the deployment calendar**, driven by `permitting_triggers`. It's the biggest outline gap and the API already has the inputs.
3. **Click through the new 3D features (†) in a browser**: view switch, fly-along, contours.
4. Small frontend polish: roads toggle, legend from `/assumptions`, show `row.segments`.
