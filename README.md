# Gen-tie Router

Click a proposed renewable project site and a substation on a 3D terrain map. The app finds the least-cost route for a short gen-tie line between them, picks **overhead or underground for each segment**, and draws it in 3D with towers, a results panel and a straight-line baseline for comparison.

**Region:** San Diego County backcountry: Ramona, Santa Ysabel and Julian (bbox `-116.89, 32.94, -116.56, 33.21`, about 31 × 30 km). The terrain runs from about 215 m to 1,980 m. About 95% of the area is CPUC HFTD Tier 3, and it has a patchwork of preserves, state park, Cleveland NF and tribal land.

```
data/
  prepare_data.py        one-off pipeline: download -> clip -> reproject -> rasterize
  raw/                   cached downloads (git-ignored)
  processed/             aligned 30 m GeoTIFFs + overlays/*.geojson + meta.json
backend/
  cost_model.py          overhead / underground / combined cost grids
  router.py              least-cost path, OH/UG segmentation, stats, baseline
  app.py                 FastAPI: POST /route, GET /bounds, overlays; also serves frontend/
  test_route.py          stage-2 smoke test (prints routes, writes demo route)
frontend/
  index.html app.js style.css
  config.js              your Cesium ion token (git-ignored; see config.example.js)
  demo_route.json        precomputed route for the "Load demo route" button
config.py                region, grid, data URLs and every cost weight
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
| Corridors | OpenStreetMap via Overpass: `power=line/minor_line`, `highway=motorway/trunk/primary/secondary` | bit 1 road, bit 2 power line |

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

Open **http://localhost:8000**. The backend serves the frontend too, so this one command runs everything.

- Click once to place the **project site**, then click again to place the **substation**. Clicking a purple substation marker snaps to it. The route appears in about 0.1–0.3 s.
- Overhead segments are solid orange with tower glyphs every ~300 m. Underground segments are dashed cyan. The straight baseline is a faint white line.
- Toggles: wildfire hazard (USFS WHP, draped image), HFTD/FHSZ tiers, protected land, tribal land, existing power lines, baseline. **Reset** (or Esc) clears the route.

**Demo safety:** **Load demo route** reads `frontend/demo_route.json` and needs no backend. If the backend dies, serve just the static files and the demo still works:

```bash
python -m http.server 8100 --directory frontend   # then open http://localhost:8100
```

To serve the frontend separately from the API, set `API_BASE: "http://localhost:8000"` in `frontend/config.js` (CORS is open).

## API

`POST /route` with `{"start": [lon, lat], "end": [lon, lat]}` returns a GeoJSON `FeatureCollection`:

- One `LineString` per segment: `kind: "segment"`, `mode` (`overhead` | `underground`), `length_km`, `est_cost_usd`, `cost_per_mile_usd`, `high_fire_km`
- A `MultiPoint` with `kind: "towers"` (overhead structures every ~300 m)
- A `LineString` with `kind: "baseline"`: the straight, all-overhead line with its own `est_cost_usd`, `high_fire_km`, `protected_km`, …
- Top-level `properties`: `total_km`, `total_cost_usd`, `overhead_km`, `underground_km`, `n_transitions`, `high_fire_km`, `overhead_high_fire_km`, `protected_cells_crossed`, `protected_km`, `baseline`, `savings_vs_baseline_usd`, `profile` (distance, elevation, mode), `synthetic_layers`, `compute_ms`

Points outside the region return `400`. Other endpoints: `GET /bounds` (bbox, centre, data provenance), `GET /overlays/{fire|protected|powerlines|roads|substations}`, `GET /assumptions`, `GET /health`. Interactive docs are at `/docs`.

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
| OH↔UG transition (post-hoc) | $750k each | |

Routing uses `skimage.graph.route_through_array` (8-connected, geometric distances) on the combined grid. The raw path's per-cell modes are smoothed: any overhead or underground run shorter than 450 m is merged into its neighbours. The path is then split into contiguous runs, and each run is simplified (20 m tolerance) and costed with its chosen mode. Transition structures are added on top of that cost.

## Assumptions (read before quoting numbers)

- **All dollar figures are order-of-magnitude placeholders** for a 69–138 kV single-circuit gen-tie. They are not engineering estimates. The fire multiplier on overhead lines stands in for hardening (covered conductor, fast-trip), PSPS exposure, vegetation management and ignition liability.
- The baseline's dollar cost **excludes** the protected-land routing penalty, because that penalty is a routing deterrent, not a construction cost. The panel flags baselines that cross protected land as "likely unpermittable" instead.
- "Protected" (near-impassable) means PAD-US GAP 1–2 plus state parks and wilderness, matched by designation type. That catches Cuyamaca Rancho SP, which PAD-US lists as GAP 4. National Forest and BLM land (GAP 3) carry only a mild permitting multiplier.
- Existing corridors are assumed shareable within 60 m of a power line and 30 m of a major road.

## Known limitations

- **Overhead vs underground is decided mostly by the underground cost assumption.** With continuous hazard, routes thread overhead through low-hazard grass and farm valleys. That cuts overhead line in high hazard by about 95% against the straight line, at the cost of routes about 1.3× longer. It only rarely buries the line. A sweep over 80 random routes found that at $10M/mi underground almost no route uses underground. At **$5M/mi**, 84% of routes include some underground, averaging 33% of their length. Change `UNDERGROUND_COST_PER_MILE` in `config.py` to explore this.
- Wildfire Hazard Potential reflects 2020 fuels (LANDFIRE 2.2.0), so recent burn scars may score higher than their current fuel load warrants. The FHSZ SRA layer dates from 2007, and federal land gets its tier from HFTD only.
- **Tribal land** (Barona, Santa Ysabel, Mesa Grande, Inaja and Cosmit, Capitan Grande) gets a ×10 routing penalty, so it is strongly avoided but not forbidden. It is **not** added to the dollar estimate. Any crossing is listed in `tribal_lands_crossed`, and the panel warns that it triggers tribal consultation (NHPA §106) and needs tribal consent for right-of-way (25 CFR 169). Allotted and fee land outside reservation boundaries isn't in PAD-US, so it isn't flagged.
- The optimizer prices each cell independently. Transition costs, minimum span lengths, angle structures and tower placement are applied **after** routing, so they don't influence the path itself.
- Not modelled: parcels/ROW acquisition, habitat and waters, visual impact, geotech, existing-line capacity, access roads, line losses, the interconnection queue.
- The 30 m grid with 8-connectivity gives stair-stepped paths. Lengths come from the simplified geometry, which is about 2–8% shorter than the raw grid path.
- OSM coverage is uneven: only 2 tagged substations in the bbox, and some distribution lines are missing.
- Cells outside the lon/lat bbox (the UTM grid is slightly larger) are near-impassable, so routes stay inside the box.
- The Cesium ion token in `config.js` is visible to anyone who loads the page. Restrict its allowed URLs in your Cesium ion dashboard before deploying publicly.

Data: USGS 3DEP and PAD-US (public domain), CPUC HFTD, CAL FIRE FHSZ, © OpenStreetMap contributors (ODbL), Cesium World Terrain/Imagery via Cesium ion.
