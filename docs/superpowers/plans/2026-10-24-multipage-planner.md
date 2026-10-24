# Multi-page Route Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single-page Gen-tie Router into a four-page planner (landing → dashboard → project → route) with backend-persisted projects, strategy-preset alternative routes, a dark/light design system and no lost functionality.

**Architecture:** FastAPI gains preset-aware routing (search on a preset grid, price on the balanced grid) and a SQLite project store with seed data. The frontend stays vanilla ES modules with no build step. `app.js` is split into a reusable Cesium `map.js` plus `panel.js` (results rendering). Four pages share `styles/tokens.css` and `styles/components.css`, `theme.js`, `api.js` and `estimates.js`.

**Tech Stack:** Python 3.13, FastAPI, stdlib sqlite3, numpy. CesiumJS 1.123.0 (jsDelivr). Vanilla JS ES modules. Google Fonts (Instrument Serif, Geist, Geist Mono). Headless Chrome for smoke tests. Node 20 for pure-JS self-checks.

**Spec:** `docs/superpowers/specs/2026-10-24-multipage-planner-design.md`. Read it fully before any task. The approved mockup is `../.superpowers/brainstorm/83971-1792871920/content/screens-v1.html`: **variant 1** of each screen, direction A = dark and direction B = light.

## Global Constraints

- Branch `feature/multipage-planner`. Commit after each task. Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- `python -m backend.test_route` and `python -m backend.test_api` must still print ALL CHECKS PASSED after every backend task.
- `POST /route` without `preset` must return byte-identical results to today (`preset` defaults to `"balanced"`).
- No build step and no new Python or npm dependencies. Cesium is pinned at `1.123.0`. Keep `config.js` / `config.example.js` handling as it is.
- Every map is the existing Cesium viewer. The only non-Cesium route drawings allowed are the landing hero illustration and the dashboard card thumbnails.
- Every placeholder value from `estimates.js` renders with an `est.` marker (class `est`).
- Tokens are copied verbatim from the spec. Dark: `--bg #02030a`, `--surf #0a0e1c`, `--line #dee4f521`, `--text #edeff6`, `--muted #9aa0b6`, `--acc #8fb4ff`, `--acc2 #ff8a5b`, `--ok #7ef2c4`. Light: `--bg #f3f1ea`, `--surf #fbfaf6`, `--text #121512`, `--muted #6b7068`, `--acc #0f9d6b`, `--acc2 #e0682b`.
- Theme is `data-theme="dark|light"` on `<html>`, stored in localStorage key `gentie.theme`, defaults to `prefers-color-scheme`, and is set by an inline `<head>` script so the page doesn't flash.
- localStorage keys stay as they are: `gentie.view`, `gentie.exaggeration`. Every storage access is wrapped in try/catch.
- Phases: `drafting | comparing | permit-risk | ready`. Presets: `balanced | avoid_fire | cheapest | underground`.
- Run the server for manual checks with `python -m uvicorn backend.app:app --port 8000`.

## Review Focus

1. **Backend down or slow when a page loads.** Dashboard, project and route pages show an inline error state with a retry button, not a blank page. `route.html?demo=1` works with the backend stopped. *Test: Task 10 / Task 11 smoke runs `route.html?demo=1` with `--no-server`.*
2. **Adding a preset route whose endpoints are outside the region, or the same cell.** The API returns 400 with a detail message, stores nothing, and the UI shows the existing toast. *Test: Task 2 `test_projects` case `bad_endpoints`.*
3. **Deleting a project while its route page is open, or opening `route.html?id=999`.** You get a "not found" state with a link back to the dashboard. *Test: Task 2 asserts 404. Task 11 smoke loads `route.html?id=999999`.*
4. **Theme toggled while the Cesium map is showing routes.** Route colours, HUDs and the flowing line all recolour without a reload. *Test: Task 5 smoke toggles the theme and asserts `map.routeColor()` changed.*
5. **Flowing-line animation and fps.** With the animation on, the Enhanced view must not fall below about 80 % of its current fps. It pauses when the tab is hidden, and reduced motion disables it. *Test: Task 5 step that measures fps on/off and records the numbers in the commit message.*

---

## File map

