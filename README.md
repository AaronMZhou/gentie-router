# Gen-tie Router

A multi-page planner for short gen-tie lines. Create a project, click a proposed renewable project site and a substation on a 3D terrain map, and the app finds the least-cost route between them, picks **overhead or underground for each segment**, and draws it in 3D with towers and a straight-line baseline. Each project holds several candidate routes (one per strategy preset) to compare side by side, and each route has its own page with costs, inventory, maintenance, a deployment calendar, permits and the full route analysis.

**Region:** San Diego County backcountry: Ramona, Santa Ysabel and Julian (bbox `-116.89, 32.94, -116.56, 33.21`, about 31 × 30 km). The terrain runs from about 215 m to 1,980 m. About 95% of the area is CPUC HFTD Tier 3, and it has a patchwork of preserves, state park, Cleveland NF and tribal land.

```
data/
  prepare_data.py        one-off pipeline: download -> clip -> reproject -> rasterize
  raw/                   cached downloads (git-ignored)
  processed/             aligned 30 m GeoTIFFs + overlays/*.geojson + meta.json
backend/
  cost_model.py          overhead / underground / combined cost grids
  router.py              least-cost path, OH/UG segmentation, stats, baseline
  app.py                 FastAPI: POST /route, GET /bounds, overlays, projects/routes; also serves frontend/
  store.py, seed.py      SQLite project/route store + sample projects
  test_route.py          stage-2 smoke test (prints routes, writes demo route)
  test_api.py, test_projects.py   API and project-store checks
frontend/
  index.html             landing page
  dashboard.html         every project, KPIs, "+ Start a project"
  project.html           one project: all its routes on the map, route cards, add/rename/delete, validation
  route.html             one route: 3D hero with fly-along, costs, inventory, calendar, permits, full analysis
  styles/                tokens.css, components.css, STYLES.md (the style guide), preview.html
  js/                    map.js (Cesium), panel.js, api.js, estimates.js, theme.js, pages/*.js
  config.js              your Cesium ion token (git-ignored; see config.example.js)
  demo_route.json        precomputed route for the offline demo (route.html?demo=1)
tools/smoke_pages.py     headless-Chrome page smoke test
config.py                region, grid, data URLs, every cost weight and the strategy presets
```

## Setup

Requires Python 3.11+ (tested on 3.13).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Cesium token: copy `frontend/config.example.js` to `frontend/config.js` and paste your Cesium ion token. Without a token the app still runs, but with flat terrain and OSM imagery.

## 1. Prepare data (once, about 15 s to 3 min)

```bash
python data/prepare_data.py              # uses cached downloads in data/raw if present
python data/prepare_data.py --refresh    # force re-download
python data/prepare_data.py --synthetic-all   # offline: synthetic placeholders for every layer
```

| Layer | Source | Grid value |
|---|---|---|
| Elevation → slope | USGS 3DEP ImageServer (requested directly on the UTM grid) | metres / degrees |
| Land status | PAD-US 3.0 Fee (USGS ArcGIS service) | `protected.tif` 2 = GAP 1–2 or state park / wilderness, 1 = GAP 3 multiple-use; `owner.tif` private / federal / state / local / tribal / ngo; `landunit.tif` id of the named PAD-US unit |
| Wildfire hazard (continuous) | USFS Wildfire Risk to Communities, **Wildfire Hazard Potential 2024, 30 m** (burn probability × intensity), via the imagery.geoplatform.gov ImageServer | `firerisk.tif` 0–1 = clip(WHP / 25,000)^0.5. **Fallback** if the download fails: derived from the tier layer below (0 / 0.25 / 0.5 / 1.0) |
| Wildfire tier (regulatory) | CPUC High Fire-Threat District (Tier 2/3) + CAL FIRE FHSZ (SRA 2007, LRA 2011), max of the two | `fire.tif` 0–3 |
| Corridors | **California Energy Commission** transmission lines (official; 8 SDG&E 69/138 kV lines, ~85 km in the region) merged with OpenStreetMap via Overpass (`power=line/minor_line`, `highway=motorway/trunk/primary/secondary`). OSM alone had only ~30 km of the region's power lines | bit 1 road, bit 2 power line |
| Substations | CEC substation data (Creelman, Santa Ysabel, Boulder Creek; public re-host because the CEC layer needs a token), merged with OSM | `overlays/substations.geojson` (click-to-snap targets and precomputed routing trees) |

