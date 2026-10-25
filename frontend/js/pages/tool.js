/* Legacy tool page (tool.html) on the map.js / panel.js modules: proof the refactor matches app.js. */
import { createMap, getJson } from "../map.js";
import { renderResults } from "../panel.js";

const $ = (id) => document.getElementById(id);
const ui = { start: null, end: null, backendOk: false };

function setStatus(msg, busy = false) {
  $("status").textContent = msg;
  $("status").className = "status" + (busy ? " busy" : "");
}

function updateSteps() {
  $("step1").className = "step " + (ui.start ? "done" : "active");
  $("step2").className = "step " + (ui.end ? "done" : ui.start ? "active" : "");
}

const map = await createMap($("cesiumContainer"), {
  onPick(kind, lonlat) {
    if (!ui.backendOk) {
      map.toast("Backend is offline — click “Load demo route” to see a precomputed result.");
      return false;
    }
    if (kind === "start") {
      reset();
      ui.start = lonlat;
      setStatus("Now click the substation.");
    } else {
      ui.end = lonlat;
      requestRoute();
    }
    updateSteps();
  },
  onStatus: setStatus,
  onFlyChange: (on) => ($("btnFly").textContent = on ? "■ Stop fly-through" : "▶ Fly along route"),
});

function reset() {
  ui.start = ui.end = null;
  map.clearRoutes();
  map.clearMarkers();
  $("results").hidden = true;
  $("validation").hidden = true;
  updateSteps();
  setStatus(ui.backendOk ? "Click on the map to place the project site." : "Backend offline — use “Load demo route”.");
}

function showRoute(fc, isDemo = false) {
  $("validation").hidden = true;
  map.showRoute(fc);
  ui.start = fc.properties.start;
  ui.end = fc.properties.end;
  updateSteps();
  $("results").hidden = false;
  renderResults($("results"), fc.properties, fc.features, { isDemo });
}

async function requestRoute() {
  map.setPickMode(false); // ignore clicks while routing
  setStatus("Computing least-cost route…", true);
  try {
    const fc = await getJson("/route", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start: ui.start, end: ui.end }),
    });
    showRoute(fc);
    setStatus(`Route computed in ${fc.properties.compute_ms} ms. Click the map to start a new one.`);
  } catch (err) {
    map.toast(`Routing failed: ${err.message}. Try “Load demo route”.`);
    setStatus("Routing failed.");
  } finally {
    map.setPickMode(true);
  }
}

function syncView() {
  const enh = map.getView() === "enhanced";
  $("vTop").classList.toggle("on", !enh);
  $("vEnh").classList.toggle("on", enh);
  $("exagRow").hidden = !enh;
  $("exag").value = String(map.getExaggeration());
}

// ------------------------------------------------------------------ UI wiring
$("btnReset").onclick = () => { reset(); map.homeView(1.2); };
$("btnDemo").onclick = async () => {
  try {
    const r = await fetch("demo_route.json");
    if (!r.ok) throw new Error(r.statusText);
    showRoute(await r.json(), true);
    setStatus("Showing precomputed demo route.");
  } catch (err) {
    map.toast(`Could not load demo_route.json: ${err.message}`);
  }
};
$("btnValidate").onclick = async () => {
  const v = await map.showValidation();
  if (!v) return;
  ui.start = v.route.properties.start;
  ui.end = v.route.properties.end;
  updateSteps();
  $("results").hidden = false;
  renderResults($("results"), v.route.properties, v.route.features, { isDemo: false });
  $("validation").hidden = false;
  $("valLabel").textContent = v.label;
  $("valRows").innerHTML = v.rows;
  $("valNote").innerHTML = v.note;
  $("btnValidate").textContent = `Compare to real line (${(v.index + 1) % v.count + 1}/${v.count} next)`;
  setStatus("Magenta = real existing line; solid/dashed = our route with that line hidden.");
};
const toggles = { tglFire: "fire", tglHftd: "hftd", tglProtected: "protected", tglTribal: "tribal", tglLines: "lines",
                  tglBaseline: "baseline", tglContours: "contours", tglCities: "cities", tglBorders: "borders" };
for (const [id, name] of Object.entries(toggles)) $(id).onchange = (e) => map.toggleOverlay(name, e.target.checked);
$("vTop").onclick = () => { map.setView("top"); syncView(); };
$("vEnh").onclick = () => { map.setView("enhanced"); syncView(); };
$("btnFly").onclick = () => map.startFly();
$("exag").onchange = (ev) => map.setExaggeration(Number(ev.target.value));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !e.defaultPrevented) reset(); // map.js already used Esc to stop a fly-through
});

// ------------------------------------------------------------------ boot
const b = map.bounds();
ui.backendOk = !b.offline;
if (ui.backendOk) {
  $("region").textContent = b.region_name;
  const badge = $("dataBadge");
  if (b.synthetic_layers.length) {
    badge.textContent = `SYNTHETIC data: ${b.synthetic_layers.join(", ")}`;
    badge.className = "badge warn";
  } else {
    badge.textContent = "Real data · 3DEP · PAD-US · USFS WHP · CPUC HFTD · CEC · OSM";
    badge.className = "badge";
  }
  if ($("tglLines").checked) map.toggleOverlay("lines", true);
} else {
  $("region").textContent = "Backend offline";
  $("dataBadge").textContent = "Demo mode — precomputed route only";
  $("dataBadge").className = "badge err";
}
map.toggleOverlay("baseline", $("tglBaseline").checked);
if ($("tglCities").checked) map.toggleOverlay("cities", true);
if ($("tglBorders").checked) map.toggleOverlay("borders", true);
syncView();
reset();
map.setPickMode(true);
window.__map = map;
window.gentie = { viewer: map.viewer }; // handle for the browser console
map.ready.then(() => (document.documentElement.dataset.ready = "1"));