```
config.py                        + PRESETS
backend/cost_model.py            build_cost_grids(..., overrides=None)
backend/router.py                Router.route(start, end, preset="balanced"); lazy preset grids/trees
backend/worker.py                route_json(start, end, preset="balanced")
backend/app.py                   RouteRequest.preset, cache key, /assumptions presets, project routes
backend/store.py        (new)    SQLite project/route store
backend/seed.py         (new)    sample projects
backend/test_projects.py (new)   store + endpoint checks
tools/smoke_pages.py    (new)    headless-Chrome page smoke test
frontend/
  index.html                     landing (Task 8; until then the legacy tool)
  tool.html             (tmp)    legacy tool on the new modules (Task 4), deleted in Task 11
  dashboard.html project.html route.html
  styles/tokens.css components.css STYLES.md preview.html
  js/theme.js api.js estimates.js estimates.test.mjs map.js panel.js
  js/pages/landing.js dashboard.js project.js route.js
  app.js style.css               deleted in Task 11
```

**Page boot contract (all pages, used by the smoke test):** each page sets `document.documentElement.dataset.ready = "1"` once boot finishes, and records uncaught errors with `window.addEventListener("error", e => (document.documentElement.dataset.errors = (document.documentElement.dataset.errors || "") + e.message + "|"))`. That listener goes in an inline `<head>` script, next to the theme script.

---

### Task 1: Strategy presets in the router

**Files:**
- Modify: `config.py` (add `PRESETS` after the cost constants)
- Modify: `backend/cost_model.py` (`build_cost_grids`)
- Modify: `backend/router.py` (`Router.__init__`, `_substation_trees`, `route`, `least_cost_path`)
- Modify: `backend/worker.py` (`route_json`)
- Modify: `backend/app.py` (`RouteRequest`, `_compute`, `/route` cache key, `/assumptions`)
- Test: `backend/test_route.py`, `backend/test_api.py`

**Interfaces:**
- Produces: `config.PRESETS: dict[str, dict[str, object]]`, mapping preset name → `{"label": str, "blurb": str, "overrides": {CONFIG_NAME: value}}`. `balanced` has empty overrides.
- Produces: `build_cost_grids(corridor_override=None, overrides: dict | None = None) -> CostGrids`. Every config constant used inside it is read through `_c(name) = overrides.get(name, getattr(C, name))`.
- Produces: `Router.route(start, end, preset: str = "balanced") -> dict`. It raises `RouteError` for an unknown preset. The output `properties` gain `"preset": <name>`.
- Produces: `worker.route_json(start, end, preset="balanced") -> (status, bytes)`.
- Produces: `POST /route` body `{start, end, preset?}`. `GET /assumptions` gains `"PRESETS": {name: {label, blurb}}`.

- [ ] **Step 1: Add failing checks to `backend/test_route.py`** (same plain-assert, script style as the existing checks). Use the existing Ramona-area start and `CREELMAN`:

```python
base = r.route(S, CREELMAN)
assert base == r.route(S, CREELMAN, preset="balanced")
assert base["properties"]["preset"] == "balanced"
alts = {p: r.route(S, CREELMAN, preset=p) for p in C.PRESETS}
geoms = {p: json.dumps([f["geometry"] for f in fc["features"] if f["properties"].get("kind") == "segment"]) for p, fc in alts.items()}
assert len(set(geoms.values())) >= 3, "presets must give visibly different routes"
# pricing is always on the balanced grid: re-pricing the balanced route gives its own cost
assert abs(alts["balanced"]["properties"]["total_cost_usd"] - base["properties"]["total_cost_usd"]) <= 1
try: r.route(S, CREELMAN, preset="nope"); raise AssertionError("unknown preset accepted")
except RouteError: pass
```

- [ ] **Step 2: Run** `python -m backend.test_route`. Expected: it fails with `TypeError` (no `preset` kwarg).
- [ ] **Step 3: Implement.** Add `PRESETS` in `config.py`:
  - `avoid_fire`: raise `OH_FIRE_RISK_MAX_MULT` and `OH_FIRE_TIER_MULT[3]`.
  - `cheapest`: lower `MANAGED_LAND_MULT`, shrink the fire multipliers toward 1. Keep `PROTECTED_PENALTY` and `TRIBAL_PENALTY` as they are.
  - `underground`: set `UNDERGROUND_COST_PER_MILE` to about 4M.

  Tune the values until Step 1 passes. `Router` keeps `self.g` (balanced, used for pricing and stats) and a lazy `self._search = {"balanced": self.g}` with per-preset trees built on first use. `route()` searches and takes `mode` from the preset grid, but every cost, stat and inventory field stays priced from `self.g` (`self.g.overhead/underground` indexed by the preset-chosen modes). Thread `preset` through `worker.route_json`. In `app.py`, add `preset: str = "balanced"` to `RouteRequest`, append it to the cache key, and add `PRESETS` (label and blurb only) to `/assumptions`.