Map context (country/state borders and city labels) comes from a separate, quick script. Its output is committed in `frontend/basemap/`, so re-run it only to change the extent or population cut-offs in `config.py`:

```bash
python data/prepare_basemap.py           # Natural Earth borders + metros, OSM cities/towns -> frontend/basemap/
```

Everything is clipped to the bbox, reprojected to EPSG:32611 (UTM 11N) and burned onto one aligned 1030 × 1001 grid of 30 m cells. If a download fails, that layer is replaced by **synthetic placeholder data**. The script prints `*** SYNTHETIC PLACEHOLDER LAYERS: ... ***`, the layer is listed in `meta.json`, and the map shows an amber "SYNTHETIC data" badge. Overpass is often busy, so the script cycles through three mirrors before giving up.

## 2. Test routing without the API

```bash
python -m backend.test_route              # prints 3 example routes + sanity checks
python -m backend.test_route --save-demo  # also refreshes frontend/demo_route.json
```

## 3. Run

```bash
python -m uvicorn backend.app:app --port 8000
```

Open **http://localhost:8000**. The backend serves the frontend too, so this one command runs everything. On first start with an empty store it seeds four sample projects (Ramona Solar Gen-tie, Santa Ysabel Reinforcement, Julian Wind Interconnect, Boulder Creek BESS Tie) with real computed routes.

| Environment variable | Effect |
|---|---|
| `GENTIE_DB` | project store path (default `data/projects.db`, git-ignored) |
| `GENTIE_NO_SEED=1` | don't create the sample projects in an empty store |
| `ROUTE_WORKERS`, `ROUTE_CACHE_SIZE` | routing worker processes (default 8; 0 = inline) and cached responses (default 1024) |

**Pages**

- **Landing** (`/`): what the tool does. "Start a project" goes to the dashboard; "Watch a route get built" opens the offline demo.
- **Dashboard** (`dashboard.html`): a card per project with a route thumbnail and phase, plus KPIs. **+ Start a project** asks for a name, voltage and phase, then opens the project page in pick mode.
- **Project** (`project.html?id=…`): every candidate route on one Cesium map, with a card rail beside it. Click once to place the **project site**, then again to place the **substation**; clicking a purple substation marker snaps to it, and a click outside the dashed study region shows a toast. **+ Add route** computes another route with a strategy preset; cards can be renamed or deleted; **Change endpoints** re-picks both points and recomputes every route; **Project ⋯** edits the name, phase and filing target or deletes the project. The **Layers · view** HUD has the view switch, terrain height, every overlay toggle and **Compare to real line**.
- **Route** (`route.html?id=…`): a 3D hero with the flowing route line, **▶ Fly along**, a layers HUD, the data badge (amber when any layer is synthetic) and a hover/click segment callout; then costs, inventory, maintenance, deployment calendar, permits & legal, and the full route analysis (every results metric, right-of-way, vs-straight table, elevation profile, segments, the cost-assumption note from `/assumptions`, camera help). **Export packet** downloads the route + estimate as JSON; **Mark for filing** sets the project phase to ready.
- **Demo** (`route.html?demo=1`): the route page on `demo_route.json` with no API calls, plus the "About this demo" card.

Every page has a light/dark toggle (remembered; defaults to the OS setting). Maintenance, lifetime cost, calendar, lead times and permit durations come from `frontend/js/estimates.js` and are **placeholders**, marked `est.` wherever they appear; the deployment calendar is one of them. The upfront total, inventory, triggers and every route-analysis metric are real model output.

**Strategy presets** (`PRESETS` in `config.py`: weight overrides for the path search; every route is priced on the balanced cost model):

| Preset | Label | What changes |
|---|---|---|
| `balanced` | Balanced | the default cost model; `POST /route` without `preset` uses it |
| `avoid_fire` | Avoid fire | overhead wildfire multiplier up to ×10, HFTD Tier 3 ×5 |
| `cheapest` | Direct | lighter fire and managed-land weighting; protected and tribal penalties kept |
| `underground` | Prefer underground | searches as if burial cost ~$4M/mi |

