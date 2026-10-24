/* Gen-tie Router frontend: Cesium viewer, click-to-route, results panel. */
(() => {
  "use strict";

  const cfg = window.APP_CONFIG || {};
  const API = cfg.API_BASE || "";
  const hasToken = !!cfg.CESIUM_ION_TOKEN;
  const $ = (id) => document.getElementById(id);

  const COLORS = {
    oh: Cesium.Color.fromCssColorString("#ff8c1a"),
    ug: Cesium.Color.fromCssColorString("#19d3f0"),
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

  // ------------------------------------------------------------------ viewer
  if (hasToken) Cesium.Ion.defaultAccessToken = cfg.CESIUM_ION_TOKEN;
  // vertex normals -> sun shading of the terrain in Enhanced view
  const terrain = hasToken ? Cesium.Terrain.fromWorldTerrain({ requestVertexNormals: true, requestWaterMask: true }) : undefined;
  const viewer = new Cesium.Viewer("cesiumContainer", {
    terrain,
    baseLayer: hasToken
      ? Cesium.ImageryLayer.fromWorldImagery()
      : new Cesium.ImageryLayer(new Cesium.OpenStreetMapImageryProvider({ url: "https://tile.openstreetmap.org/" })),
    baseLayerPicker: false, geocoder: false, homeButton: false, sceneModePicker: false,
    navigationHelpButton: false, animation: false, timeline: false, fullscreenButton: false,
    infoBox: false, selectionIndicator: false,
  });
  const scene = viewer.scene, globe = scene.globe;
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

  const layers = {
    route: new Cesium.CustomDataSource("route"),        // underground lines, real line (both views)
    routeTop: new Cesium.CustomDataSource("routeTop"),  // overhead route geometry + flat tower icons
    routeEnh: new Cesium.CustomDataSource("routeEnh"),  // overhead line tower-to-tower (Enhanced)
    hardware: new Cesium.CustomDataSource("hardware"),  // 3D poles, crossarms, conductors (Enhanced)
    markers: new Cesium.CustomDataSource("markers"),
    baseline: new Cesium.CustomDataSource("baseline"),
    region: new Cesium.CustomDataSource("region"),
  };
  Object.values(layers).forEach((ds) => viewer.dataSources.add(ds));
  const overlays = {}; // name -> GeoJsonDataSource (lazy)

  // ------------------------------------------------------------------ state
  const state = { bbox: cfg.FALLBACK_BBOX || [-116.89, 32.94, -116.56, 33.21], start: null, end: null,
                  busy: false, backendOk: false, view: "enhanced", route: null };
  try { if (localStorage.getItem("gentie.view") === "top") state.view = "top"; } catch (_) { /* storage blocked */ }
  const EXAGGERATIONS = [1, 1.5, 2, 3];
  state.exaggeration = 1.5; // Enhanced view terrain height scale (dropdown)
  try {
    const saved = Number(localStorage.getItem("gentie.exaggeration"));
    if (EXAGGERATIONS.includes(saved)) state.exaggeration = saved;
  } catch (_) { /* storage blocked */ }

  // Enhanced view
  const TOWER_H = 26;               // conductor attachment height above ground (m)
  const PHASE_OFFSETS = [-5, 0, 5]; // conductor positions along the crossarm (m)
  const SAG_FRAC = 0.025;           // mid-span sag as a fraction of span length
  const NEAR = new Cesium.DistanceDisplayCondition(0, 9000); // 3D hardware only when zoomed in
  const M_PER_DEG = 111320;

  // ------------------------------------------------------------------ helpers
  const fmtUsd = (x) => (Math.abs(x) >= 1e6 ? `$${(x / 1e6).toFixed(1)}M` : `$${Math.round(x / 1e3)}k`);
  const fmtKm = (x) => `${x.toFixed(x < 10 ? 2 : 1)} km`;

  function toast(msg, kind = "error", ms = 5000) {
    const t = $("toast");
    t.textContent = msg;
    t.className = kind === "info" ? "info" : "";
    t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => (t.hidden = true), ms);
  }

  function setStatus(msg, busy = false) {
    $("status").textContent = msg;
    $("status").className = "status" + (busy ? " busy" : "");
  }

  function updateSteps() {
    $("step1").className = "step " + (state.start ? "done" : "active");
    $("step2").className = "step " + (state.end ? "done" : state.start ? "active" : "");
  }

  async function getJson(path, opts) {
    const r = await fetch(API + path, opts);
    if (!r.ok) {
      let detail = r.statusText;
      try { detail = (await r.json()).detail || detail; } catch (_) { /* not json */ }
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return r.json();
  }

  function inBbox([lon, lat]) {
    const [w, s, e, n] = state.bbox;
    return lon >= w && lon <= e && lat >= s && lat <= n;
  }

  // small-area geometry on [lon, lat] (equirectangular - fine at this scale)
  const kLon = (lat) => Math.cos(Cesium.Math.toRadians(lat));
  const bearingOf = (a, b) => Math.atan2((b[0] - a[0]) * kLon((a[1] + b[1]) / 2), b[1] - a[1]); // rad, clockwise from north
  const distM = (a, b) => Math.hypot((b[0] - a[0]) * kLon((a[1] + b[1]) / 2), b[1] - a[1]) * M_PER_DEG;
  const offsetLL = ([lon, lat], dEast, dNorth) => [lon + dEast / (M_PER_DEG * kLon(lat)), lat + dNorth / M_PER_DEG];

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

  // Small lattice-tower glyph for overhead structures.
  const TOWER_IMG = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="40" viewBox="0 0 24 40">
      <g fill="none" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 3 L5 37 M12 3 L19 37 M3 11 H21 M5.5 18 H18.5 M7.5 25 L16.5 33 M16.5 25 L7.5 33 M7 25 H17"
              stroke="#1a1a1a" stroke-width="4"/>
        <path d="M12 3 L5 37 M12 3 L19 37 M3 11 H21 M5.5 18 H18.5 M7.5 25 L16.5 33 M16.5 25 L7.5 33 M7 25 H17"
              stroke="#ff8c1a" stroke-width="2"/>
      </g></svg>`);

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

  function frameRoute(duration = 1.6) {
    const r = state.route;
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

  async function toggleOverlay(name, on) {
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

  // ------------------------------------------------------------------ basemap context
  // Country/state borders and city labels: static files from data/prepare_basemap.py, so they also
  // show in demo mode.  Cities appear by tier as you zoom in: metros always, cities < 250 km, towns < 60 km.
  const CITY_STYLE = {
    metro: { font: "700 15px system-ui, sans-serif", size: 9, far: Number.POSITIVE_INFINITY },
    city: { font: "600 13px system-ui, sans-serif", size: 7, far: 250e3 },
    town: { font: "12px system-ui, sans-serif", size: 5, far: 60e3 },
  };
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

  // Render-on-demand doesn't notice async work finishing (ground polylines, overlays, tiles for
  // newly added entities), so request frames for a short while after changing the scene.
  function redraw(ms = 2500) {
    const until = performance.now() + ms;
    const pump = () => { scene.requestRender(); if (performance.now() < until) setTimeout(pump, 120); };
    pump();
  }

  // Switching views keeps the camera where it is; only the rendering changes.
  async function applyView() {
    const enh = state.view === "enhanced";
    layers.routeTop.show = !enh;
    layers.routeEnh.show = layers.hardware.show = enh;
    $("vTop").classList.toggle("on", !enh);
    $("vEnh").classList.toggle("on", enh);
    $("exagRow").hidden = !enh;
    $("exag").value = String(state.exaggeration);
    $("readout").hidden = true;
    try { localStorage.setItem("gentie.view", state.view); } catch (_) { /* storage blocked */ }
    await applyViewSettings();
    if (enh) liftCameraAboveTerrain();
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

  // ------------------------------------------------------------------ routing
  function clearRoute() {
    stopFly();
    state.route = null;
    state.routeSeq = (state.routeSeq || 0) + 1; // invalidates any pending 3D build
    for (const ds of [layers.route, layers.routeTop, layers.routeEnh, layers.hardware, layers.baseline]) ds.entities.removeAll();
    $("results").hidden = true;
    $("validation").hidden = true;
    redraw(600);
  }

  function reset() {
    state.start = state.end = null;
    clearRoute();
    layers.markers.entities.removeAll();
    updateSteps();
    setStatus(state.backendOk ? "Click on the map to place the project site." : "Backend offline — use “Load demo route”.");
  }

  async function requestRoute() {
    state.busy = true;
    setStatus("Computing least-cost route…", true);
    try {
      const fc = await getJson("/route", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start: state.start, end: state.end }),
      });
      showRoute(fc);
      setStatus(`Route computed in ${fc.properties.compute_ms} ms. Click the map to start a new one.`);
    } catch (err) {
      toast(`Routing failed: ${err.message}. Try “Load demo route”.`);
      setStatus("Routing failed.");
    } finally {
      state.busy = false;
    }
  }

  function showRoute(fc, isDemo = false) {
    clearRoute();
    let towerLL = [];
    const segments = [];
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
          layers.routeTop.entities.add({ polyline: { positions, clampToGround: true, width: 5, material: COLORS.oh } });
        } else {
          layers.route.entities.add({ polyline: {
            positions, clampToGround: true, width: 5,
            material: new Cesium.PolylineDashMaterialProperty({ color: COLORS.ug, dashLength: 14 }),
          } });
        }
      } else if (p.kind === "towers") {
        towerLL = f.geometry.coordinates;
      } else if (p.kind === "baseline") {
        layers.baseline.entities.add({
          polyline: {
            positions: Cesium.Cartesian3.fromDegreesArray(f.geometry.coordinates.flat()),
            clampToGround: true, width: 2, material: COLORS.baseline,
          },
        });
      }
    }
    for (const [lon, lat] of towerLL) {
      layers.routeTop.entities.add({
        position: Cesium.Cartesian3.fromDegrees(lon, lat),
        billboard: {
          image: TOWER_IMG, verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
          scaleByDistance: new Cesium.NearFarScalar(1.5e3, 1.0, 4e4, 0.45),
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
      });
    }
    layers.baseline.show = $("tglBaseline").checked;

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
      layers.routeEnh.entities.add({ polyline: {
        positions: Cesium.Cartesian3.fromDegreesArray(run.flatMap((i) => towerLL[i])), clampToGround: true,
        width: 4, material: COLORS.oh.withAlpha(0.85),
      } });
    }

    const props = fc.properties;
    state.start = props.start;
    state.end = props.end;
    state.route = { start: props.start, end: props.end, box, segments, towerLL, runs, towerBearing };
    placeMarker("start", props.start, "Project site");
    placeMarker("end", props.end, "Substation");
    updateSteps();
    renderPanel(props, fc.features, isDemo);
    buildHardware(state.routeSeq);
    redraw();

    if (Number.isFinite(box[0])) frameRoute(1.6);
  }

  // 3D poles + crossarms + three sagging conductors per span.  Positions are absolute heights
  // (sampled terrain x exaggeration + attachment height) rather than RELATIVE_TO_GROUND, so the
  // entities don't re-query terrain every time a tile loads (that made the fly-through stutter).
  async function buildHardware(seq) {
    const r = state.route;
    if (!r || !r.towerLL.length) return;
    if (!r.towerHeights) { // sample real terrain once per route; rebuilds (new exaggeration) reuse it
      await terrainReady;
      const carto = r.towerLL.map(([lon, lat]) => Cesium.Cartographic.fromDegrees(lon, lat));
      try { if (worldTerrain) await Cesium.sampleTerrainMostDetailed(worldTerrain, carto); } catch (_) { /* flat fallback */ }
      if (seq !== state.routeSeq) return; // a newer route replaced this one
      r.towerHeights = carto.map((c) => c.height || 0);
    }
    layers.hardware.entities.removeAll();
    const e = state.exaggeration;
    const ground = r.towerHeights.map((h) => h * e);
    const steel = Cesium.Color.fromCssColorString("#c9ced4");
    const wire = Cesium.Color.fromCssColorString("#2b2f33").withAlpha(0.9);
    const ents = layers.hardware.entities;
    ents.suspendEvents();
    r.towerLL.forEach(([lon, lat], i) => {
      const at = (h) => Cesium.Cartesian3.fromDegrees(lon, lat, ground[i] + h);
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
  async function startFly() {
    if (fly) return stopFly();
    const r = state.route;
    if (!r) return;
    setStatus("Preparing fly-through…", true);
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
    if (pts.length < 3) return setStatus("");

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

    const km = (N - 1) * STEP / 1000;
    const duration = Math.min(Math.max(km * 3, 20), 60); // seconds
    scene.requestRenderMode = false; // the camera is driven from preRender, so render every frame

    const t0 = performance.now();
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
      if (u >= 1) stopFly();
    };
    scene.preRender.addEventListener(tick);
    fly = { tick };
    $("btnFly").textContent = "■ Stop fly-through";
    setStatus(`Flying along the route (${Math.round(duration)} s) — click or Esc to stop.`);
  }

  function stopFly() {
    if (!fly) return;
    scene.preRender.removeEventListener(fly.tick);
    fly = null;
    scene.requestRenderMode = true;
    redraw(500);
    $("btnFly").textContent = "▶ Fly along route";
    setStatus("");
  }

  // ------------------------------------------------------------------ panel
  function renderPanel(p, features, isDemo) {
    const b = p.baseline;
    $("results").hidden = false;
    $("demoTag").hidden = !isDemo;
    $("demoStory").hidden = !isDemo;
    if (isDemo) renderDemoStory(p, features);
    $("rCost").textContent = fmtUsd(p.total_cost_usd);
    $("rKm").textContent = fmtKm(p.total_km);
    $("rOh").textContent = fmtKm(p.overhead_km);
    $("rUg").textContent = fmtKm(p.underground_km);
    $("rRisk").textContent = fmtKm(p.high_risk_km);
    $("rOhRisk").textContent = fmtKm(p.overhead_high_risk_km);
    $("rFire").textContent = fmtKm(p.high_fire_km);
    $("rMeanRisk").textContent = p.mean_fire_risk.toFixed(2);
    $("rProt").textContent = p.protected_cells_crossed
      ? `${fmtKm(p.protected_km)} (${p.protected_cells_crossed} cells)` : "none";
    $("rTrans").textContent = `${p.n_transitions} (${fmtUsd(p.transition_cost_usd)})`;
    const tribalList = (lands) => (lands || []).map((t) => `${t.name} (${fmtKm(t.km)})`).join(", ");
    $("rTribal").textContent = p.tribal_km ? fmtKm(p.tribal_km) : "none";
    $("tribalNote").hidden = !p.tribal_km;
    $("tribalNote").textContent = p.tribal_km
      ? `Crosses ${tribalList(p.tribal_lands_crossed)} — triggers tribal consultation (NHPA §106) and requires tribal consent for right-of-way (25 CFR 169).`
      : "";

    $("cLenR").textContent = fmtKm(p.total_km);
    $("cLenB").textContent = fmtKm(b.length_km);
    $("cCostR").textContent = fmtUsd(p.total_cost_usd);
    $("cCostB").textContent = fmtUsd(b.est_cost_usd);
    $("cProtR").textContent = p.protected_km ? fmtKm(p.protected_km) : "none";
    $("cProtB").textContent = b.protected_km ? fmtKm(b.protected_km) : "none";
    $("cTribR").textContent = p.tribal_km ? fmtKm(p.tribal_km) : "none";
    $("cTribB").textContent = b.tribal_km ? fmtKm(b.tribal_km) : "none";
    $("cFireR").textContent = fmtKm(p.overhead_high_risk_km);
    $("cFireB").textContent = fmtKm(b.high_risk_km);
    $("cRiskR").textContent = p.mean_fire_risk.toFixed(2);
    $("cRiskB").textContent = b.mean_fire_risk.toFixed(2);

    const notes = [];
    const delta = b.est_cost_usd - p.total_cost_usd;
    if (delta >= 0) notes.push(`<span class="good">Saves ${fmtUsd(delta)} (${Math.round((100 * delta) / b.est_cost_usd)}%)</span> vs. a straight overhead line.`);
    else notes.push(`Costs ${fmtUsd(-delta)} more than a straight overhead line…`);
    if (b.protected_km > 0) notes.push(`<span class="bad">Straight line crosses ${fmtKm(b.protected_km)} of protected land</span> — likely unpermittable.`);
    if (b.tribal_km > (p.tribal_km || 0)) notes.push(`<span class="bad">Straight line crosses tribal land:</span> ${tribalList(b.tribal_lands_crossed)}.`);
    const exposure = b.high_risk_km - p.overhead_high_risk_km;
    if (exposure > 0.05) notes.push(`Cuts overhead conductor in high wildfire hazard by ${fmtKm(exposure)}.`);
    $("cmpNote").innerHTML = notes.join("<br>");

    if (p.row && p.inventory) {
      $("rLandholders").textContent =
        `${p.row.landholder_negotiations_est} (${p.row.private_parcels} parcels${p.row.private_parcels_method.startsWith("est") ? " est." : ""} + ${p.row.public_agreements} agencies)`;
      $("rStructures").textContent = `${p.inventory.tower_count} towers · ${p.inventory.splice_vault_count_est} vaults`;
      $("triggers").innerHTML = (p.permitting_triggers || [])
        .map((t) => `<li><code>${t.code}</code>${t.reason}</li>`).join("");
    }
    renderProfile(p.profile || []);

    const ol = $("segments");
    ol.innerHTML = "";
    for (const f of features.filter((f) => f.properties.kind === "segment")) {
      const s = f.properties;
      const li = document.createElement("li");
      li.innerHTML = `<span class="seg-chip ${s.mode}"></span><span class="m">${s.mode}</span>
        <span class="muted">${fmtKm(s.length_km)}</span><span class="n">${fmtUsd(s.est_cost_usd)}</span>`;
      ol.appendChild(li);
    }
  }

  // Plain-language explanation of the demo route, built from its own numbers.
  function renderDemoStory(p, features) {
    const b = p.baseline, segs = features.filter((f) => f.properties.kind === "segment");
    const costOf = (mode) => segs.filter((f) => f.properties.mode === mode).reduce((t, f) => t + f.properties.est_cost_usd, 0);
    const perMile = p.total_cost_usd / (p.total_km / 1.609344);
    const detour = p.total_km - b.length_km;
    const why = [];
    if (b.high_risk_km > p.overhead_high_risk_km + 0.05) {
      why.push(`<b>Wildfire:</b> a straight line would string ${fmtKm(b.high_risk_km)} of overhead conductor through high wildfire
        hazard; this route has ${fmtKm(p.overhead_high_risk_km)} (average hazard ${p.mean_fire_risk.toFixed(2)} vs
        ${b.mean_fire_risk.toFixed(2)} on a 0–1 scale). It follows lower-hazard valleys instead of chaparral ridges.`);
    }
    if (b.protected_km > p.protected_km) {
      why.push(`<b>Protected land:</b> the straight line crosses ${fmtKm(b.protected_km)} of preserves; this route crosses
        ${p.protected_km ? fmtKm(p.protected_km) : "none"}.`);
    }
    if ((b.tribal_km || 0) > (p.tribal_km || 0)) {
      why.push(`<b>Tribal land:</b> the straight line crosses ${fmtKm(b.tribal_km)}; this route ${p.tribal_km ? fmtKm(p.tribal_km) : "avoids it"}.`);
    }
    why.push(`<b>Trade-off:</b> ${detour >= 0 ? `${fmtKm(detour)} (${Math.round((100 * detour) / b.length_km)}%) longer` : `${fmtKm(-detour)} shorter`}
      than a straight line, but estimated ${fmtUsd(Math.abs(b.est_cost_usd - p.total_cost_usd))}
      ${b.est_cost_usd >= p.total_cost_usd ? "cheaper" : "more expensive"} once hazard and land are priced in.`);
    const inv = p.inventory || {}, row = p.row || {};
    const rows = [
      [`Overhead line · ${fmtKm(p.overhead_km)}`, costOf("overhead")],
      ...(p.underground_km > 0 ? [[`Underground cable · ${fmtKm(p.underground_km)}`, costOf("underground")]] : []),
      ...(p.n_transitions ? [[`Overhead↔underground transitions · ${p.n_transitions}`, p.transition_cost_usd]] : []),
    ];
    $("demoStory").innerHTML = `
      <h4>About this demo: ${p.demo_title || p.demo_label || "example route"}</h4>
      <p>${p.demo_blurb || "A hypothetical example route, not a real proposed project."}</p>
      <h4>Estimated cost: ${fmtUsd(p.total_cost_usd)} <span class="muted">(≈ ${fmtUsd(perMile)} per mile)</span></h4>
      <table>
        ${rows.map(([k, v]) => `<tr><td>${k}</td><td>${fmtUsd(v)}</td></tr>`).join("")}
        <tr class="total"><td>Total · ${fmtKm(p.total_km)}</td><td>${fmtUsd(p.total_cost_usd)}</td></tr>
        <tr><td class="muted">Straight overhead line · ${fmtKm(b.length_km)}</td><td class="muted">${fmtUsd(b.est_cost_usd)}</td></tr>
      </table>
      <h4>Why it goes this way</h4>
      <ul>${why.map((w) => `<li>${w}</li>`).join("")}</ul>
      <h4>What it would take</h4>
      <ul>
        <li>${inv.tower_count ?? p.tower_count} overhead structures, ~${inv.conductor_km_est ?? "?"} km of conductor</li>
        ${row.landholder_negotiations_est ? `<li>~${row.landholder_negotiations_est} landholder agreements (${row.private_parcels} private parcels, estimated)</li>` : ""}
        ${(p.permitting_triggers || []).length ? `<li>Reviews: ${p.permitting_triggers.map((t) => t.code.replaceAll("_", " ")).join(", ")}</li>` : ""}
      </ul>
      <p class="caveat">Costs are planning-level estimates from the model's assumptions ($2M/mi overhead and $10M/mi
        underground base, scaled by slope, wildfire hazard, corridors and land) — not a quote or a real project's cost.
        Real costs could plausibly be 0.5–2× these.</p>`;
  }

  function renderProfile(profile) {
    const svg = $("profile");
    svg.innerHTML = "";
    if (profile.length < 2) return;
    const W = 300, H = 90, pad = 4;
    const dMax = profile[profile.length - 1][0] || 1;
    const zs = profile.map((q) => q[1]);
    const zMin = Math.min(...zs), zMax = Math.max(...zs), zr = Math.max(zMax - zMin, 1);
    const X = (d) => (d / dMax) * W;
    const Y = (z) => H - pad - ((z - zMin) / zr) * (H - 2 * pad);
    const ns = "http://www.w3.org/2000/svg";
    // terrain fill
    const area = document.createElementNS(ns, "path");
    area.setAttribute("d", `M0 ${H} ` + profile.map((q) => `L${X(q[0]).toFixed(1)} ${Y(q[1]).toFixed(1)}`).join(" ") + ` L${W} ${H} Z`);
    area.setAttribute("fill", "rgba(255,255,255,0.08)");
    svg.appendChild(area);
    // line coloured by mode
    for (let i = 1; i < profile.length; i++) {
      const [d0, z0] = profile[i - 1], [d1, z1, mode] = profile[i];
      const l = document.createElementNS(ns, "line");
      l.setAttribute("x1", X(d0)); l.setAttribute("y1", Y(z0));
      l.setAttribute("x2", X(d1)); l.setAttribute("y2", Y(z1));
      l.setAttribute("stroke", mode === "overhead" ? "#ff8c1a" : "#19d3f0");
      l.setAttribute("stroke-width", "2");
      l.setAttribute("vector-effect", "non-scaling-stroke");
      svg.appendChild(l);
    }
    let climb = 0, descent = 0;
    for (let i = 1; i < profile.length; i++) {
      const dz = profile[i][1] - profile[i - 1][1];
      if (dz > 0) climb += dz; else descent -= dz;
    }
    $("profEnd").textContent =
      `${dMax.toFixed(1)} km · ${Math.round(zMin)}–${Math.round(zMax)} m · +${Math.round(climb)} / −${Math.round(descent)} m`;
  }

  // ------------------------------------------------------------------ validation
  let validation = null, valIndex = -1;
  async function showValidation() {
    try {
      if (!validation) {
        const r = await fetch("validation.json");
        if (!r.ok) throw new Error(r.statusText);
        validation = await r.json();
      }
    } catch (err) {
      toast(`Could not load validation.json (run python -m backend.validate): ${err.message}`);
      return;
    }
    valIndex = (valIndex + 1) % validation.cases.length;
    const c = validation.cases[valIndex];
    showRoute(c.route, false);
    layers.route.entities.add({
      polyline: {
        positions: Cesium.Cartesian3.fromDegreesArray(c.real_line.geometry.coordinates.flat()),
        clampToGround: true, width: 4, material: COLORS.real.withAlpha(0.9),
      },
    });
    redraw();
    $("validation").hidden = false;
    $("valLabel").textContent = `${c.label} (${valIndex + 1}/${validation.cases.length}, real line ${c.metrics.real_km} km)`;
    const row = (name, m) => `<tr><td>${name}</td><td>${m.mean_dist_m} m</td><td>${m.pct_within_500m.toFixed(0)}%</td><td>${m.route_km} km</td></tr>`;
    $("valRows").innerHTML =
      row("Router (line hidden)", c.metrics) +
      row("Straight line", c.baseline_metrics) +
      row("Router (line known)", c.with_line_in_corridor_metrics) +
      row("Hidden + preserves passable", c.preserves_passable_metrics);
    const prot = (c.real_line_protected_crossed || []).map((u) => `${u.name} (${fmtKm(u.km)})`).join(", ");
    $("valNote").innerHTML =
      `Leave-one-out: the real line is removed from the corridor layer before routing.` +
      (prot ? `<br><span class="bad">Real line crosses preserves:</span> ${prot} — likely predating them; our model treats preserves as near-impassable for new right-of-way.` : "");
    $("btnValidate").textContent = `Compare to real line (${(valIndex + 1) % validation.cases.length + 1}/${validation.cases.length} next)`;
    setStatus("Magenta = real existing line; orange/cyan = our route with that line hidden.");
  }

  // ------------------------------------------------------------------ clicks
  const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
  handler.setInputAction(() => { if (fly) { state.flyStoppedAt = performance.now(); stopFly(); } },
    Cesium.ScreenSpaceEventType.LEFT_DOWN);
  handler.setInputAction((click) => {
    if (performance.now() - (state.flyStoppedAt || 0) < 600) return; // that click just stopped the fly-through
    if (state.busy) return;
    let lonlat = null;
    // snap to a substation marker if one was clicked
    const picked = viewer.scene.pick(click.position);
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
    if (!state.backendOk) {
      toast("Backend is offline — click “Load demo route” to see a precomputed result.");
      return;
    }
    if (!state.start || state.end) { // first click, or starting over after a finished route
      reset();
      state.start = lonlat;
      placeMarker("start", lonlat, "Project site");
      setStatus("Now click the substation.");
    } else {
      state.end = lonlat;
      placeMarker("end", lonlat, "Substation");
      requestRoute();
    }
    updateSteps();
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
    const out = $("readout");
    if (!cart) { out.hidden = true; return; }
    const c = Cesium.Cartographic.fromCartesian(cart);
    out.hidden = false;
    const ll = `${Cesium.Math.toDegrees(c.latitude).toFixed(4)}, ${Cesium.Math.toDegrees(c.longitude).toFixed(4)}`;
    out.textContent = state.view === "enhanced"  // Top View is flat: no meaningful elevation to show
      ? `${ll} · elevation ${Math.round(c.height / scene.verticalExaggeration).toLocaleString()} m` : ll;
  }, Cesium.ScreenSpaceEventType.MOUSE_MOVE);

  // ------------------------------------------------------------------ UI wiring
  $("btnReset").onclick = () => { reset(); homeView(1.2); };
  $("btnDemo").onclick = async () => {
    try {
      const r = await fetch("demo_route.json");
      if (!r.ok) throw new Error(r.statusText);
      showRoute(await r.json(), true);
      setStatus("Showing precomputed demo route.");
    } catch (err) {
      toast(`Could not load demo_route.json: ${err.message}`);
    }
  };
  $("btnValidate").onclick = showValidation;
  $("tglFire").onchange = (e) => toggleRisk(e.target.checked);
  $("tglHftd").onchange = (e) => toggleOverlay("fire", e.target.checked);
  $("tglProtected").onchange = (e) => toggleOverlay("protected", e.target.checked);
  $("tglTribal").onchange = (e) => toggleOverlay("tribal", e.target.checked);
  $("tglLines").onchange = (e) => toggleOverlay("powerlines", e.target.checked);
  $("tglBaseline").onchange = (e) => { layers.baseline.show = e.target.checked; redraw(600); };
  $("tglContours").onchange = (e) => toggleContours(e.target.checked);
  $("tglCities").onchange = (e) => toggleBasemap("cities", e.target.checked);
  $("tglBorders").onchange = (e) => toggleBasemap("borders", e.target.checked);
  $("vTop").onclick = () => { if (state.view !== "top") { stopFly(); state.view = "top"; applyView(); } };
  $("vEnh").onclick = () => { if (state.view !== "enhanced") { stopFly(); state.view = "enhanced"; applyView(); } };
  $("btnFly").onclick = startFly;
  $("exag").onchange = async (ev) => {
    stopFly();
    state.exaggeration = Number(ev.target.value);
    try { localStorage.setItem("gentie.exaggeration", String(state.exaggeration)); } catch (_) { /* storage blocked */ }
    await applyViewSettings();
    buildHardware(state.routeSeq); // towers/conductors sit on the rescaled terrain
    liftCameraAboveTerrain();
  };
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (fly) stopFly(); else reset();
  });

  // ------------------------------------------------------------------ boot
  async function boot() {
    if (!hasToken) toast("No Cesium ion token in config.js — using flat terrain + OSM imagery.", "info", 7000);
    try {
      const b = await getJson("/bounds");
      state.bbox = b.bbox;
      state.backendOk = true;
      $("region").textContent = b.region_name;
      const badge = $("dataBadge");
      if (b.synthetic_layers.length) {
        badge.textContent = `SYNTHETIC data: ${b.synthetic_layers.join(", ")}`;
        badge.className = "badge warn";
      } else {
        badge.textContent = "Real data · 3DEP · PAD-US · USFS WHP · CPUC HFTD · CEC · OSM";
        badge.className = "badge";
      }
      toggleOverlay("substations", true);
      if ($("tglLines").checked) toggleOverlay("powerlines", true);
    } catch (err) {
      state.backendOk = false;
      $("region").textContent = "Backend offline";
      $("dataBadge").textContent = "Demo mode — precomputed route only";
      $("dataBadge").className = "badge err";
    }
    drawRegion();
    if ($("tglCities").checked) toggleBasemap("cities", true);
    if ($("tglBorders").checked) toggleBasemap("borders", true);
    applyView();
    homeView();
    reset();
  }
  boot();
  window.gentie = { viewer, state }; // handle for the browser console / smoke test
})();