- [ ] **Step 4: Add to `backend/test_api.py`:** `POST /route` with `preset="avoid_fire"` is 200 with `properties.preset == "avoid_fire"`. The same request with `preset` omitted gives the same body as before (compare against the in-process `Router`, as the existing check does). `preset="nope"` → 400.
- [ ] **Step 5: Run** `python -m backend.test_route && python -m backend.test_api`. Expected: both print ALL CHECKS PASSED.
- [ ] **Step 6: Commit** with the message `Add strategy presets to /route (search per preset, priced on balanced grid)`.

---

### Task 2: Project store, API and seed

**Files:**
- Create: `backend/store.py`, `backend/seed.py`, `backend/test_projects.py`
- Modify: `backend/app.py` (lifespan opens the store and seeds; new endpoints before the static mount), `config.py` (`PROJECTS_DB = DATA_DIR / "projects.db"`), `.gitignore` (`data/projects.db`)

**Interfaces:**
- Consumes: `worker.route_json(start, end, preset)` and the app's existing `_compute` + `RouteCache` (route creation goes through the cache).
- Produces: `store.Store(path)` with these methods:
  - `list_projects() -> list[dict]`
  - `get_project(id) -> dict | None`
  - `create_project(name, voltage_kv, phase, filing_target=None) -> dict`
  - `update_project(id, **fields) -> dict | None`
  - `delete_project(id) -> bool`
  - `add_route(project_id, name, preset, result: dict) -> dict`
  - `get_route(id) -> dict | None`
  - `update_route(id, name) -> dict | None`
  - `delete_route(id) -> bool`
  - `replace_route_result(id, result) -> None`

  Projects carry `start_lonlat`, `end_lonlat` (JSON lists or null) and `filing_target` (ISO date or null). The schema is in the spec ("Calls made by the designer" §3). `ON DELETE CASCADE` applies to routes.
- Produces these HTTP routes (JSON). A project summary is `{id, name, voltage_kv, phase, filing_target, start_lonlat, end_lonlat, created_at, updated_at, routes: [route_summary]}`, where `route_summary = {id, name, preset, total_km, total_cost_usd, tower_count, n_triggers, thumb: [[lon,lat],...] (≤ 40 points, all segments concatenated)}`.
  - `GET /projects` → `[project_summary]`
  - `POST /projects {name, voltage_kv, phase, filing_target?}` → 201 project_summary
  - `GET /projects/{id}` → project_summary
  - `PATCH /projects/{id} {name?, phase?, filing_target?, voltage_kv?, start_lonlat?, end_lonlat?}` → project_summary. Changing either endpoint recomputes every route of the project with its own preset.
  - `DELETE /projects/{id}` → 204
  - `POST /projects/{id}/routes {preset, start_lonlat?, end_lonlat?}` → 201 `{route_summary, result}`. Endpoints given in the body are saved on the project first (the first-route flow). Otherwise the project's endpoints are used, and it's 400 if the project has none. The route name defaults to `PRESETS[preset]["label"]`.
  - `GET /routes/{id}` → `{id, project_id, name, preset, created_at, result}`, where result is the full `/route` FeatureCollection
  - `PATCH /routes/{id} {name}` → the route without the result
  - `DELETE /routes/{id}` → 204
  - Unknown ids → 404. Invalid phase or preset → 422. A route error → 400 with the router's detail, and nothing stored.
- Produces: `seed.seed_if_empty(store, compute)`. It is skipped when the env var `GENTIE_NO_SEED=1` is set, and creates four projects:
  - Ramona Solar Gen-tie, 230 kV, `comparing`, 3 presets
  - Santa Ysabel Reinforcement, 138 kV, `permit-risk`, 2 presets
  - Julian Wind Interconnect, 230 kV, `drafting`, 3 presets
  - Boulder Creek BESS Tie, 69 kV, `ready`, 3 presets

  Each ends at a real substation from `overlays/substations.geojson`. Starts are inside the bbox, and filing targets are staggered from today (for example +49, +102, none, +27 days).