**Map controls** (project and route pages):

- **View modes**, switched in the map's layers HUD; the app remembers your choice:
  - **Enhanced** (default): terrain exaggerated 1.5× (the **Terrain height** dropdown changes it: 1× true scale, 1.5×, 2× or 3×; the choice is remembered), early-afternoon sun with shading and shadows, and a low oblique camera. Within about 9 km, overhead lines appear as **3D poles with crossarms and three sagging conductors**, with the orange trace running tower to tower beneath them.
  - **Top View**: the basic, fastest version. Flat ground (no 3D terrain), satellite imagery, flat tower icons, no lighting or shadows. Useful for picking points.
  - Switching views keeps the camera where it is; only the rendering changes.
- **▶ Fly along** (route page) flies the camera along the line in 20–60 s, counting towers as it passes them. Click or press Esc to stop.
- **Performance:** render settings are fixed per view, with nothing switching while you drag. An earlier version lowered quality during camera moves, and that made brightness, shadows and terrain detail flicker. Enhanced renders at CSS-pixel resolution with FXAA (not device resolution with 4× MSAA) and keeps HDR, shadows and full terrain detail. While panning on an Intel UHD integrated GPU at 2× display scaling, that's about 63 fps, against 12 fps at device resolution. When nothing changes the app renders no frames at all (Cesium `requestRenderMode`), so the GPU idles.
- The demo route page also shows an "About this demo" card. It explains that the route is a hypothetical example (not a real project) and gives the estimated cost with a breakdown, why the route goes where it does, and what it would take.
- **Mouse:** left-drag pans, **right-drag looks around** from where you are, the wheel zooms, Ctrl- or middle-drag tilts. Hovering shows the elevation under the cursor.
- Route colours come from the theme tokens (see `frontend/styles/STYLES.md`): overhead is solid, underground dashed, other candidate routes on the project page are dimmer, and the straight baseline is a faint line. The elevation profile shows total climb and descent.
- Toggles: wildfire hazard (USFS WHP, draped image), HFTD/FHSZ tiers, protected land, tribal land, existing power lines, baseline, 100 m elevation contours (off by default), cities & towns, and country/state borders. City labels appear by size as you zoom in: metros (LA, San Diego, Tijuana, Phoenix) always, cities like Escondido and El Cajon within 250 km, and towns like Ramona and Julian within 60 km.

