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
    line: Cesium.Color.fromCssColorString("#ffe14d"),
    fire: { 1: "#ffd23f", 2: "#ff6b35", 3: "#e63030" },
    prot: { 1: "#9be3b5", 2: "#2fbf71" },
    tribal: "#e0a458",
  };

  // ------------------------------------------------------------------ viewer
  if (hasToken) Cesium.Ion.defaultAccessToken = cfg.CESIUM_ION_TOKEN;
  const viewer = new Cesium.Viewer("cesiumContainer", {
    terrain: hasToken ? Cesium.Terrain.fromWorldTerrain() : undefined,
    baseLayer: hasToken
      ? Cesium.ImageryLayer.fromWorldImagery()
      : new Cesium.ImageryLayer(new Cesium.OpenStreetMapImageryProvider({ url: "https://tile.openstreetmap.org/" })),
    baseLayerPicker: false, geocoder: false, homeButton: false, sceneModePicker: false,
    navigationHelpButton: false, animation: false, timeline: false, fullscreenButton: false,
    infoBox: false, selectionIndicator: false,
  });
  viewer.scene.globe.depthTestAgainstTerrain = true;
  viewer.scene.globe.enableLighting = false;
  viewer.screenSpaceEventHandler.removeInputAction(Cesium.ScreenSpaceEventType.LEFT_DOUBLE_CLICK);

  const layers = {
    route: new Cesium.CustomDataSource("route"),
    markers: new Cesium.CustomDataSource("markers"),
    baseline: new Cesium.CustomDataSource("baseline"),
    region: new Cesium.CustomDataSource("region"),
  };
  Object.values(layers).forEach((ds) => viewer.dataSources.add(ds));
  const overlays = {}; // name -> GeoJsonDataSource (lazy)

  // ------------------------------------------------------------------ state
  const state = { bbox: cfg.FALLBACK_BBOX || [-116.89, 32.94, -116.56, 33.21], start: null, end: null,
                  busy: false, backendOk: false };

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
  }

  // Tilted, north-up view of a lon/lat box, nudged so it sits right of the side panel.
  function frameBox([w, s, e, n], duration = 1.5) {
    const cy = (s + n) / 2;
    const halfDiag = Math.max(
      Cesium.Cartesian3.distance(Cesium.Cartesian3.fromDegrees(w, s), Cesium.Cartesian3.fromDegrees(e, n)) / 2, 2500);
    const range = halfDiag * 2.5;
    // the panel covers ~1/4 of the screen; visible ground width ~ 2*range*tan(30deg)
    const shiftM = window.innerWidth > 720 ? (350 / window.innerWidth) * range * Math.tan(Math.PI / 6) : 0;
    const shiftDeg = shiftM / (111320 * Math.cos(Cesium.Math.toRadians(cy)));
    const target = Cesium.Cartesian3.fromDegrees((w + e) / 2 - shiftDeg, cy, 800);
    viewer.camera.flyToBoundingSphere(new Cesium.BoundingSphere(target, halfDiag), {
      offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-45), range), duration,
    });
  }

  const homeView = (duration = 0) => frameBox(state.bbox, duration);

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
      if (ent.polyline) {
        ent.polyline.material = COLORS.line.withAlpha(0.9);
        ent.polyline.width = 2.5;
      }
      if (ent.billboard) { // substations: replace default pin with a labelled marker
        ent.billboard = undefined;
        ent.point = new Cesium.PointGraphics({
          pixelSize: 11, color: COLORS.end, outlineColor: Cesium.Color.WHITE, outlineWidth: 2,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND, disableDepthTestDistance: Number.POSITIVE_INFINITY,
        });
        ent.label = new Cesium.LabelGraphics({
          text: `${props.name}${props.voltage ? ` (${Math.round(+props.voltage / 1000)} kV)` : ""}`,
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
    } catch (err) {
      toast("Could not load wildfire hazard overlay (backend offline?)");
    }
  }

  async function toggleOverlay(name, on) {
    try {
      const ds = on ? await loadOverlay(name) : overlays[name];
      if (ds) ds.show = on;
    } catch (err) {
      toast(`Could not load ${name} overlay (backend offline?)`);
    }
  }

  // ------------------------------------------------------------------ routing
  function clearRoute() {
    layers.route.entities.removeAll();
    layers.baseline.entities.removeAll();
    $("results").hidden = true;
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
    const towerPositions = [];
    const box = [Infinity, Infinity, -Infinity, -Infinity];
    for (const f of fc.features) {
      const p = f.properties;
      if (p.kind === "segment") {
        const positions = Cesium.Cartesian3.fromDegreesArray(f.geometry.coordinates.flat());
        for (const [lon, lat] of f.geometry.coordinates) {
          box[0] = Math.min(box[0], lon); box[1] = Math.min(box[1], lat);
          box[2] = Math.max(box[2], lon); box[3] = Math.max(box[3], lat);
        }
        const oh = p.mode === "overhead";
        layers.route.entities.add({
          polyline: {
            positions, clampToGround: true, width: 5,
            material: oh ? COLORS.oh : new Cesium.PolylineDashMaterialProperty({ color: COLORS.ug, dashLength: 14 }),
          },
        });
      } else if (p.kind === "towers") {
        for (const [lon, lat] of f.geometry.coordinates) towerPositions.push(Cesium.Cartesian3.fromDegrees(lon, lat));
      } else if (p.kind === "baseline") {
        layers.baseline.entities.add({
          polyline: {
            positions: Cesium.Cartesian3.fromDegreesArray(f.geometry.coordinates.flat()),
            clampToGround: true, width: 2, material: COLORS.baseline,
          },
        });
      }
    }
    for (const position of towerPositions) {
      layers.route.entities.add({
        position,
        billboard: {
          image: TOWER_IMG, verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
          scaleByDistance: new Cesium.NearFarScalar(1.5e3, 1.0, 4e4, 0.45),
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
      });
    }
    layers.baseline.show = $("tglBaseline").checked;

    const props = fc.properties;
    state.start = props.start;
    state.end = props.end;
    placeMarker("start", props.start, "Project site");
    placeMarker("end", props.end, "Substation");
    updateSteps();
    renderPanel(props, fc.features, isDemo);

    if (Number.isFinite(box[0])) frameBox(box, 1.6);
  }

  // ------------------------------------------------------------------ panel
  function renderPanel(p, features, isDemo) {
    const b = p.baseline;
    $("results").hidden = false;
    $("demoTag").hidden = !isDemo;
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
    $("profEnd").textContent = `${dMax.toFixed(1)} km · ${Math.round(zMin)}–${Math.round(zMax)} m`;
  }

  // ------------------------------------------------------------------ clicks
  const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
  handler.setInputAction((click) => {
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
  $("tglFire").onchange = (e) => toggleRisk(e.target.checked);
  $("tglHftd").onchange = (e) => toggleOverlay("fire", e.target.checked);
  $("tglProtected").onchange = (e) => toggleOverlay("protected", e.target.checked);
  $("tglTribal").onchange = (e) => toggleOverlay("tribal", e.target.checked);
  $("tglLines").onchange = (e) => toggleOverlay("powerlines", e.target.checked);
  $("tglBaseline").onchange = (e) => (layers.baseline.show = e.target.checked);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") reset(); });

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
        badge.textContent = "Real data · 3DEP · PAD-US · USFS WHP · CPUC HFTD · OSM";
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
    homeView();
    reset();
  }
  boot();
  window.gentie = { viewer, state }; // handle for the browser console / smoke test
})();