- [ ] **Step 1: Write `backend/test_projects.py`.** It uses the same `start_server` pattern as `test_api` (copy the helper; port 8766; env `ROUTE_WORKERS=2`, `GENTIE_NO_SEED=1`, and `PROJECTS_DB` pointed at a temp file through a new env override `GENTIE_DB`). Checks:
  - create → list contains it
  - `POST /projects/{id}/routes` with endpoints → 201, and the summary has `total_km > 0` and `len(thumb) <= 40`
  - a second preset → the project has 2 routes
  - `GET /routes/{rid}` has `result.type == "FeatureCollection"`
  - `PATCH` the name → reflected
  - `PATCH end_lonlat` → the route's `total_km` changes
  - case `bad_endpoints`: start `[-118, 33]` → 400, and the route count is unchanged
  - `phase="bogus"` → 422
  - `GET /projects/999999` → 404
  - `DELETE` project → its routes give 404
  - restart the server on the same DB → the data persists
  - a fresh DB without `GENTIE_NO_SEED` → 4 projects and 11 routes
- [ ] **Step 2: Run** `python -m backend.test_projects`. Expected: FAIL (404 on `/projects`).
- [ ] **Step 3: Implement** `store.py` (one connection per call, `check_same_thread=False` not needed; WAL mode), the endpoints in `app.py`, and `seed.py` (called in lifespan after the pool warms; `compute` = the app's cached route function).
- [ ] **Step 4: Run** `python -m backend.test_projects && python -m backend.test_api`. Expected: ALL CHECKS PASSED for both.
- [ ] **Step 5: Commit** with the message `Add SQLite project store, project/route API and seed projects`.

---

### Task 3: Design system, theme and STYLES.md

**Files:**
- Create: `frontend/styles/tokens.css`, `frontend/styles/components.css`, `frontend/styles/STYLES.md`, `frontend/styles/preview.html`, `frontend/js/theme.js`, `tools/smoke_pages.py`

**Interfaces:**
- Produces CSS custom properties. These are the tokens from Global Constraints plus:
  - `--bg2`, `--line2`, `--mapbg`, `--glow`
  - `--route-1..--route-4`: route colours for the selected and other routes; `--route-1` = `--acc`
  - `--font-display: "Instrument Serif"`, `--font-body: Geist`, `--font-mono: "Geist Mono"`
  - spacing `--s-1..--s-8` (4 px base), radii `--r-sm 10px / --r-md 16px / --r-lg 18px / --r-pill 99px`
- Produces component classes, mirroring the mockup's names where they exist: `.nav-top`, `.logo`, `.btn`, `.btn--fill`, `.pill` (+`--ok/--warn/--blue`), `.mono-label`, `.display` (+`.it` for the italic accent), `.grid-bg`, `.kpis`, `.pcard`, `.rcard` (+`.is-selected`), `.hud`, `.bento` / `.cell`, `.inv`, `.gantt`, `.donut`, `.permit`, `.tabs`, `.modal`, `.toast`, `.est`, `.empty-state`, `.theme-toggle`, `.side`.
- Produces from `js/theme.js` (ES module): `initThemeToggle(button: HTMLElement): void`, `currentTheme(): "dark"|"light"`, `onThemeChange(cb: (theme) => void): void`, `cssVar(name: string): string`. Every theme change (from the toggle, or from code that sets `data-theme` and then dispatches it) fires `window` event `gentie:theme`. `onThemeChange` listens to that event. Also an inline snippet, documented in STYLES.md, that every page pastes into `<head>` for no-flash theming plus the boot/error contract.
- Produces: `tools/smoke_pages.py URL [URL ...] [--no-server]`. By default it starts uvicorn on :8767 with `ROUTE_WORKERS=0` and a temp DB. For each URL it runs Chrome headless (`--headless=new --use-angle=swiftshader --enable-unsafe-swiftshader --virtual-time-budget=15000 --dump-dom`), parses `<html>` for `data-ready="1"` and an empty `data-errors`, prints `OK url` or `FAIL url: reason`, and exits non-zero on any failure. `--no-server` serves `frontend/` with a static-only `http.server` on :8768, so there is no API at all. That's how the offline and demo cases are proven. `--eval JS` (optional, repeatable) runs a check whose truthy result is written to `data-check` before the dump.

- [ ] **Step 1:** Write `styles/preview.html`. It's a component gallery that renders every component in both themes side by side and follows the boot contract.
- [ ] **Step 2: Run** `python tools/smoke_pages.py /styles/preview.html`. Expected: FAIL (the script or CSS is missing). Then implement the script and see it fail for the right reason (`data-ready` missing).
- [ ] **Step 3:** Implement `tokens.css`, `components.css` and `theme.js`, porting values and shapes from the mockup's `<style>`, so `preview.html` passes. Contrast: body text ≥ 4.5:1 and `--muted` ≥ 4.5:1 on `--bg` in both themes (check with a quick calculation and note the ratios in STYLES.md). `:focus-visible` gets a 2 px `--acc` outline. Under `@media (prefers-reduced-motion: reduce)`, all animations are off.
- [ ] **Step 4:** Write `STYLES.md` covering every bullet in spec § "STYLES.md must cover", with copy-paste HTML for each component, the page skeleton (head snippet, nav, boot contract) and "How to add a page".
- [ ] **Step 5: Run** `python tools/smoke_pages.py /styles/preview.html`. Expected: `OK`. Screenshot both themes (Chrome `--screenshot`) and compare them visually to the mockup.
- [ ] **Step 6: Commit** with the message `Add design tokens, components, theme toggle, STYLES.md and page smoke test`.

---

### Task 4: Split app.js into map.js + panel.js (behaviour-identical)

**Files:**
- Create: `frontend/js/map.js`, `frontend/js/panel.js`, `frontend/tool.html`, `frontend/js/pages/tool.js`
- Keep untouched for now: `frontend/index.html`, `frontend/app.js` and `style.css` keep working until Task 11.

**Interfaces:**
- Produces `createMap(container: HTMLElement, opts?: {onPick?(kind, lonlat), onRouteClick?(routeId)}) => Promise<MapApi>`. `MapApi` has these members:
  - `viewer`, `ready: Promise<void>`
  - `bounds(): {bbox, synthetic_layers, region_name, ...}` (cached `/bounds`; falls back to `cfg.FALLBACK_BBOX` offline)
  - `showRoute(fc, {id = "main", selected = true, frame = true} = {})`
  - `removeRoute(id)`, `clearRoutes()`, `selectRoute(id)`
  - `setPickMode(on: boolean)`: the click → site → substation flow with snapping and the out-of-region toast; calls `opts.onPick("start"|"end", lonlat)`
  - `placeMarker(kind, lonlat, label)`, `frameRoute(id?)`, `homeView(duration?)`
  - `setView("top"|"enhanced")`, `getView()`, `setExaggeration(x)`, `getExaggeration()` (both persisted under the existing keys)
  - `toggleOverlay(name, on)` with names `fire | hftd | protected | tribal | lines | baseline | contours | cities | borders`
  - `startFly(id?)`, `stopFly()`, `onFlyProgress(cb(towerIndex, towerCount, km))`
  - `showValidation(): Promise<{label, rows, note}>`
  - `toast(msg, kind?, ms?)`
- Produces from `panel.js`: `renderResults(root: HTMLElement, props, features, {isDemo}): void` (everything `renderPanel`, `renderDemoStory` and `renderProfile` render today, writing into elements found by `data-r="…"` attributes under `root`, not by global ids), plus `fmtUsd(x)` and `fmtKm(x)`.
- `tool.html` is a copy of today's `index.html` markup, wired through `pages/tool.js` to `createMap` + `renderResults`. It is the refactor's proof of equivalence.

- [ ] **Step 1:** Write the feature checklist from spec § "Feature-preservation checklist" into the PR notes. Then run the current app (`index.html`) and screenshot Top and Enhanced with the demo route loaded. Those screenshots are the baseline.
- [ ] **Step 2:** Move the code. The logic stays the same; module-level state goes into the `createMap` closure; the hardcoded `#cesiumContainer` becomes `container`. Multiple routes are keyed by `id`, each with its own `routeTop` / `routeEnh` / `baseline` / hardware layers. Only the selected route builds 3D hardware (this keeps fps where it is today).
- [ ] **Step 3: Run** `python tools/smoke_pages.py /tool.html --eval "document.querySelector('#btnDemo').click(), true"`. Expected: OK. Then go through every checklist item by hand in `tool.html` at :8000 against the baseline screenshots, and tick each one in the PR notes.
- [ ] **Step 4: Commit** with the message `Refactor Cesium app into reusable map.js and panel.js modules`.

---

### Task 5: Flowing line, tower dots, multi-route colours, theme-aware map

**Files:**
- Modify: `frontend/js/map.js`

**Interfaces:**
- Consumes: `theme.js` `onThemeChange` and `cssVar`.
- Produces: `MapApi.routeColor(id): string` (current CSS colour) and `MapApi.setFlow(on: boolean)`.
- The selected route gets a ground polyline with an animated dash (a time-driven `dashPattern` offset or a custom `Material` with a `time` uniform) in `--text` over the `--acc` trace, plus small `--acc` dots at the tower positions. In Enhanced view the flow follows the tower-to-tower trace under the 3D conductors. Non-selected routes use `--route-2..4` at lower alpha, with no flow, dots or hardware. All colours are re-read on theme change.
- Animation sets `scene.requestRenderMode = false` only while flow is on, the page is visible and reduced motion is off, and it is throttled to about 30 fps. Otherwise it falls back to render-on-demand.

- [ ] **Step 1: Write the smoke check.** `python tools/smoke_pages.py /tool.html --eval "(async()=>{document.querySelector('#btnDemo').click(); await new Promise(r=>setTimeout(r,3000)); const a=window.__map.routeColor('main'); document.documentElement.dataset.theme='light'; window.dispatchEvent(new Event('gentie:theme')); await new Promise(r=>setTimeout(r,300)); return a!==window.__map.routeColor('main')})()"`. `tool.js` exposes `window.__map`. Expected: FAIL (`routeColor` is undefined).
- [ ] **Step 2: Implement** the flow, the dots, multi-route styling and theme reactivity.
- [ ] **Step 3: Run** the smoke check again. Expected: OK.
- [ ] **Step 4: Measure fps.** Count `scene.postRender` events over 10 s at a fixed camera on the demo route in Enhanced view, with flow on and off. Use a normal Chrome window, not headless. Flow-on must be ≥ 80 % of flow-off. Record both numbers in the commit message.
- [ ] **Step 5: Commit** with the message `Add animated flow line, tower dots and theme-aware multi-route styling (fps on/off: X/Y)`.

---

### Task 6: api.js and estimates.js

**Files:**
- Create: `frontend/js/api.js`, `frontend/js/estimates.js`, `frontend/js/estimates.test.mjs`

**Interfaces:**
- Produces from `api.js`:
  - `apiGet(path)`, `apiSend(method, path, body)`. Both throw `ApiError {status, detail}`. The base is `window.APP_CONFIG.API_BASE || ""`.
  - `listProjects()`, `getProject(id)`, `createProject(body)`, `updateProject(id, body)`, `deleteProject(id)`
  - `addRoute(projectId, {preset, start_lonlat?, end_lonlat?})`, `getRoute(id)`, `renameRoute(id, name)`, `deleteRoute(id)`
  - `getAssumptions()` (cached), `getStats()`
- Produces from `estimates.js`: `estimate(props, project?) -> Estimate`, using the exact shape in spec § Estimates plus:
  - `upfront_total` (`= props.total_cost_usd`)
  - `permits: [{code, reason, months_min, months_max}]`
  - `labels: {cheapest_build: bool, fastest: bool}`, which stay false until `rankRoutes` sets them
  - `rankRoutes(estimates[]) -> estimates[]`, which sets `labels`, `recommended` (highest score) and `labels.cheapest_build` (lowest upfront)

  If `props.estimates` exists, it is returned with `is_placeholder: false`. Placeholder rates are module constants in one `RATES` object at the top. Voltage scales maintenance and materials (69 kV ×0.8, 138 kV ×1.0, 230 kV ×1.35). The calendar starts at `project.filing_target` or today. Permit durations come from a `PERMIT_MONTHS` table keyed by trigger `code` (unknown code → 3–6 months). `score` is 0–100.

- [ ] **Step 1: Write `estimates.test.mjs`** (plain `node:assert`). Using `frontend/demo_route.json` properties, check:
  - `estimate(p).is_placeholder === true`
  - `upfront_total === p.total_cost_usd`
  - the upfront breakdown values sum to `upfront_total` within $1
  - `lifetime_cost_40yr > upfront_total`
  - the calendar phases are ordered and `energize_date` = the last end
  - a 230 kV project gives a higher `maintenance_per_year` than a 69 kV one
  - `estimate({...p, estimates: X}) === X`, with `is_placeholder` false
  - `rankRoutes` on two copies where one has a +50 % cost → the cheaper one gets `cheapest_build`
  - `score` is in [0, 100]
- [ ] **Step 2: Run** `node frontend/js/estimates.test.mjs`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement** both modules.
- [ ] **Step 4: Run** it again. Expected: it prints `estimates: all checks passed`.
- [ ] **Step 5: Commit** with the message `Add API client and placeholder estimates module`.

---

### Task 7: Landing page

**Files:**
- Create: `frontend/landing.html` (renamed to `index.html` in Task 11), `frontend/js/pages/landing.js`

Build mockup "Landing · variant 1" with the components from Task 3: nav with the theme toggle, the hero with its animated SVG route illustration, the stats row, and the three steps. CTAs and Sign in go to `dashboard.html`. Stats: the route-solve figure comes from `getStats()` when available, otherwise `~9 ms`; the layer count comes from `/bounds` sources, otherwise `12`. Both fall back silently.

- [ ] **Step 1: Run** `python tools/smoke_pages.py /landing.html` → FAIL. Then `python tools/smoke_pages.py --no-server /landing.html` (served from a file / static-only): it must still pass offline. Implement until both are OK.
- [ ] **Step 2:** Screenshot both themes and compare with the mockup.
- [ ] **Step 3: Commit** with the message `Add landing page`.

---

### Task 8: User dashboard

**Files:**
- Create: `frontend/dashboard.html`, `frontend/js/pages/dashboard.js`

**Interfaces:**
- Consumes: `listProjects`, `createProject`, `estimate`, `rankRoutes`, `initThemeToggle`.

Build mockup "User dashboard · variant 1":
- Sidebar (Projects is active; the other nav items are static for now) with the theme toggle.
- Greeting header and "+ New project".
- KPIs: active projects; routes in study; portfolio capex = the sum over projects of the minimum `total_cost_usd`; next filing = the soonest future `filing_target`, or "—".
- A card per project:
  - Thumbnail: inline SVG of each route's `thumb`, projected linearly into the card box, selected-style for the recommended route with a CSS flowing dash.
  - Phase pill, route count, name, capex / voltage / length.
  - Progress bar from phase (15 / 50 / 35 / 90 %).
- A "Start a project" card. It and "+ New project" open `.modal` with name (required), voltage (69 / 138 / 230), phase (default drafting) and filing target (optional). Submitting goes to `project.html?id=…`.
- Error state with a retry button if `listProjects` fails. Empty state when there are no projects.

- [ ] **Step 1: Run** `python tools/smoke_pages.py /dashboard.html --eval "document.querySelectorAll('.pcard[data-id]').length===4"` (the smoke server seeds) → FAIL. Implement until OK.
- [ ] **Step 2:** Create a project through the modal by hand, then check that it appears and that the redirect works.
- [ ] **Step 3: Commit** with the message `Add user dashboard`.

---

### Task 9: Project dashboard

**Files:**
- Create: `frontend/project.html`, `frontend/js/pages/project.js`

**Interfaces:**
- Consumes: `createMap` (Task 4/5), `getProject`, `addRoute`, `getRoute`, `renameRoute`, `deleteRoute`, `updateProject`, `deleteProject`, `getAssumptions` (preset labels), `estimate`, `rankRoutes`.

Build mockup "Project · variant 1":
- Breadcrumb nav.
- A Cesium map on the left (`createMap`), with a badge overlay top left ("A → B · N kV / N candidate routes") and a legend bottom left (one entry per route in its `routeColor`, plus fire tier and protected).
- A collapsible map toolbar with all nine overlay toggles, Top / Enhanced, terrain height and "Compare to real line". The validation result shows in a toolbar popover.
- The right rail has one `.rcard` per route:
  - Label pill: Recommended / Cheapest build / Fastest, from `rankRoutes`.
  - Score, name, and six metrics. Upfront, length and towers (or vaults when `underground_km > 0`) and permits are real. Upkeep and energize are `est.`.
  - The top risk line.
  - Open → `route.html?id=…`. Card actions: rename and delete.
- Card click or route-line click → `selectRoute`.
- **No endpoints yet:** the rail shows an empty state with step text ("Click the project site", then "a substation"), using `setPickMode(true)`. After the second pick, `addRoute(id, {preset: "balanced", start_lonlat, end_lonlat})`.
- "+ Add route" offers the presets not used yet (label + blurb) and calls `addRoute`. It's disabled when all are used.
- "Change endpoints": confirm, re-pick, then `updateProject(id, {start_lonlat, end_lonlat})`, then reload the routes.
- Project menu: rename, change phase, set filing target, delete (confirm → dashboard).
- Errors from the API show as `map.toast`. `?id=` missing or 404 → `.empty-state` with a link to the dashboard.

- [ ] **Step 1: Run** `python tools/smoke_pages.py "/project.html?id=1" --eval "document.querySelectorAll('.rcard').length===3"` and `"/project.html?id=999999" --eval "!!document.querySelector('.empty-state')"` → FAIL. Implement until OK.
- [ ] **Step 2:** By hand: create an empty project from the dashboard, pick the endpoints, add two presets, select between them, change the endpoints, delete a route, and toggle every overlay and the validation.
- [ ] **Step 3: Commit** with the message `Add project dashboard with multi-route map and route cards`.

---

### Task 10: Route dashboard

**Files:**
- Create: `frontend/route.html`, `frontend/js/pages/route.js`

**Interfaces:**
- Consumes: `createMap`, `renderResults` (panel.js), `getRoute`, `getProject`, `estimate`.

Build mockup "Route · variant 1":
- Breadcrumb nav with "Export packet" and "Mark for filing". Export = download the route JSON + estimate as a `.json` file. Mark = `updateProject(pid, {phase: "ready"})`.
- Header: name, voltage, length, upfront (real), 40-yr total (`est.`), energize (`est.`).
- Sticky `.tabs` that scroll to the sections.
- A 520 px Cesium hero in Enhanced view with the selected route only: 3D poles and the flowing line underneath them. HUDs:
  - top left: terrain and elevation range from `profile`
  - top right: layer toggles (towers, fire tier, protected, plus the rest of the nine in a "More" list) and the Top/Enhanced switch and terrain height
  - bottom centre: "▶ Fly along" and a counter from `onFlyProgress`
  - a segment callout on hover or click of a segment (mode, length, cost)
- Bento grid:
  1. Upfront donut + legend (`est.` breakdown of the real total).
  2. Inventory (real `inventory` fields + `est.` lead times; the top lead time becomes the risk line).
  3. Maintenance (`est.` sparkline + breakdown).
  4. Deployment calendar Gantt from `estimate().calendar`, starting at the project filing target.
  5. Permits & legal: real triggers with reasons and `est.` month ranges.
  6. "Route analysis": `renderResults` output, which covers every existing metric, the tribal note, ROW, vs-straight table and note, elevation profile, segment list, demo story and the cost-assumption note (built from `getAssumptions()`; hardcoded text only when offline).
- `?demo=1` loads `demo_route.json` with no API calls, and no project gives a 230 kV label "Demo".
- `?id=` missing or 404 → `.empty-state`.

- [ ] **Step 1: Run** `python tools/smoke_pages.py "/route.html?id=1" --eval "document.querySelectorAll('.cell').length>=6"`, then `python tools/smoke_pages.py --no-server "/route.html?demo=1"`, then `"/route.html?id=999999" --eval "!!document.querySelector('.empty-state')"` → FAIL. Implement until all are OK.
- [ ] **Step 2:** By hand in normal Chrome: poles appear zoomed in under 9 km, the flow runs under them, fly-along plus Esc works, contours and the hover readout work, the theme toggles live, and the segment callout works.
- [ ] **Step 3: Commit** with the message `Add route dashboard with 3D hero and bento breakdown`.

---

### Task 11: Cut over, remove legacy, docs

**Files:**
- Rename: `frontend/landing.html` → `frontend/index.html`, replacing the legacy tool.
- Delete: `frontend/tool.html`, `frontend/js/pages/tool.js`, `frontend/app.js`, `frontend/style.css`
- Modify: `README.md` (run instructions, pages, presets, projects API, `GENTIE_NO_SEED` / `GENTIE_DB`), `FEATURES.md` (new rows; mark the deployment calendar as 🔧 placeholder), `backend/app.py` docstring (new endpoints)

- [ ] **Step 1:** Go through the spec's feature-preservation checklist once more, mapping each item to its page and element (project page toolbar, route page, …). Fill any gap before deleting the legacy code.
- [ ] **Step 2: Run all checks:**

```
python -m backend.test_route && python -m backend.test_api && python -m backend.test_projects && node frontend/js/estimates.test.mjs && \
python tools/smoke_pages.py / /dashboard.html "/project.html?id=1" "/route.html?id=1" "/route.html?id=999999" /styles/preview.html && \
python tools/smoke_pages.py --no-server / "/route.html?demo=1"
```

Expected: every line ALL CHECKS PASSED or OK.
- [ ] **Step 3: Commit** with the message `Cut over to multi-page planner; remove legacy single-page app; update docs`.