**Demo safety:** `route.html?demo=1` reads `frontend/demo_route.json` and needs no backend. If the backend dies, serve just the static files: the landing page and the demo still work, and the dashboard, project and route pages show a "Backend unreachable" state with a Retry button (the dashboard's also links to the demo):

```bash
python -m http.server 8100 --directory frontend   # then open http://localhost:8100/route.html?demo=1
```

Page smoke test (headless Chrome; starts its own server on :8767 with a temp seeded DB):

```bash
python tools/smoke_pages.py / /dashboard.html "/project.html?id=1" "/route.html?id=1"
python tools/smoke_pages.py --no-server / "/route.html?demo=1"   # static files only, no API
```

To serve the frontend separately from the API, set `API_BASE: "http://localhost:8000"` in `frontend/config.js` (CORS is open).

**Run it locally only.** CORS allows all origins and the API has unauthenticated mutating endpoints (create/rename/delete projects and routes), so it is a local dev server, not something to deploy. Keep uvicorn bound to `127.0.0.1` (the default); even then, a page open in your browser could call it.

## Validation against real lines

```bash
python -m backend.validate   # prints metrics, writes frontend/validation.json for the "Compare to real line" button
```

Can the router reproduce existing SDG&E 69 kV lines from OSM? Each line is **removed from the corridor layer** before routing between its endpoints (leave-one-out). Lines are identified by their OSM ref; the CEC copy of the same circuit is removed too, by cutting every other line within 200 m of the hidden one. Otherwise the "follow existing lines" discount would just trace it. Distances are sampled every 10 m along our route.

| Line (real length) | Variant | Mean distance to real line | Within 500 m |
|---|---|---|---|
| 637, Creelman → east (8.7 km) | **Router, line hidden** | **1,520 m** | **13%** |
| | Straight line (reference) | 177 m | 100% |
| | Router, line known (co-location) | 304 m | 66% |
| | Line hidden, preserves passable | 176 m | 89% |
| 635, Creelman → south (9.2 km in-region) | **Router, line hidden** | **1,425 m** | **18%** |
| | Straight line (reference) | 153 m | 100% |
| | Router, line known (co-location) | 30 m | 100% |
| | Line hidden, preserves passable | 1,425 m | 18% |

What this shows:
- **The router does not reproduce these lines, and a straight line is a better predictor of where they are.** These 69 kV lines are nearly straight and predate today's constraints.
- **Line 637 crosses 3.4 km of county/BLM preserves** (Simon Preserve, Mt. Gower Preserve), which the model treats as near-impassable for new right-of-way. Making preserves passable cuts the error from 1,520 m to 176 m (89% within 500 m), so most of the gap is a deliberate modelling choice, not noise.
- **Line 635's gap has no single cause.** Removing fire, slope, roads, the power-line discount or preserves one at a time each changes it by under 15%. The cost surface has a near-equal alternative corridor to the east via Cañada de San Vicente.
- **Two fixes came out of this.** (1) *String-pulling*: on near-uniform cost, the 8-connected grid path dog-legged up to 1.3 km off the true shortest line. The router now replaces zig-zags with straight spans wherever they cost no more (1% tolerance), and a pure-distance route now matches the straight line within 153–177 m. (2) *Co-location*: a preserve crossed **inside an existing power-line easement** costs ×3 instead of ×1000. With the line known, the router now follows line 635 within 30 m.
- This is 2 lines in one region. It's a sanity check, not a statistical validation.

## API

Interactive docs: `http://localhost:8000/docs`. Other endpoints:

- `GET /bounds`: region bbox, centre, grid, data provenance, `synthetic_layers`
- `GET /overlays/{name}`: `firerisk` (PNG over the bbox), plus `fire`, `protected`, `tribal`, `powerlines`, `roads` and `substations` (GeoJSON)
- `GET /assumptions`: the main cost constants, plus `PRESETS` (`{key: {label, blurb}}`)
- `GET /health`, `GET /stats`

### Projects and routes

Stored in SQLite (`GENTIE_DB`, default `data/projects.db`). Phases: `drafting | comparing | permit-risk | ready`.

| Endpoint | Body | Returns |
|---|---|---|
| `GET /projects` | | every project with route summaries (no full GeoJSON) and small simplified geometries for thumbnails |
| `POST /projects` | `{name, voltage_kv, phase, filing_target?}` | the new project (201) |
| `GET /projects/{id}` | | the project with its route summaries |
| `PATCH /projects/{id}` | any of `name, voltage_kv, phase, filing_target, start_lonlat, end_lonlat` | the project. New endpoints recompute every route first, so a bad endpoint (400) changes nothing |
| `DELETE /projects/{id}` | | 204; its routes go too |
| `POST /projects/{id}/routes` | `{preset, start_lonlat?, end_lonlat?}` | `{route_summary, result}` (201). Computes and stores a route named after the preset; endpoints default to the project's, and passing them updates the project |
| `GET /routes/{id}` | | the stored route; `result` is the full `POST /route` response |
| `PATCH /routes/{id}` | `{name}` | the route |
| `DELETE /routes/{id}` | | 204 |

Unknown ids return 404. Out-of-region or same-cell endpoints return 400 with a `detail` message and store nothing.

### `POST /route`, schema v1.1

**Request**

```json
{"start": [-116.69, 33.10], "end": [-116.85305, 33.01807], "preset": "balanced"}
```

`start` and `end` are `[lon, lat]` in WGS84 and must be inside the region bbox, otherwise the API returns `400` with a `detail` message. `preset` is optional and defaults to `balanced`, which gives exactly the pre-preset result; an unknown preset also returns `400`. A malformed body returns `422`.

**Response:** a GeoJSON `FeatureCollection` with a top-level `properties` object.

Units are fixed across the whole schema: lengths in **km**, money in **USD (integers)**, coordinates `[lon, lat]`. Every `*_est` field is a modelled estimate, not survey data. So is `row.private_parcels` whenever `private_parcels_method` starts with `"estimated"`.

#### `features[]`, distinguished by `properties.kind`

| `kind` | Geometry | Properties |
|---|---|---|
| `segment` | LineString | `index` (order along the route), `mode` (`overhead`/`underground`), `length_km`, `est_cost_usd`, `cost_per_mile_usd`, `high_fire_km`, `high_risk_km`, `mean_fire_risk` |
| `towers` | MultiPoint | `count`: overhead structure locations (~every 300 m) |
| `baseline` | LineString | straight all-overhead line between the same endpoints; same fields as `properties.baseline` |

Segments are ordered and touch end-to-start. Adjacent segments always have different `mode`s.

#### `properties.inventory`: what gets built

| Field | Meaning |
|---|---|
| `total_km`, `overhead_km`, `underground_km` | route length by mode (`overhead_km + underground_km = total_km`) |
| `underground_in_road_km` | underground length inside road right-of-way |
| `tower_count` | overhead structures (one every ~300 m, plus segment ends) |
| `transition_count` | overhead↔underground riser structures (`= number of segments − 1`) |
| `splice_vault_count_est` | underground splice vaults, one per 550 m cable section |
| `conductor_km_est` | overhead conductor: `overhead_km × 3 phases × 1.03 sag` |
| `cable_km_est` | underground cable: `underground_km × 3` single-core cables |

#### `properties.land`: what it crosses

| Field | Meaning |
|---|---|
| `km_by_owner` | `{private, federal, state, local, tribal, ngo}` km. The values sum to `total_km`; land not in PAD-US counts as `private` |
| `protected_km`, `protected_cells_crossed` | GAP 1–2 / state park / wilderness |
| `federal_km`, `state_km`, `tribal_km`, `private_km` | shortcuts into `km_by_owner` |
| `protected_units`, `federal_units`, `tribal_units` | `[{name, km}]`, e.g. `{"name": "Mesa Grande Reservation", "km": 0.613}` |
| `units_crossed` | every named PAD-US unit crossed: `[{name, owner, protected (0/1/2), km}]`, longest first |

#### `properties.row`: right-of-way negotiations

| Field | Meaning |
|---|---|
| `segments` | ordered landholder runs: `[{seq, owner, unit, start_km, km, parcels}]`. `unit` is `null` for private land. Public runs have `parcels: 1` (one agreement with that agency) |
| `segment_count` | number of runs (the same agency crossed twice counts twice) |
| `private_parcels` | parcels crossed on private land |
| `private_parcels_method` | `"estimated from building density"` (default) or `"real parcel polygons"` (when `data/raw/parcels.*` was provided) |
| `public_agreements` | distinct public/tribal units crossed |
| `landholder_negotiations_est` | `private_parcels + public_agreements` |

#### `properties.permitting_triggers`: review processes the crossings trigger

These are flags only, so the timeline feature decides durations. Each item is `{code, reason}`, where `reason` is a human-readable string naming the units and km.

| `code` | When |
|---|---|
| `CEQA` | always (California project) |
| `FEDERAL_ROW_NEPA` | `land.federal_km > 0` |
| `TRIBAL_CONSENT` | `land.tribal_km > 0` |
| `PROTECTED_AREA` | `land.protected_km > 0` |
| `STATE_LANDS` | `land.state_km > 0` |
| `HFTD` | route length in CPUC HFTD Tier 2/3 > 0 |
| `ENCROACHMENT` | `inventory.underground_in_road_km > 0` |

#### Other `properties`

| Field | Meaning |
|---|---|
| `schema_version` | `"1.1"`. Groups and fields are only added, never renamed, within 1.x |
| `start`, `end` | the request points |
| `total_cost_usd`, `transition_cost_usd`, `savings_vs_baseline_usd` | estimated construction cost; routing penalties (protected/tribal) are **not** dollars |
| `high_fire_km`, `overhead_high_fire_km` | km in HFTD Tier 2/3 (regulatory) |
| `high_risk_km`, `overhead_high_risk_km`, `mean_fire_risk` | continuous USFS hazard (risk ≥ 0.5; mean 0–1) |
| `baseline` | straight line: `length_km`, `est_cost_usd`, `best_mode_cost_usd`, `high_fire_km`, `high_risk_km`, `mean_fire_risk`, `protected_km`, `protected_cells_crossed`, `federal_km`, `tribal_km`, `tribal_lands_crossed`, `max_slope_deg` |
| `profile` | `[[distance_km, elevation_m, mode], …]`, ≤ 200 samples |
| `synthetic_layers` | input layers that were synthetic placeholders (empty with real data) |
| `compute_ms` | server compute time |
| *legacy flat fields* | `overhead_km`, `underground_km`, `tower_count`, `n_segments`, `n_transitions`, `protected_km`, `federal_km`, `tribal_km`, `tribal_lands_crossed`, `land_km_by_owner`, `land_units_crossed`. Kept for the v1.0 frontend; prefer the groups above |

**Example** (trimmed; route from inside Mesa Grande Reservation to Creelman Sub):

```json
{
  "type": "FeatureCollection",
  "properties": {
    "schema_version": "1.1",
    "total_km": 13.62, "total_cost_usd": 38644201,
    "inventory": {"total_km": 13.62, "overhead_km": 13.62, "underground_km": 0.0, "underground_in_road_km": 0.0,
                  "tower_count": 46, "transition_count": 0, "splice_vault_count_est": 0,
                  "conductor_km_est": 42.09, "cable_km_est": 0.0},
    "land": {"km_by_owner": {"private": 13.01, "federal": 0.0, "state": 0.0, "local": 0.0, "tribal": 0.61, "ngo": 0.0},
             "tribal_km": 0.61, "tribal_units": [{"name": "Mesa Grande Reservation", "km": 0.613}]},
    "row": {"segment_count": 2, "private_parcels": 83, "private_parcels_method": "estimated from building density",
            "public_agreements": 1, "landholder_negotiations_est": 84,
            "segments": [{"seq": 0, "owner": "tribal", "unit": "Mesa Grande Reservation", "start_km": 0.0, "km": 0.61, "parcels": 1},
                         {"seq": 1, "owner": "private", "unit": null, "start_km": 0.61, "km": 13.01, "parcels": 83}]},
    "permitting_triggers": [{"code": "CEQA", "reason": "California project: CEQA review by the lead agency ..."},
                            {"code": "TRIBAL_CONSENT", "reason": "Crosses 0.61 km of tribal land (Mesa Grande Reservation) ..."},
                            {"code": "HFTD", "reason": "13.62 km in CPUC High Fire-Threat District ..."}]
  },
  "features": [
    {"type": "Feature", "properties": {"kind": "segment", "index": 0, "mode": "overhead", "length_km": 13.62},
     "geometry": {"type": "LineString", "coordinates": [[-116.7531, 33.088], [-116.7598, 33.0862]]}}
  ]
}
```

**Parcels:** by default `row.private_parcels` is estimated from USFS building density. Parcel area is taken as about 1/density, clamped to the range 1–160 acres, and a line crosses about 1/√area parcels per km. For real counts, download the SanGIS parcel layer under its end-user agreement, save it as `data/raw/parcels.gpkg|.shp|.geojson`, and re-run `prepare_data.py`. The pipeline de-duplicates SanGIS's stacked parcels, and `private_parcels_method` switches to `"real parcel polygons"`.

## Cost model

All weights are named constants in [config.py](config.py). The backend rebuilds the cost grids at startup, so after changing a weight just restart the server; there's no need to re-run data prep. Each per-cell value is $ per metre of line:

```
overhead    = $2M/mi    × (1 + 0.15·slope/10°)                × fire_OH  × corridor_OH × land
underground = $10M/mi   × (1 + 0.6·(slope/10°)²) [×4 if >25°] × fire_UG  × corridor_UG × land

fire_OH     = (1 + 3·risk) × tier_OH[level]      risk = continuous WHP hazard 0–1
fire_UG     = 1 + 0.1·risk
combined    = min(overhead, underground)   -> mode = whichever won
routing     = combined × 1000 on protected land (near-impassable) × 10 on tribal land × 1e4 outside bbox
```

| Factor | Overhead | Underground |
|---|---|---|
| Wildfire hazard, risk 0 → 1 | ×1 → **×4** | ×1 → ×1.1 |
| HFTD/FHSZ tier 0–1 / 2 / 3 (compliance: covered conductor, inspections, PSPS) | ×1.0 / 1.4 / **2.0** | — |
| Along existing power line | ×0.6 | ×0.9 |
| Along major road | ×0.85 | ×0.6 (trench in road ROW) |
| GAP 3 public land | ×1.25 | ×1.25 |
| Tribal land (routing penalty only) | ×10 | ×10 |
| Protected land inside an existing power-line easement (routing penalty) | ×3 instead of ×1000 | same |
| OH↔UG transition (post-hoc) | $750k each | |

Routing uses `skimage.graph.route_through_array` (8-connected, geometric distances) on the combined grid. A string-pulling pass then replaces grid zig-zags with straight spans wherever a span costs no more than 1% above the path it replaces. The raw path's per-cell modes are smoothed: any overhead or underground run shorter than 450 m is merged into its neighbours. The path is then split into contiguous runs, and each run is simplified (20 m tolerance) and costed with its chosen mode. Transition structures are added on top of that cost.

## Assumptions (read before quoting numbers)

- **All dollar figures are order-of-magnitude placeholders** for a 69–138 kV single-circuit gen-tie. They are not engineering estimates. The fire multiplier on overhead lines stands in for hardening (covered conductor, fast-trip), PSPS exposure, vegetation management and ignition liability.
- The baseline's dollar cost **excludes** the protected-land routing penalty, because that penalty is a routing deterrent, not a construction cost. The route analysis flags baselines that cross protected land as "likely unpermittable" instead.
- "Protected" (near-impassable) means PAD-US GAP 1–2 plus state parks and wilderness, matched by designation type. That catches Cuyamaca Rancho SP, which PAD-US lists as GAP 4. National Forest and BLM land (GAP 3) carry only a mild permitting multiplier.
- Existing corridors are assumed shareable within 60 m of a power line and 30 m of a major road.

## Known limitations

- **Overhead vs underground is decided mostly by the underground cost assumption.** With continuous hazard, routes thread overhead through low-hazard grass and farm valleys. That cuts overhead line in high hazard by about 95% against the straight line, at the cost of routes about 1.3× longer. It only rarely buries the line. A sweep over 80 random routes found that at $10M/mi underground almost no route uses underground. At **$5M/mi**, 84% of routes include some underground, averaging 33% of their length. Change `UNDERGROUND_COST_PER_MILE` in `config.py` to explore this.
- Wildfire Hazard Potential reflects 2020 fuels (LANDFIRE 2.2.0), so recent burn scars may score higher than their current fuel load warrants. The FHSZ SRA layer dates from 2007, and federal land gets its tier from HFTD only.
- **Tribal land** (Barona, Santa Ysabel, Mesa Grande, Inaja and Cosmit, Capitan Grande) gets a ×10 routing penalty, so it is strongly avoided but not forbidden. It is **not** added to the dollar estimate. Any crossing is listed in `tribal_lands_crossed`, and the route analysis warns that it triggers tribal consultation (NHPA §106) and needs tribal consent for right-of-way (25 CFR 169). Allotted and fee land outside reservation boundaries isn't in PAD-US, so it isn't flagged.
- The optimizer prices each cell independently. Transition costs, minimum span lengths, angle structures and tower placement are applied **after** routing, so they don't influence the path itself.
- Parcel count is reported but **not priced**, so the router can favour low-hazard valleys that are also the most subdivided (the demo route crosses ~108 estimated parcels). Also not modelled: habitat and waters, visual impact, geotech, existing-line capacity, access roads, line losses, the interconnection queue.
- The 30 m grid with 8-connectivity gives stair-stepped paths. Lengths come from the simplified geometry, which is about 2–8% shorter than the raw grid path.
- Existing lines come from the CEC transmission layer (69 kV and up) plus OSM. Distribution lines (12 kV) are mostly missing, and substation capacity isn't modelled: the router will happily connect to a small 69 kV substation that might need upgrades to accept a project.
- Cells outside the lon/lat bbox (the UTM grid is slightly larger) are near-impassable, so routes stay inside the box.
- The Cesium ion token in `config.js` is visible to anyone who loads the page. Restrict its allowed URLs in your Cesium ion dashboard before deploying publicly.

Data: USGS 3DEP and PAD-US (public domain), CPUC HFTD, CAL FIRE FHSZ, © OpenStreetMap contributors (ODbL), Cesium World Terrain/Imagery via Cesium ion.
