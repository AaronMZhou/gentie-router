# Multi-page route planner: design

Date: 2026-10-24 · Status: draft for approval

## Intent

Gen-tie Router is a single-page tool today: click a site and a substation, get one least-cost route on a Cesium 3D map with a results panel. The user is a **transmission project manager brainstorming routes before filing for federal route approval**. They juggle several projects, and each project has several candidate routes. They want data they'd normally only see after approval or deployment (maintenance, legal steps, logistics, upfront cost, deployment calendar, inventory) during planning, so they can pick the route that saves millions.

This work turns the tool into a four-page app with a modern, minimal look (reference: nobodle.com) and a futuristic, sustainable-energy feel:

**Landing → User dashboard (all projects) → Project dashboard (candidate routes + map) → Route dashboard (full breakdown + 3D map)**

Approved mockups (variant 1 of every page): `.superpowers/brainstorm/83971-1792871920/content/screens-v1.html` in the parent folder `HackOhio2026-Transmission/`. Open it in a browser; the top bar switches screens and themes. Direction A (Night Grid) = dark mode and direction B (Solar Paper) = light mode.

## Goals

1. Four pages matching variant 1 of the mockups, in both themes.
2. **No lost functionality.** Everything the current `frontend/` does still works somewhere in the new app (checklist below).
3. Projects and routes persist on the backend (SQLite).
4. A project can hold several candidate routes between the same endpoints, made with **strategy presets**.
5. Dark/light mode toggle on every page.
6. A reusable style system plus a `STYLES.md` guide that future features follow.

## Non-goals

- Real authentication. "Sign in" / "Open app" just goes to the dashboard. There is one implicit user.
- Real maintenance, calendar, lead-time or score models. These are **placeholders** for now (see Estimates).
- A framework migration. It stays vanilla JS with no build step, served by FastAPI with one command.
- Mobile-first design. Pages should not break at phone width, but the target is desktop.

## Anti-goals (failure even if it "works")

- Any current feature gone or broken (overlays, validation, demo mode, view modes, fly-along and so on).
- Replacing the Cesium map with a fake SVG map. The mockup's SVG maps are only illustrations. **Every map in the app is the existing Cesium viewer.**
- Placeholder numbers presented as real. Every placeholder value carries a visible "est." marker or tooltip.
- Fps regressions that make the 3D view stutter again (the README documents the work that got it from 12 to 63 fps).

## Constraints

- Backend: FastAPI + process pool (`backend/app.py`, `backend/worker.py`). Keep `POST /route` backward compatible, and keep `python -m backend.test_route` and `python -m backend.test_api` passing.
- Frontend: plain HTML/CSS/JS, Cesium 1.123.0 from jsDelivr, `config.js` token handling unchanged.
- Fonts: Instrument Serif (display, italic accents), Geist (body), Geist Mono (uppercase tracked labels), all from Google Fonts.
- Tokens (from the mockup):
  - Dark: `--bg #02030a`, `--surf #0a0e1c`, `--line #dee4f521`, `--text #edeff6`, `--muted #9aa0b6`, `--acc #8fb4ff`, `--acc2 #ff8a5b`, `--ok #7ef2c4`
  - Light: `--bg #f3f1ea`, `--surf #fbfaf6`, `--text #121512`, `--muted #6b7068`, `--acc #0f9d6b`, `--acc2 #e0682b`
- The user's working tree on `main` has uncommitted changes. They must be committed before work starts, and the work happens on a feature branch.

## Decisions the user made

| Topic | Decision |
|---|---|
| Layouts | Variant 1 of all four pages |
| Persistence | Backend SQLite. No real login |
| New numbers (maintenance, 40-yr cost, calendar, lead times, score) | Frontend placeholders, structured so real data slots in easily |
| Framework | Keep vanilla JS |
| Alternative routes | Strategy presets on the same endpoints |
| Maps | Keep the current Cesium map everywhere. Add the mockup's **animated flowing line with dots** on top of the route |
| Route page 3D | Keep the existing 3D poles, crossarms and sagging conductors. The animated flowing line runs on the ground **underneath** the poles |
| Themes | Dark and light toggle |

## Pages

### 1. Landing (`/` → `index.html`)

