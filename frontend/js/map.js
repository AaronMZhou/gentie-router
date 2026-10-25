/* Gen-tie Router Cesium map: viewer, routes, overlays, view modes, fly-along, validation.
   Ported from app.js with its performance settings; route styling follows styles/STYLES.md §6. */
import { fmtKm } from "./panel.js";
import { cssVar, onThemeChange } from "./theme.js";

const cfg = window.APP_CONFIG || {};
const API = cfg.API_BASE || "";

export async function getJson(path, opts) {
  const r = await fetch(API + path, opts);
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch (_) { /* not json */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return r.json();
}

const COLORS = {
  start: Cesium.Color.fromCssColorString("#3ddc84"),
  end: Cesium.Color.fromCssColorString("#b57cff"),
  bbox: Cesium.Color.fromCssColorString("#ffffff").withAlpha(0.8),
  baseline: Cesium.Color.WHITE.withAlpha(0.55),
  line: Cesium.Color.fromCssColorString("#ffe14d"),     // existing 69 kV
  line138: Cesium.Color.fromCssColorString("#fff6b0"),  // existing 138 kV (pale, thicker - not the route orange)
  fire: { 1: "#ffd23f", 2: "#ff6b35", 3: "#e63030" },
  prot: { 1: "#9be3b5", 2: "#2fbf71" },
  tribal: "#e0a458",
  real: Cesium.Color.fromCssColorString("#ff4fd8"),
  country: Cesium.Color.WHITE.withAlpha(0.85),
  state: Cesium.Color.fromCssColorString("#c8d2ff").withAlpha(0.75),
  city: Cesium.Color.WHITE,
};

// Enhanced view
const TOWER_H = 26;               // conductor attachment height above ground (m)
const PHASE_OFFSETS = [-5, 0, 5]; // conductor positions along the crossarm (m)
const SAG_FRAC = 0.025;           // mid-span sag as a fraction of span length
const NEAR = new Cesium.DistanceDisplayCondition(0, 9000); // 3D hardware only when zoomed in
const M_PER_DEG = 111320;
const EXAGGERATIONS = [1, 1.5, 2, 3];

// small-area geometry on [lon, lat] (equirectangular - fine at this scale)
const kLon = (lat) => Math.cos(Cesium.Math.toRadians(lat));
const bearingOf = (a, b) => Math.atan2((b[0] - a[0]) * kLon((a[1] + b[1]) / 2), b[1] - a[1]); // rad, clockwise from north
const distM = (a, b) => Math.hypot((b[0] - a[0]) * kLon((a[1] + b[1]) / 2), b[1] - a[1]) * M_PER_DEG;
const offsetLL = ([lon, lat], dEast, dNorth) => [lon + dEast / (M_PER_DEG * kLon(lat)), lat + dNorth / M_PER_DEG];

// Small lattice-tower glyph for overhead structures.
const TOWER_IMG = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="40" viewBox="0 0 24 40">
    <g fill="none" stroke-linecap="round" stroke-linejoin="round">
      <path d="M12 3 L5 37 M12 3 L19 37 M3 11 H21 M5.5 18 H18.5 M7.5 25 L16.5 33 M16.5 25 L7.5 33 M7 25 H17"
            stroke="#1a1a1a" stroke-width="4"/>
      <path d="M12 3 L5 37 M12 3 L19 37 M3 11 H21 M5.5 18 H18.5 M7.5 25 L16.5 33 M16.5 25 L7.5 33 M7 25 H17"
            stroke="#ff8c1a" stroke-width="2"/>
    </g></svg>`);

// Flowing line (the mockup's `.flow`): screen-space dashes, 4 px on / 14 px off, in dashColor (--text)
// sliding along a soft-edged base line in color (--acc).  offset is in CSS pixels; gap > 0 dashes the
// base line itself (gap px on, gap px off) - the underground look.
const FLOW_PERIOD = 18, FLOW_SPEED = 0.033; // px per ms (mockup: 200 px per 6 s)
Cesium.Material._materialCache.addMaterial("GentieFlow", {
  fabric: {
    type: "GentieFlow",
    uniforms: { color: Cesium.Color.WHITE, dashColor: Cesium.Color.BLACK, offset: 0, gap: 0 },
    source: `
      in float v_polylineAngle;
      czm_material czm_getMaterial(czm_materialInput materialInput) {
        czm_material m = czm_getDefaultMaterial(materialInput);
        float c = cos(v_polylineAngle), s = sin(v_polylineAngle);
        float along = (mat2(c, s, -s, c) * gl_FragCoord.xy).x;
        float p = fract((along - offset * czm_pixelRatio) / (${FLOW_PERIOD}.0 * czm_pixelRatio));
        float core = 1.0 - abs(materialInput.st.t - 0.5) * 2.0; // 1 on the centre line, 0 at the edges
        float base = (gap > 0.0 && fract(along / (2.0 * gap * czm_pixelRatio)) >= 0.5) ? 0.0 : 1.0;
        vec4 col = (p < ${4 / FLOW_PERIOD} && core > 0.55) ? dashColor
                 : vec4(color.rgb, base * color.a * mix(0.3, 1.0, smoothstep(0.0, 0.6, core)));
        if (col.a < 0.005) discard;
        col = czm_gammaCorrect(col);
        m.emission = col.rgb;
        m.alpha = col.a;
        return m;
      }`,
  },
  translucent: () => true,
});

const cssColor = (name) => Cesium.Color.fromCssColorString(cssVar(name)) || Cesium.Color.WHITE; // tokens.css missing

// Country/state borders and city labels: static files from data/prepare_basemap.py, so they also
// show in demo mode.  Cities appear by tier as you zoom in: metros always, cities < 250 km, towns < 60 km.
const CITY_STYLE = {
  metro: { font: "700 15px system-ui, sans-serif", size: 9, far: Number.POSITIVE_INFINITY },
  city: { font: "600 13px system-ui, sans-serif", size: 7, far: 250e3 },
  town: { font: "12px system-ui, sans-serif", size: 5, far: 60e3 },
};

// overlay name (public) -> /overlays/<name> on the backend
const GEOJSON_OVERLAYS = { hftd: "fire", protected: "protected", tribal: "tribal", lines: "powerlines", substations: "substations" };

/**
 * opts: onPick(kind, lonlat) -> false vetoes the pick; onRouteClick(routeId) (pick mode off);
 * onStatus(msg, busy); onFlyChange(flying).
 */
export async function createMap(container, opts = {}) {
  const hasToken = !!cfg.CESIUM_ION_TOKEN;
  const status = (msg, busy = false) => opts.onStatus && opts.onStatus(msg, busy);

  // ------------------------------------------------------------------ viewer
  if (hasToken) Cesium.Ion.defaultAccessToken = cfg.CESIUM_ION_TOKEN;
  // vertex normals -> sun shading of the terrain in Enhanced view
  const terrain = hasToken ? Cesium.Terrain.fromWorldTerrain({ requestVertexNormals: true, requestWaterMask: true }) : undefined;
  const viewer = new Cesium.Viewer(container, {
    terrain,
    baseLayer: hasToken
      ? Cesium.ImageryLayer.fromWorldImagery()
      : new Cesium.ImageryLayer(new Cesium.OpenStreetMapImageryProvider({ url: "https://tile.openstreetmap.org/" })),
    baseLayerPicker: false, geocoder: false, homeButton: false, sceneModePicker: false,
    navigationHelpButton: false, animation: false, timeline: false, fullscreenButton: false,
    infoBox: false, selectionIndicator: false,
  });
  const scene = viewer.scene, globe = scene.globe;
  // Cesium catches render-loop errors (and shows its error panel), so they never reach the page's
  // data-errors boot contract.  scene.renderError covers scene.render(); errors in the clock tick /
  // entity update go straight to showErrorPanel, so that is wrapped too (seen: one entry per error).
  const seenErrors = new WeakSet();
  const recordError = (error) => {
    if (error && typeof error === "object") { if (seenErrors.has(error)) return; seenErrors.add(error); }
    const d = document.documentElement;
    d.dataset.errors = (d.dataset.errors || "") + ((error && error.message) || String(error)) + "|";
  };
  scene.renderError.addEventListener((_, error) => recordError(error));
  const showErrorPanel = viewer.cesiumWidget.showErrorPanel.bind(viewer.cesiumWidget);
  viewer.cesiumWidget.showErrorPanel = (title, message, error) => { recordError(error ?? message ?? title); showErrorPanel(title, message, error); };
  globe.depthTestAgainstTerrain = true;
  viewer.screenSpaceEventHandler.removeInputAction(Cesium.ScreenSpaceEventType.LEFT_DOUBLE_CLICK);
  // resolves once real terrain heights can be sampled; Top View swaps in flat terrain, so keep
  // the 3D terrain provider for sampling heights and for switching back
  const flatTerrain = new Cesium.EllipsoidTerrainProvider();
  let worldTerrain = null;
  const terrainReady = new Promise((resolve) => {
    if (!terrain || terrain.ready) return resolve();
    terrain.readyEvent.addEventListener(() => resolve());
    terrain.errorEvent.addEventListener(() => resolve());
  }).then(() => { if (terrain && terrain.ready) worldTerrain = terrain.provider; });

  // Render settings are FIXED per view (applyViewSettings) - switching quality while dragging made
  // brightness, shadows and terrain detail flicker.  Measured panning on an Intel UHD iGPU at 2x DPR:
  // device resolution + 4x MSAA + HDR + shadows = 12 fps; CSS resolution + FXAA + HDR + shadows = 63 fps.
  // Render-on-demand: no frames are drawn while nothing changes (idle GPU ~0%).
  viewer.useBrowserRecommendedResolution = true; // CSS-pixel resolution
  scene.msaaSamples = 1;
  scene.postProcessStages.fxaa.enabled = true;
  scene.requestRenderMode = true;
  scene.maximumRenderTimeChange = Infinity;
  globe.tileCacheSize = 1000;
  viewer.shadowMap.softShadows = true;
  viewer.shadowMap.maximumDistance = 20000;
  viewer.shadowMap.size = 4096;
  viewer.shadowMap.darkness = 0.55;
  viewer.clock.shouldAnimate = false;
  viewer.clock.currentTime = Cesium.JulianDate.fromIso8601("2026-10-24T20:30:00Z"); // 1:30 pm PDT sun

  // HUD elements live inside the map container
  const hud = (cls) => {
    const d = document.createElement("div");
    d.className = cls;
    d.hidden = true;
    container.appendChild(d);
    return d;
  };
  const toastEl = hud("map-toast"), readoutEl = hud("map-readout");

  const layers = {
    markers: new Cesium.CustomDataSource("markers"),
    region: new Cesium.CustomDataSource("region"),
  };
  Object.values(layers).forEach((ds) => viewer.dataSources.add(ds));
  const overlays = {}; // name -> GeoJsonDataSource (lazy)
  // id -> { id, ds: {route, top, enh, hardware, baseline}, start, end, box, segments, towerLL, runs, towerBearing }
  // route: underground lines + real line (both views); top: overhead geometry + flat tower icons;
  // enh: overhead line tower-to-tower (Enhanced); hardware: 3D poles/conductors (selected route only)
  const routes = new Map();
  let selectedId = null;

  // ------------------------------------------------------------------ state
  const state = { bbox: cfg.FALLBACK_BBOX || [-116.89, 32.94, -116.56, 33.21], start: null, end: null,
                  view: "enhanced", pickMode: false, baseline: true, flyStoppedAt: 0 };
  try { if (localStorage.getItem("gentie.view") === "top") state.view = "top"; } catch (_) { /* storage blocked */ }
  state.exaggeration = 1.5; // Enhanced view terrain height scale (dropdown)
  try {
    const saved = Number(localStorage.getItem("gentie.exaggeration"));
    if (EXAGGERATIONS.includes(saved)) state.exaggeration = saved;
  } catch (_) { /* storage blocked */ }

  // ------------------------------------------------------------------ helpers
  let toastTimer = 0;
  function toast(msg, kind = "error", ms = 5000) {
    toastEl.textContent = msg;
    toastEl.className = "map-toast" + (kind === "info" ? " info" : "");
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toastEl.hidden = true), ms);
  }

  function inBbox([lon, lat]) {
    const [w, s, e, n] = state.bbox;
    return lon >= w && lon <= e && lat >= s && lat <= n;
  }

  // Render-on-demand doesn't notice async work finishing (ground polylines, overlays, tiles for
  // newly added entities), so request frames for a short while after changing the scene.
  function redraw(ms = 2500) {
    const until = performance.now() + ms;
    const pump = () => { scene.requestRender(); if (performance.now() < until) setTimeout(pump, 120); };
    pump();
  }

  const pinBuilder = new Cesium.PinBuilder();
  function placeMarker(kind, lonlat, label) {
    const id = `marker-${kind}`;
    layers.markers.entities.removeById(id);
    layers.markers.entities.add({
      id,
      position: Cesium.Cartesian3.fromDegrees(lonlat[0], lonlat[1]),
      billboard: {
        image: pinBuilder.fromText(kind === "start" ? "P" : "S", kind === "start" ? COLORS.start : COLORS.end, 44).toDataURL(),
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
      label: {
        text: label, font: "600 13px system-ui, sans-serif", fillColor: Cesium.Color.WHITE,
        outlineColor: Cesium.Color.BLACK, outlineWidth: 3, style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM, pixelOffset: new Cesium.Cartesian2(0, -48),
        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
    });
    redraw();
  }

  function clearMarkers() {
    state.start = state.end = null;
    layers.markers.entities.removeAll();
    redraw(600);
  }

  // ------------------------------------------------------------------ region
  function drawRegion() {
    layers.region.entities.removeAll();
    const [w, s, e, n] = state.bbox;
    layers.region.entities.add({
      polyline: {
        positions: Cesium.Cartesian3.fromDegreesArray([w, s, e, s, e, n, w, n, w, s]),
        width: 3, clampToGround: true,
        material: new Cesium.PolylineDashMaterialProperty({ color: COLORS.bbox, dashLength: 24 }),
      },
    });
    redraw();
  }

  // Tilted view of a lon/lat box, nudged so it sits right of the side panel.
  function frameBox([w, s, e, n], duration = 1.5, { heading = 0, pitchDeg = -45, rangeFactor = 2.5 } = {}) {
    const center = [(w + e) / 2, (s + n) / 2];
    const halfDiag = Math.max(distM([w, s], [e, n]) / 2, 2500);
    const range = halfDiag * rangeFactor;
    // the panel covers ~1/4 of the screen; visible ground width ~ 2*range*tan(30deg).
    // Shift the target toward screen-left (bearing heading - 90deg) so the box lands right of the panel.
    const shiftM = window.innerWidth > 720 ? (350 / window.innerWidth) * range * Math.tan(Math.PI / 6) : 0;
    const left = heading - Math.PI / 2;
    const [tx, ty] = offsetLL(center, shiftM * Math.sin(left), shiftM * Math.cos(left));
    viewer.camera.flyToBoundingSphere(new Cesium.BoundingSphere(Cesium.Cartesian3.fromDegrees(tx, ty, 800), halfDiag), {
      offset: new Cesium.HeadingPitchRange(heading, Cesium.Math.toRadians(pitchDeg), range), duration,
    });
  }

  function homeView(duration = 0) {
    if (state.view === "enhanced") frameBox(state.bbox, duration, { pitchDeg: -35, rangeFactor: 2.1 });
    else frameBox(state.bbox, duration);
  }

  function frameRoute(id = selectedId, duration = 1.6) {
    const r = routes.get(id);
    if (!r) return homeView(duration);
    if (state.view === "enhanced") { // side-on and low: look across the terrain at the line,
      // from whichever side of it the camera is already on (no flipping to the far side)
      const b = bearingOf(r.start, r.end), h = viewer.camera.heading;
      const sides = [b + Math.PI / 2, b - Math.PI / 2];
      const turn = (a) => Math.abs(Math.atan2(Math.sin(a - h), Math.cos(a - h)));
      const heading = turn(sides[0]) <= turn(sides[1]) ? sides[0] : sides[1];
      frameBox(r.box, duration, { heading, pitchDeg: -30, rangeFactor: 2.8 });
    } else {
      frameBox(r.box, duration);
    }
  }

  // ------------------------------------------------------------------ overlays
  async function loadOverlay(name) {
    if (overlays[name]) return overlays[name];
    const ds = await Cesium.GeoJsonDataSource.load(`${API}/overlays/${name}`, { clampToGround: true });
    const labelled = new Set();
    for (const ent of ds.entities.values.slice()) {
      const props = ent.properties ? ent.properties.getValue(Cesium.JulianDate.now()) : {};
      if (ent.polygon) {
        let color, alpha;
        if (name === "fire") [color, alpha] = [COLORS.fire[props.level], 0.28];
        else if (name === "tribal") [color, alpha] = [COLORS.tribal, 0.4];
        else [color, alpha] = [COLORS.prot[props.level], props.level === 2 ? 0.5 : 0.18];
        ent.polygon.material = Cesium.Color.fromCssColorString(color || "#888").withAlpha(alpha);
        ent.polygon.outline = false;
        if (name === "tribal" && props.name && !labelled.has(props.name)) {
          labelled.add(props.name);
          const hierarchy = ent.polygon.hierarchy.getValue(Cesium.JulianDate.now());
          ds.entities.add({
            position: Cesium.BoundingSphere.fromPoints(hierarchy.positions).center,
            label: {
              text: props.name, font: "600 12px system-ui, sans-serif", fillColor: Cesium.Color.fromCssColorString("#ffe0b8"),
              outlineColor: Cesium.Color.BLACK, outlineWidth: 3, style: Cesium.LabelStyle.FILL_AND_OUTLINE,
              heightReference: Cesium.HeightReference.CLAMP_TO_GROUND, disableDepthTestDistance: Number.POSITIVE_INFINITY,
            },
          });
        }
      }
      if (ent.polyline) { // existing grid: CEC transmission lines by voltage; OSM-only extras thinner
        const kv = Number(props.kv) || 0;
        ent.polyline.material = (kv >= 115 ? COLORS.line138 : COLORS.line).withAlpha(props.source === "CEC" ? 0.95 : 0.7);
        ent.polyline.width = kv >= 115 ? 4 : props.source === "CEC" ? 2.5 : 1.8;
      }
      if (ent.billboard) { // substations: replace default pin with a labelled marker
        ent.billboard = undefined;
        ent.point = new Cesium.PointGraphics({
          pixelSize: 11, color: COLORS.end, outlineColor: Cesium.Color.WHITE, outlineWidth: 2,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND, disableDepthTestDistance: Number.POSITIVE_INFINITY,
        });
        ent.label = new Cesium.LabelGraphics({
          text: `${props.name}${props.voltage_label ? ` (${props.voltage_label})` : ""}`,
          font: "12px system-ui, sans-serif", fillColor: Cesium.Color.WHITE, outlineColor: Cesium.Color.BLACK,
          outlineWidth: 3, style: Cesium.LabelStyle.FILL_AND_OUTLINE, pixelOffset: new Cesium.Cartesian2(0, -16),
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND, disableDepthTestDistance: Number.POSITIVE_INFINITY,
          scaleByDistance: new Cesium.NearFarScalar(2e3, 1.1, 6e4, 0.75),
        });
        ent.isSubstation = true;
      }
    }
    viewer.dataSources.add(ds);
    overlays[name] = ds;
    return ds;
  }

  let riskLayer = null; // continuous hazard: a single draped PNG over the bbox
  async function toggleRisk(on) {
    try {
      if (on && !riskLayer) {
        const [w, s, e, n] = state.bbox;
        const provider = await Cesium.SingleTileImageryProvider.fromUrl(`${API}/overlays/firerisk`, {
          rectangle: Cesium.Rectangle.fromDegrees(w, s, e, n),
        });
        riskLayer = viewer.imageryLayers.addImageryProvider(provider);
      }
      if (riskLayer) riskLayer.show = on;
      redraw();
    } catch (err) {
      toast("Could not load wildfire hazard overlay (backend offline?)");
    }
  }

  async function toggleGeoJson(name, on) {
    try {
      const ds = on ? await loadOverlay(name) : overlays[name];
      if (ds) ds.show = on;
      redraw();
    } catch (err) {
      toast(`Could not load ${name} overlay (backend offline?)`);
    }
  }

  const contourMaterial = Cesium.Material.fromType("ElevationContour", {
    color: Cesium.Color.WHITE.withAlpha(0.18), spacing: 100, width: 1.0,
  });
  const toggleContours = (on) => { globe.material = on ? contourMaterial : undefined; redraw(800); };

  const basemap = {}; // "borders" | "cities" -> CustomDataSource (lazy)
  async function loadBasemap(name) {
    if (basemap[name]) return basemap[name];
    const r = await fetch(`basemap/${name}.geojson`);
    if (!r.ok) throw new Error(r.statusText);
    const ds = new Cesium.CustomDataSource(name);
    for (const f of (await r.json()).features) {
      const p = f.properties;
      if (name === "borders") {
        const country = p.kind === "country";
        const lines = f.geometry.type === "MultiLineString" ? f.geometry.coordinates : [f.geometry.coordinates];
        for (const line of lines) {
          ds.entities.add({
            polyline: {
              positions: Cesium.Cartesian3.fromDegreesArray(line.flat()), clampToGround: true, width: country ? 3 : 2,
              material: country ? COLORS.country : new Cesium.PolylineDashMaterialProperty({ color: COLORS.state, dashLength: 16 }),
            },
          });
        }
      } else {
        const st = CITY_STYLE[p.tier] || CITY_STYLE.town;
        const show = new Cesium.DistanceDisplayCondition(0, st.far);
        ds.entities.add({
          position: Cesium.Cartesian3.fromDegrees(...f.geometry.coordinates),
          point: {
            pixelSize: st.size, color: COLORS.city, outlineColor: Cesium.Color.BLACK, outlineWidth: 1.5,
            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND, disableDepthTestDistance: Number.POSITIVE_INFINITY,
            distanceDisplayCondition: show,
          },
          label: {
            text: p.name, font: st.font, fillColor: Cesium.Color.WHITE, outlineColor: Cesium.Color.BLACK,
            outlineWidth: 3, style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            horizontalOrigin: Cesium.HorizontalOrigin.LEFT, verticalOrigin: Cesium.VerticalOrigin.CENTER,
            pixelOffset: new Cesium.Cartesian2(st.size, 0),
            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND, disableDepthTestDistance: Number.POSITIVE_INFINITY,
            distanceDisplayCondition: show,
          },
        });
      }
    }
    viewer.dataSources.add(ds);
    basemap[name] = ds;
    return ds;
  }

  async function toggleBasemap(name, on) {
    try {
      const ds = on ? await loadBasemap(name) : basemap[name];
      if (ds) ds.show = on;
      redraw();
    } catch (err) {
      toast(`Could not load ${name} (run python data/prepare_basemap.py): ${err.message}`);
    }
  }

  // name: fire | hftd | protected | tribal | lines | baseline | contours | cities | borders
  function toggleOverlay(name, on) {
    if (name === "fire") return toggleRisk(on);
    if (name === "contours") return toggleContours(on);
    if (name === "cities" || name === "borders") return toggleBasemap(name, on);
    if (name === "baseline") { state.baseline = on; applyRouteVisibility(); redraw(600); return; }
    return toggleGeoJson(GEOJSON_OVERLAYS[name] || name, on);
  }

  // ------------------------------------------------------------------ view modes
  // Top View: basic map of the track - flat terrain, imagery, no lighting/shadows (cheapest).
  // Enhanced: 3D terrain x1.5, sun lighting, shadows, HDR.  Nothing here changes while the camera moves.
  async function applyViewSettings() {
    const enh = state.view === "enhanced";
    scene.verticalExaggeration = enh ? state.exaggeration : 1;
    globe.enableLighting = enh;
    scene.highDynamicRange = enh;
    viewer.shadows = enh;
    viewer.terrainShadows = enh ? Cesium.ShadowMode.ENABLED : Cesium.ShadowMode.DISABLED;
    globe.maximumScreenSpaceError = enh ? 1.33 : 2;
    if (!enh) scene.terrainProvider = flatTerrain;
    await terrainReady;  // the 3D terrain may still be loading on first run
    if (worldTerrain) scene.terrainProvider = state.view === "enhanced" ? worldTerrain : flatTerrain;
    redraw();
  }

  function applyRouteVisibility() {
    const enh = state.view === "enhanced";
    for (const r of routes.values()) {
      r.ds.top.show = !enh;
      r.ds.enh.show = r.ds.hardware.show = enh;
      r.ds.baseline.show = state.baseline && r.id === selectedId;
    }
  }

  // Switching views keeps the camera where it is; only the rendering changes.
  async function applyView() {
    const enh = state.view === "enhanced";
    applyRouteVisibility();
    readoutEl.hidden = true;
    try { localStorage.setItem("gentie.view", state.view); } catch (_) { /* storage blocked */ }
    await applyViewSettings();
    if (enh) liftCameraAboveTerrain();
  }

  async function setView(view) {
    if (state.view === view) return;
    stopFly();
    state.view = view;
    await applyView();
  }

  async function setExaggeration(x) {
    stopFly();
    state.exaggeration = Number(x);
    try { localStorage.setItem("gentie.exaggeration", String(state.exaggeration)); } catch (_) { /* storage blocked */ }
    await applyViewSettings();
    const r = routes.get(selectedId);
    if (r) buildHardware(r); // towers/conductors sit on the rescaled terrain
    liftCameraAboveTerrain();
  }

  // Coming from flat Top View, a low camera could end up inside the (exaggerated) 3D terrain.
  async function liftCameraAboveTerrain() {
    if (!worldTerrain) return;
    const cam = Cesium.Cartographic.clone(viewer.camera.positionCartographic);
    const ground = Cesium.Cartographic.clone(cam);
    try { await Cesium.sampleTerrain(worldTerrain, 11, [ground]); } catch (_) { return; }
    const minH = (ground.height || 0) * state.exaggeration + 150;
    if (cam.height < minH) {
      viewer.camera.setView({ destination: Cesium.Cartesian3.fromRadians(cam.longitude, cam.latitude, minH),
        orientation: { heading: viewer.camera.heading, pitch: viewer.camera.pitch, roll: 0 } });
    }
  }

  // ------------------------------------------------------------------ flow + route colours
  // The flow keeps render-on-demand and requests ~30 frames/s itself, so camera moves still render
  // at full rate and stopFly() (which resets requestRenderMode) can't stop it.
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  let flowOn = true, flowTimer = 0, flowPhase = 0, flowT = 0;
  function syncFlow() {
    const sel = routes.get(selectedId); // every line of the selected route carries the flow material
    const run = flowOn && !!sel && sel.ohLines.length + sel.ugLines.length > 0 && !document.hidden && !reducedMotion.matches;
    if (run && !flowTimer) { flowT = performance.now(); flowTimer = setInterval(() => scene.requestRender(), 33); }
    else if (!run && flowTimer) { clearInterval(flowTimer); flowTimer = 0; scene.requestRender(); }
  }
  document.addEventListener("visibilitychange", syncFlow);
  reducedMotion.addEventListener("change", syncFlow);

  // MaterialProperty; non-constant, so Cesium re-reads the uniforms every frame.  One instance per
  // look (overhead solid, underground dashed) so each batches once; both share the phase.
  const makeFlow = (gap) => ({
    definitionChanged: new Cesium.Event(), isConstant: false,
    // color must be a Property: Cesium's dynamic-material batch reads material.color.getValue()
    color: new Cesium.ConstantProperty(Cesium.Color.WHITE), dashColor: Cesium.Color.BLACK,
    getType: () => "GentieFlow",
    getValue(time, result = {}) {
      if (flowTimer) { // advances only while running, so pausing freezes the dashes in place
        const now = performance.now();
        flowPhase = (flowPhase + (now - flowT) * FLOW_SPEED) % FLOW_PERIOD;
        flowT = now;
      }
      result.color = this.color.getValue(time); result.dashColor = this.dashColor;
      result.offset = flowPhase; result.gap = gap;
      return result;
    },
    equals(other) { return this === other; },
  });
  const flowOh = makeFlow(0), flowUg = makeFlow(7); // 7 on / 7 off = the old 14 px underground dash

  // Selected route = --route-1 (--acc); the others take --route-2..4 in insertion order.
  const routeSlot = (id) => id === selectedId ? 1 : 2 + [...routes.keys()].filter((o) => o !== selectedId).indexOf(id) % 3;
  const routeColor = (id) => routes.has(id) ? cssVar(`--route-${routeSlot(id)}`) : "";

  // (Re)colours one route for its selection state and the current theme.
  function paintRoute(r) {
    const sel = r.id === selectedId;
    const color = Cesium.Color.fromCssColorString(routeColor(r.id)) || Cesium.Color.WHITE;
    const dashed = routeSlot(r.id) === 2; // the --acc2 route is dashed (7 on, 6 off)
    const dim = color.withAlpha(0.65);
    for (const ent of r.ohLines) {
      ent.polyline.width = sel ? 9 : 3;
      ent.polyline.material = sel ? flowOh
        : dashed ? new Cesium.PolylineDashMaterialProperty({ color: dim, dashLength: 13, dashPattern: 0x01ff }) : dim;
    }
    for (const ent of r.ugLines) {
      ent.polyline.width = sel ? 7 : 3;
      ent.polyline.material = sel ? flowUg : new Cesium.PolylineDashMaterialProperty({ color: dim, dashLength: 14 });
    }
    const acc = cssColor("--acc");
    for (const ent of r.ds.top.entities.values) {
      if (ent.billboard) ent.show = sel; // lattice glyphs: selected route only
      if (ent.point) { ent.show = sel; ent.point.color = acc; }
    }
  }

  function paintAll() {
    const acc = cssColor("--acc");
    for (const f of [flowOh, flowUg]) { f.color.setValue(acc); f.dashColor = cssColor("--text"); }
    for (const r of routes.values()) paintRoute(r);
    const r = routes.get(selectedId);
    if (r) for (const ent of r.ds.hardware.entities.values) if (ent.point) ent.point.color = acc;
    syncFlow();
    redraw(1500);
  }
  onThemeChange(paintAll);

  // ------------------------------------------------------------------ routes
  function removeRoute(id) {
    stopFly();
    const r = routes.get(id);
    if (!r) return;
    routes.delete(id);
    if (selectedId === id) { selectedId = null; clearMarkers(); } // P/S markers belonged to this route
    // remove() is false for a layer whose async add() hasn't landed yet; showRoute's add callback drops it
    for (const ds of Object.values(r.ds)) viewer.dataSources.remove(ds, true);
    paintAll(); // the other routes' colour slots shift
  }

  function clearRoutes() {
    stopFly();
    for (const id of [...routes.keys()]) removeRoute(id);
    redraw(600);
  }

  function showRoute(fc, { id = "main", selected = true, frame = true } = {}) {
    removeRoute(id);
    const ds = {};
    for (const k of ["route", "top", "enh", "hardware", "baseline"]) ds[k] = new Cesium.CustomDataSource(`${k}:${id}`);
    let towerLL = [];
    const segments = [], ohLines = [], ugLines = []; // coloured by paintRoute
    const box = [Infinity, Infinity, -Infinity, -Infinity];
    for (const f of fc.features) {
      const p = f.properties;
      if (p.kind === "segment") {
        segments.push(f);
        const positions = Cesium.Cartesian3.fromDegreesArray(f.geometry.coordinates.flat());
        for (const [lon, lat] of f.geometry.coordinates) {
          box[0] = Math.min(box[0], lon); box[1] = Math.min(box[1], lat);
          box[2] = Math.max(box[2], lon); box[3] = Math.max(box[3], lat);
        }
        if (p.mode === "overhead") {
          ohLines.push(ds.top.entities.add({ polyline: { positions, clampToGround: true } }));
        } else {
          ugLines.push(ds.route.entities.add({ polyline: { positions, clampToGround: true } }));
        }
      } else if (p.kind === "towers") {
        towerLL = f.geometry.coordinates;
      } else if (p.kind === "baseline") {
        ds.baseline.entities.add({
          polyline: {
            positions: Cesium.Cartesian3.fromDegreesArray(f.geometry.coordinates.flat()),
            clampToGround: true, width: 2, material: COLORS.baseline,
          },
        });
      }
    }
    for (const [lon, lat] of towerLL) {
      ds.top.entities.add({
        position: Cesium.Cartesian3.fromDegrees(lon, lat),
        billboard: {
          image: TOWER_IMG, verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
          scaleByDistance: new Cesium.NearFarScalar(1.5e3, 1.0, 4e4, 0.45),
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
      });
      // Top View terrain is flat, so a plain height-0 point sits on the ground without clamping
      ds.top.entities.add({ position: Cesium.Cartesian3.fromDegrees(lon, lat), point: { pixelSize: 7,
        outlineColor: Cesium.Color.BLACK.withAlpha(0.7), outlineWidth: 1.5, disableDepthTestDistance: Number.POSITIVE_INFINITY } });
    }

    // towers grouped into overhead runs (a gap > 400 m = underground section in between)
    const runs = [];
    towerLL.forEach((t, i) => {
      if (!i || distM(towerLL[i - 1], t) > 400) runs.push([]);
      runs[runs.length - 1].push(i);
    });
    const towerBearing = towerLL.map(() => 0);
    for (const run of runs) run.forEach((i, k) => {
      const a = towerLL[run[Math.max(k - 1, 0)]], b = towerLL[run[Math.min(k + 1, run.length - 1)]];
      towerBearing[i] = run.length > 1 ? bearingOf(a, b) : 0;
    });
    // Enhanced: the overhead trace runs tower to tower, exactly under the conductors
    for (const run of runs) {
      if (run.length < 2) continue;
      ohLines.push(ds.enh.entities.add({ polyline: {
        positions: Cesium.Cartesian3.fromDegreesArray(run.flatMap((i) => towerLL[i])), clampToGround: true,
      } }));
    }

    const props = fc.properties;
    const r = { id, ds, start: props.start, end: props.end, box, segments, ohLines, ugLines, towerLL, runs, towerBearing };
    routes.set(id, r);
    // Route layers go under the overlays, as in the original fixed layer order
    // (route, routeTop, routeEnh, hardware, baseline were created before any overlay).
    const order = [ds.route, ds.top, ds.enh, ds.hardware, ds.baseline];
    Promise.all(order.map((d) => viewer.dataSources.add(d))).then(() => {
      for (const d of order.slice().reverse()) {
        if (routes.get(id) === r) viewer.dataSources.lowerToBottom(d);
        else viewer.dataSources.remove(d, true);
      }
      redraw();
    });
    if (selected) selectRoute(id); else { applyRouteVisibility(); paintAll(); }

    if (frame && Number.isFinite(box[0])) frameRoute(id, 1.6);
  }

  // Only the selected route gets markers and 3D hardware (keeps the Enhanced view fps).
  function selectRoute(id) {
    const r = routes.get(id);
    if (!r) return;
    selectedId = id;
    for (const o of routes.values()) if (o !== r) o.ds.hardware.entities.removeAll();
    state.start = r.start;
    state.end = r.end;
    placeMarker("start", r.start, "Project site");
    placeMarker("end", r.end, "Substation");
    applyRouteVisibility();
    paintAll();
    buildHardware(r);
  }

  // 3D poles + crossarms + three sagging conductors per span.  Positions are absolute heights
  // (sampled terrain x exaggeration + attachment height) rather than RELATIVE_TO_GROUND, so the
  // entities don't re-query terrain every time a tile loads (that made the fly-through stutter).
  async function buildHardware(r) {
    if (!r || !r.towerLL.length) return;
    if (!r.towerHeights) { // sample real terrain once per route; rebuilds (new exaggeration) reuse it
      await terrainReady;
      const carto = r.towerLL.map(([lon, lat]) => Cesium.Cartographic.fromDegrees(lon, lat));
      try { if (worldTerrain) await Cesium.sampleTerrainMostDetailed(worldTerrain, carto); } catch (_) { /* flat fallback */ }
      if (routes.get(r.id) !== r || selectedId !== r.id) return; // replaced or deselected meanwhile
      r.towerHeights = carto.map((c) => c.height || 0);
    }
    r.ds.hardware.entities.removeAll();
    const e = state.exaggeration;
    const ground = r.towerHeights.map((h) => h * e);
    const steel = Cesium.Color.fromCssColorString("#c9ced4");
    const wire = Cesium.Color.fromCssColorString("#2b2f33").withAlpha(0.9);
    const ents = r.ds.hardware.entities;
    const acc = cssColor("--acc");
    ents.suspendEvents();
    r.towerLL.forEach(([lon, lat], i) => {
      const at = (h) => Cesium.Cartesian3.fromDegrees(lon, lat, ground[i] + h);
      // tower dot at the pole foot, on the flow line; shown at every distance (the poles are NEAR only).
      // Coarser rendered terrain can sit above the sampled height, so skip the depth test when near.
      ents.add({ position: at(2), point: { pixelSize: 7, color: acc, outlineColor: Cesium.Color.BLACK.withAlpha(0.7),
        outlineWidth: 1.5, disableDepthTestDistance: 5000 } });
      ents.add({ position: at(TOWER_H / 2 - 1), cylinder: { length: TOWER_H + 2, topRadius: 0.35, bottomRadius: 1.1,
        material: steel, distanceDisplayCondition: NEAR, shadows: Cesium.ShadowMode.ENABLED } });
      ents.add({ position: at(TOWER_H),
        orientation: Cesium.Transforms.headingPitchRollQuaternion(at(TOWER_H), new Cesium.HeadingPitchRoll(r.towerBearing[i], 0, 0)),
        box: { dimensions: new Cesium.Cartesian3(12, 0.5, 0.5), material: steel, distanceDisplayCondition: NEAR,
               shadows: Cesium.ShadowMode.ENABLED } });
    });
    for (const run of r.runs) for (let k = 0; k + 1 < run.length; k++) {
      const a = run[k], b = run[k + 1];
      const span = distM(r.towerLL[a], r.towerLL[b]);
      const hA = ground[a] + TOWER_H, hB = ground[b] + TOWER_H;
      for (const off of PHASE_OFFSETS) {  // crossarm runs at bearing + 90deg
        const pa = offsetLL(r.towerLL[a], off * Math.cos(r.towerBearing[a]), -off * Math.sin(r.towerBearing[a]));
        const pb = offsetLL(r.towerLL[b], off * Math.cos(r.towerBearing[b]), -off * Math.sin(r.towerBearing[b]));
        const pts = [];
        for (let s = 0; s <= 12; s++) {
          const t = s / 12;
          pts.push(pa[0] + (pb[0] - pa[0]) * t, pa[1] + (pb[1] - pa[1]) * t,
                   hA + (hB - hA) * t - SAG_FRAC * span * 4 * t * (1 - t));
        }
        ents.add({ polyline: { positions: Cesium.Cartesian3.fromDegreesArrayHeights(pts), width: 1.5,
          arcType: Cesium.ArcType.NONE, material: wire, distanceDisplayCondition: NEAR } });
      }
    }
    ents.resumeEvents();
    redraw();
  }

  // ------------------------------------------------------------------ fly-through
  // Precomputed, smoothed camera path at uniform spacing; each frame interpolates between samples
  // (no stepping), and the heaviest render settings are relaxed for the duration of the flight.
  let fly = null;
  const flyListeners = [];
  async function startFly(id = selectedId) {
    if (fly) return stopFly();
    const r = routes.get(id);
    if (!r) return;
    status("Preparing fly-through…", true);
    const line = [];
    for (const f of r.segments) for (const c of f.geometry.coordinates) {
      if (!line.length || distM(line[line.length - 1], c) > 0.5) line.push(c);
    }
    const STEP = 25, pts = [];  // resample every 25 m along the route
    let carry = 0;
    for (let i = 0; i + 1 < line.length; i++) {
      const L = distM(line[i], line[i + 1]);
      for (let t = carry; t < L; t += STEP) pts.push([line[i][0] + (line[i + 1][0] - line[i][0]) * t / L,
                                                     line[i][1] + (line[i + 1][1] - line[i][1]) * t / L]);
      carry = (carry - L) % STEP;
      if (carry < 0) carry += STEP;
    }
    pts.push(line[line.length - 1]);
    if (pts.length < 3) return status("");

    await terrainReady;
    const carto = pts.map(([lon, lat]) => Cesium.Cartographic.fromDegrees(lon, lat));
    // Top View flies over flat ground; Enhanced follows the (exaggerated) 3D terrain
    try { if (worldTerrain && state.view === "enhanced") await Cesium.sampleTerrain(worldTerrain, 12, carto); } catch (_) { /* flat */ }
    const e = scene.verticalExaggeration, N = pts.length, AHEAD = 24; // look ~600 m ahead
    const ground = carto.map((c) => (c.height || 0) * e);
    const clear = ground.map((_, i) => Math.max(...ground.slice(Math.max(i - 8, 0), Math.min(i + AHEAD, N - 1) + 1)) + 280);
    const smooth = (arr, rad) => arr.map((_, i) => {
      let s = 0, n = 0;
      for (let j = Math.max(i - rad, 0); j <= Math.min(i + rad, arr.length - 1); j++) { s += arr[j]; n++; }
      return s / n;
    });
    const height = smooth(clear, 24);
    const hx = smooth(pts.map((p, i) => Math.sin(bearingOf(p, pts[Math.min(i + AHEAD, N - 1)]))), 16);
    const hy = smooth(pts.map((p, i) => Math.cos(bearingOf(p, pts[Math.min(i + AHEAD, N - 1)]))), 16);
    const heading = hx.map((x, i) => Math.atan2(x, hy[i]));
    // sample index nearest each tower, for onFlyProgress (towers passed so far)
    const towerAt = r.towerLL.map((t) => {
      let best = 0;
      for (let j = 1; j < N; j++) if (distM(pts[j], t) < distM(pts[best], t)) best = j;
      return best;
    }).sort((a, b) => a - b);

    const km = (N - 1) * STEP / 1000;
    const duration = Math.min(Math.max(km * 3, 20), 60); // seconds
    scene.requestRenderMode = false; // the camera is driven from preRender, so render every frame

    const t0 = performance.now();
    let passed = 0;
    const tick = () => {
      const u = Math.min((performance.now() - t0) / 1000 / duration, 1);
      const f = u * (N - 1), i = Math.min(Math.floor(f), N - 2), w = f - i;
      let dh = heading[i + 1] - heading[i];
      dh = Math.atan2(Math.sin(dh), Math.cos(dh)); // shortest turn
      viewer.camera.setView({
        destination: Cesium.Cartesian3.fromDegrees(pts[i][0] + (pts[i + 1][0] - pts[i][0]) * w,
                                                   pts[i][1] + (pts[i + 1][1] - pts[i][1]) * w,
                                                   height[i] + (height[i + 1] - height[i]) * w),
        orientation: { heading: heading[i] + dh * w, pitch: Cesium.Math.toRadians(-17), roll: 0 },
      });
      if (flyListeners.length) {
        while (passed < towerAt.length && towerAt[passed] <= f) passed++;
        for (const cb of flyListeners) cb(passed, towerAt.length, f * STEP / 1000);
      }
      if (u >= 1) stopFly();
    };
    scene.preRender.addEventListener(tick);
    fly = { tick };
    if (opts.onFlyChange) opts.onFlyChange(true);
    status(`Flying along the route (${Math.round(duration)} s) — click or Esc to stop.`);
  }

  function stopFly() {
    if (!fly) return;
    scene.preRender.removeEventListener(fly.tick);
    fly = null;
    scene.requestRenderMode = true;
    redraw(500);
    if (opts.onFlyChange) opts.onFlyChange(false);
    status("");
  }

  // ------------------------------------------------------------------ validation
  let validation = null, valIndex = -1;
  // Cycles through validation.json cases; shows the case route (id "main") plus the real line.
  async function showValidation() {
    try {
      if (!validation) {
        const r = await fetch("validation.json");
        if (!r.ok) throw new Error(r.statusText);
        validation = await r.json();
      }
    } catch (err) {
      toast(`Could not load validation.json (run python -m backend.validate): ${err.message}`);
      return null;
    }
    valIndex = (valIndex + 1) % validation.cases.length;
    const c = validation.cases[valIndex];
    showRoute(c.route);
    routes.get("main").ds.route.entities.add({
      polyline: {
        positions: Cesium.Cartesian3.fromDegreesArray(c.real_line.geometry.coordinates.flat()),
        clampToGround: true, width: 4, material: COLORS.real.withAlpha(0.9),
      },
    });
    redraw();
    const count = validation.cases.length;
    const row = (name, m) => `<tr><td>${name}</td><td>${m.mean_dist_m} m</td><td>${m.pct_within_500m.toFixed(0)}%</td><td>${m.route_km} km</td></tr>`;
    const prot = (c.real_line_protected_crossed || []).map((u) => `${u.name} (${fmtKm(u.km)})`).join(", ");
    return {
      label: `${c.label} (${valIndex + 1}/${count}, real line ${c.metrics.real_km} km)`,
      rows: row("Router (line hidden)", c.metrics) +
        row("Straight line", c.baseline_metrics) +
        row("Router (line known)", c.with_line_in_corridor_metrics) +
        row("Hidden + preserves passable", c.preserves_passable_metrics),
      note: `Leave-one-out: the real line is removed from the corridor layer before routing.` +
        (prot ? `<br><span class="bad">Real line crosses preserves:</span> ${prot} — likely predating them; our model treats preserves as near-impassable for new right-of-way.` : ""),
      index: valIndex, count, route: c.route,
    };
  }

  // ------------------------------------------------------------------ clicks
  const routeOf = (entity) => {
    for (const r of routes.values()) if (Object.values(r.ds).some((d) => d.entities.contains(entity))) return r.id;
    return null;
  };
  const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
  handler.setInputAction(() => { if (fly) { state.flyStoppedAt = performance.now(); stopFly(); } },
    Cesium.ScreenSpaceEventType.LEFT_DOWN);
  handler.setInputAction((click) => {
    if (performance.now() - state.flyStoppedAt < 600) return; // that click just stopped the fly-through
    const picked = viewer.scene.pick(click.position);
    if (!state.pickMode) {
      const id = opts.onRouteClick && picked && picked.id ? routeOf(picked.id) : null;
      if (id !== null) opts.onRouteClick(id);
      return;
    }
    let lonlat = null;
    // snap to a substation marker if one was clicked
    if (picked && picked.id && picked.id.isSubstation) {
      const c = Cesium.Cartographic.fromCartesian(picked.id.position.getValue(Cesium.JulianDate.now()));
      lonlat = [Cesium.Math.toDegrees(c.longitude), Cesium.Math.toDegrees(c.latitude)];
    } else {
      const ray = viewer.camera.getPickRay(click.position);
      const cart = (ray && viewer.scene.globe.pick(ray, viewer.scene)) || viewer.camera.pickEllipsoid(click.position);
      if (!cart) return;
      const c = Cesium.Cartographic.fromCartesian(cart);
      lonlat = [Cesium.Math.toDegrees(c.longitude), Cesium.Math.toDegrees(c.latitude)];
    }
    if (!inBbox(lonlat)) {
      toast("Please click inside the study region (dashed outline).", "info", 3000);
      return;
    }
    const kind = !state.start || state.end ? "start" : "end"; // first click, or starting over after a finished route
    if (opts.onPick && opts.onPick(kind, lonlat) === false) return;
    if (kind === "start") {
      clearRoutes();
      clearMarkers();
      state.start = lonlat;
      placeMarker("start", lonlat, "Project site");
    } else {
      state.end = lonlat;
      placeMarker("end", lonlat, "Substation");
    }
  }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

  // Right-drag looks around from where the camera is (heading/pitch in place) instead of zooming;
  // zoom stays on the wheel / pinch, tilt on middle-drag or Ctrl+drag.
  scene.screenSpaceCameraController.zoomEventTypes = [Cesium.CameraEventType.WHEEL, Cesium.CameraEventType.PINCH];
  scene.canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  let look = null;
  handler.setInputAction((e) => { stopFly(); look = { x: e.position.x, y: e.position.y }; }, Cesium.ScreenSpaceEventType.RIGHT_DOWN);
  handler.setInputAction(() => (look = null), Cesium.ScreenSpaceEventType.RIGHT_UP);

  // mouse move: right-drag look-around + elevation readout (throttled)
  let lastReadout = 0;
  handler.setInputAction((m) => {
    if (look) {
      const cam = viewer.camera, k = 0.0035; // radians per pixel
      const pitch = Cesium.Math.clamp(cam.pitch - (m.endPosition.y - look.y) * k, -Cesium.Math.PI_OVER_TWO + 0.01, 0.35);
      cam.setView({ orientation: { heading: cam.heading + (m.endPosition.x - look.x) * k, pitch, roll: 0 } });
      look = { x: m.endPosition.x, y: m.endPosition.y };
      return;
    }
    const now = performance.now();
    if (fly || now - lastReadout < 80) return;
    lastReadout = now;
    const ray = viewer.camera.getPickRay(m.endPosition);
    const cart = ray && globe.pick(ray, scene);
    if (!cart) { readoutEl.hidden = true; return; }
    const c = Cesium.Cartographic.fromCartesian(cart);
    readoutEl.hidden = false;
    const ll = `${Cesium.Math.toDegrees(c.latitude).toFixed(4)}, ${Cesium.Math.toDegrees(c.longitude).toFixed(4)}`;
    readoutEl.textContent = state.view === "enhanced"  // Top View is flat: no meaningful elevation to show
      ? `${ll} · elevation ${Math.round(c.height / scene.verticalExaggeration).toLocaleString()} m` : ll;
  }, Cesium.ScreenSpaceEventType.MOUSE_MOVE);

  // Esc stops a fly-through; preventDefault tells page handlers the key was used.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && fly) { stopFly(); e.preventDefault(); }
  });

  // ------------------------------------------------------------------ boot
  if (!hasToken) toast("No Cesium ion token in config.js — using flat terrain + OSM imagery.", "info", 7000);
  let bounds;
  try {
    // capped so a hung backend falls back to offline mode instead of never resolving createMap
    bounds = await getJson("/bounds", { signal: AbortSignal.timeout(8000) });
  } catch (err) {
    bounds = { bbox: state.bbox, synthetic_layers: [], region_name: null, offline: true };
  }
  state.bbox = bounds.bbox;
  if (!bounds.offline) toggleOverlay("substations", true);
  drawRegion();
  const ready = applyView();
  homeView();

  return {
    viewer, ready,
    bounds: () => bounds,
    showRoute, removeRoute, clearRoutes, selectRoute, routeColor,
    setFlow: (on) => { flowOn = !!on; syncFlow(); },
    setPickMode: (on) => { state.pickMode = !!on; },
    placeMarker, clearMarkers, frameRoute, homeView,
    setView, getView: () => state.view,
    setExaggeration, getExaggeration: () => state.exaggeration,
    toggleOverlay,
    startFly, stopFly, onFlyProgress: (cb) => { flyListeners.push(cb); },
    showValidation, toast,
  };
}