Mockup "Landing · variant 1": nav (logo, anchors, Sign in), a hero with headline and subtitle and two CTAs, a stats row and three numbered steps. The hero background is a decorative animated SVG route (this is the one place a non-Cesium "map" is allowed, because it's illustration, not data). CTAs go to the dashboard. The stats should use real values where cheap (for example the solve time from `/stats`, and the layer count from `/bounds`), otherwise static copy.

### 2. User dashboard (`dashboard.html`)

Mockup "User dashboard · variant 1":
- Left sidebar: logo, nav, theme toggle.
- Header with greeting and a "+ New project" button.
- KPI strip: active projects, routes in study, portfolio capex (sum of each project's best route cost), next filing date.
- Grid of project cards: phase pill, route count, name, capex / voltage / length, progress bar, and a dashed "Start a project" card.
- **Card thumbnail:** a small static rendering of that project's route geometries (inline SVG from stored GeoJSON, with the same flowing-dash animation). This is not Cesium; a dozen Cesium viewers on one page is too heavy. [Builder may choose a static Cesium snapshot instead if cheap.]
- "New project" opens a modal: name, voltage (69 / 138 / 230 kV), phase. Saving creates the project and goes to its project page, where the endpoints are picked.
- Phases: `drafting`, `comparing`, `permit-risk`, `ready`, matching the mockup pills.

### 3. Project dashboard (`project.html?id=…`)

Mockup "Project · variant 1": breadcrumb nav, a **Cesium map** filling the left side, a route-card rail on the right.
- First visit (no endpoints yet): the existing click flow. Click the project site, then a substation (snapping still works). That saves the endpoints and creates the first route with the `balanced` preset.
- "+ Add route": choose a preset not used yet. The backend computes the route and adds it to the rail.
- Every route is drawn on the map at once, each in its own colour (selected = `--acc` with the animated flowing line and tower dots, others dimmer). Clicking a card or a line selects it. The legend overlay sits bottom left and the badge top left, as in the mockup.
- Route card: label pill (Recommended / Cheapest build / Fastest, computed from placeholder or real values), score, name, six metrics (upfront, upkeep/yr, energize, length, towers or vaults, permits) and a top-risk line. Clicking "open" goes to the route dashboard.
- Overlay toggles (all nine current ones) and the Top / Enhanced view switch live in a collapsible map toolbar.
- "Compare to real line" (validation) is also in that toolbar.
- Cards can be renamed and deleted. A project can be renamed, re-phased and deleted.

### 4. Route dashboard (`route.html?id=…`, and `route.html?demo=1`)

Mockup "Route · variant 1":
- Header: route name, voltage, length, upfront / 40-yr total / energize.
- Tabs: Overview, Inventory, Costs, Maintenance, Permits & legal, Calendar, Logistics. **Tabs scroll to sections on one page.** [Builder may change this to real tab switching if clearer.]
- **Cesium 3D hero** (about 520 px tall, full width): Enhanced view by default with existing 3D poles and conductors. The animated flowing line runs on the ground underneath the poles. HUDs show terrain info top left, layer toggles top right, and "▶ Fly along" with a tower counter at the bottom centre. A hover or selected segment callout replaces the mockup's static "Segment 7" box.
- Bento grid below:
  - Upfront cost donut: real `total_cost_usd`, split into placeholder categories.
  - Inventory: real `inventory` fields plus placeholder lead times.
  - Maintenance: placeholder.
  - Deployment calendar Gantt: placeholder durations driven by the real `permitting_triggers`.
  - Permits & legal: real triggers and reasons, placeholder durations.
- Every existing results-panel item lives here too: OH/UG km, hazard metrics, protected/tribal, transitions, tribal note, right-of-way (landholders, structures), the vs-straight-line table and note, elevation profile, segments list, and the demo story card.
- `?demo=1` loads `demo_route.json` with **no backend**, which keeps today's offline demo mode.

## Feature-preservation checklist (all must work after)

Cesium terrain and the no-token fallback · click site → substation and snapping · out-of-region toast · overhead/underground drawing and tower glyphs · baseline · results metrics · ROW & permitting · vs-straight table · elevation profile · segments list · demo route with no backend · demo story · Top / Enhanced view (remembered) · terrain-height dropdown (remembered) · 3D poles and conductors under 9 km · fly-along with Esc to stop · contours toggle and elevation hover readout · cities and borders · all overlay toggles · validation compare · camera controls and help text · cost-assumption note · synthetic-data badge · render-on-demand performance.

## Backend changes

1. **Strategy presets.** `POST /route` accepts an optional `preset` (default `balanced` = today's behaviour exactly). There are four presets:
   - `balanced`: current config
   - `avoid_fire`: heavier fire multipliers
   - `cheapest`: lighter penalties, with protected and tribal penalties kept, because they're legal hard constraints
   - `underground`: cheaper underground cost

   Presets are defined as weight overrides in `config.py`. Each worker builds one `CostGrids` (and its substation trees) per preset. The cache key includes the preset. `GET /assumptions` also lists the presets. [Builder chooses the exact multipliers. Each preset must give a visibly different route on the seeded projects; verify that.]
2. **Projects store.** stdlib `sqlite3`, with the file at `data/projects.db` (git-ignored).
   - `projects(id, name, voltage_kv, phase, start, end, created_at, updated_at)`
   - `routes(id, project_id, name, preset, result_json, created_at)`
   - Endpoints: `GET/POST /projects`, `GET/PATCH/DELETE /projects/{id}`, `POST /projects/{id}/routes {preset}` (computes and stores), `GET/PATCH/DELETE /routes/{id}`.
   - The list endpoint returns summaries (no full route GeoJSON) plus small simplified geometries for the thumbnails.
3. **Seed.** On first start with an empty database, create four sample projects inside the bbox. They're modelled on the mockup (Ramona Solar Gen-tie, Santa Ysabel Reinforcement, Julian Wind Interconnect, Boulder Creek BESS Tie), with real computed routes: two or three presets each, ending at the real substations. Seeding can be skipped with an env var.
4. Tests: extend `test_api` for presets (default unchanged, different presets differ) and project/route CRUD.

## Estimates (placeholders)

One module, `frontend/js/estimates.js`, exports `estimate(routeProps, project)` and returns:

```
{ upfront_breakdown, maintenance_per_year, maintenance_breakdown, lifetime_cost_40yr,
  calendar: [{phase, start, end}], energize_date, lead_times: [{item, weeks}],
  score, top_risk, is_placeholder: true }
```

Values are derived from real route fields (length, towers, OH/UG km, fire km, triggers, parcels) with simple constant rates, so they still move between routes. **If `routeProps.estimates` exists (a future backend field), it is returned as-is.** That's the "easy implementation later" hook: a backend that adds `estimates` to the route JSON replaces every placeholder with no frontend change. Each UI value from this module shows an "est." marker.

## Frontend structure

```
frontend/
  index.html  dashboard.html  project.html  route.html
  styles/
    tokens.css       colours, type, spacing, radii, both themes
    components.css   nav, buttons, pills, cards, KPI strip, HUD, bento cells, Gantt, tables, modal
    STYLES.md        the guide (below)
  js/
    theme.js         toggle, persisted, defaults to prefers-color-scheme, no flash on load
    api.js           fetch wrapper + projects/routes calls
    estimates.js     placeholders (above)
    map.js           the current app.js Cesium code, refactored into a reusable module
    pages/landing.js dashboard.js project.js route.js
  basemap/ demo_route.json validation.json config*.js   (unchanged)
```

`map.js` is the core refactor. It turns the current `app.js` IIFE into something like `createMap(container, opts)`, which exposes drawing a route (with style, selected or not), drawing several routes, the click-to-pick flow, overlays, view mode, exaggeration, fly-along, validation and readout. The behaviour and performance tricks (render-on-demand, absolute-height hardware, FXAA) stay intact. The panel-rendering code moves to `route.js`. `app.js` is removed once everything is ported. [Builder decides the exact API.]

**Animated flowing line:** a Cesium material or polyline (for example a time-driven dash offset) drawn over the selected route's ground trace, plus dots at tower positions. Animation needs continuous rendering, which conflicts with render-on-demand. Animate only while the page is visible, throttle it, and turn it off under `prefers-reduced-motion`. Report the measured fps impact.

**Theme and Cesium:** the theme recolours the UI chrome, HUDs and route colours (via tokens read at runtime). Imagery and terrain stay the same in both themes.

## STYLES.md must cover

- Design principles: hairline borders, a grid background, serif italic for the emphasis word in headings, mono uppercase labels, glow only on live or selected things, generous whitespace.
- Every token and when to use it, with both themes.
- The type scale.
- Each component, with copy-paste HTML.
- Page skeletons.
- How to add a page.
- Map conventions: route colours, selected vs other, flowing line.
- Placeholder ("est.") conventions.
- Accessibility rules: contrast, focus rings, reduced motion.

## Calls made by the designer (not the user; flagged for review)

1. **Presets change the path only, not how cost is priced.** Each preset searches on its own grid, but `total_cost_usd` and every segment cost are always priced on the `balanced` grid. That keeps routes comparable and makes "Cheapest build", "Recommended" and portfolio capex meaningful. Under the `underground` preset the search *prefers* burial, and the reported cost shows what burial really costs.
2. **Voltage is a label plus an estimate scaler.** The routing and cost model stay as they are (they're built for 69–138 kV). `estimates.js` scales the placeholder maintenance and materials figures by voltage. Voltage-aware routing is future work.
3. **Dates.** Projects gain an optional `filing_target` date, typed in the new-project form and editable later. Seeded projects get staggered dates. The calendar starts at `filing_target`, or at today if it's empty. Energize date = end of the placeholder calendar. "Next filing" = the soonest future `filing_target`. Progress bars come from phase (drafting 15 %, comparing 50 %, permit-risk 35 %, ready 90 %). Schema: `projects(..., start_lonlat, end_lonlat, filing_target, ...)`. These names replace the ambiguous `start`/`end`.
4. **Endpoints can be changed** from the project page via "Change endpoints". After a confirm prompt, it re-picks both points and recomputes every existing route with its preset.
5. **Memory:** preset grids and substation trees are built lazily per worker, the first time each preset is used.

## Left to the builder

Exact component class names and the map.js API, preset multipliers, the Gantt rendering approach, how tabs behave (see above), the thumbnail technique, the seed endpoints, and the placeholder rates.
