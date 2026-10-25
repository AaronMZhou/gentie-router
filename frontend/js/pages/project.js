/* Project dashboard: every candidate route of one project on the Cesium map, plus a route-card rail. */
import { initThemeToggle, onThemeChange, cssVar } from "../theme.js";
import { getProject, updateProject, deleteProject, addRoute, getRoute, renameRoute, deleteRoute, getAssumptions } from "../api.js";
import { estimate, rankRoutes } from "../estimates.js";
import { createMap } from "../map.js";

initThemeToggle(document.querySelector(".theme-toggle"));

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const KM_PER_MI = 1.609344;
const EST = '<span class="est" title="Placeholder estimate, not a real model">est.</span>';
const PRESET_KEYS = ["balanced", "avoid_fire", "cheapest", "underground"];
const money = (usd) => usd >= 1e9 ? `$${(usd / 1e9).toFixed(2)}B` : usd >= 1e8 ? `$${Math.round(usd / 1e6)}M`
  : usd >= 1e6 ? `$${(usd / 1e6).toFixed(1)}M` : `$${Math.round(usd / 1e3)}K`;
const quarter = (iso) => { const [y, m] = iso.split("-").map(Number); return `Q${Math.floor((m - 1) / 3) + 1} ${y}`; };
const errMsg = (e) => e?.detail || e?.message || String(e);

const projectId = Number(new URLSearchParams(location.search).get("id"));
const app = $("app");
let project = null;     // GET /projects/{id}
let routes = [];        // summary + { n, fc, est }
let presets = {};       // key -> { label, blurb }
let map = null;
let selected = null;    // route id
let mode = "view";      // view | pick-new | pick-change
let picks = [];         // lon/lat picked so far in a pick mode
let busy = "";          // rail status while the backend computes
let validating = false;
let places = { from: "Site", to: "Substation" };

// ------------------------------------------------------------------ states without a map
function emptyPage(title, text, action) {
  document.title = "Project · Gen-tie Router";
  app.innerHTML = `<div class="wrap" style="padding-top:48px"><div class="empty-state">
    <div class="display display--3">${title}</div><p class="muted">${esc(text)}</p>${action}</div></div>`;
}
const notFound = (text) => emptyPage('Project <span class="it">not found</span>', text,
  '<a class="btn" href="dashboard.html">← Back to projects</a>');

// ------------------------------------------------------------------ data
async function loadRoutes() {
  const full = await Promise.all(project.routes.map((r) => getRoute(r.id)));
  routes = project.routes.map((s, i) => ({ ...s, n: i + 1, fc: full[i].result }));
  reestimate();
}
function reestimate() {
  for (const r of routes) r.est = estimate(r.fc.properties, project);
  rankRoutes(routes.map((r) => r.est));
}
async function reload({ frame = false } = {}) {
  project = await getProject(projectId);
  await loadRoutes();
  draw({ frame });
  render();
}
// after a successful save: a failed reload must not read as a failed save
async function refresh(opts) {
  try {
    await reload(opts);
  } catch (e) {
    map.toast(`Saved, but couldn't reload the routes: ${errMsg(e)}. Refresh the page.`);
    render();
  }
}

// ------------------------------------------------------------------ map
function draw({ frame = false } = {}) {
  validating = false;
  if ($("valpop")) $("valpop").hidden = true;
  map.clearRoutes();
  map.clearMarkers(); // removeRoute leaves the P/S markers behind
  for (const r of routes) map.showRoute(r.fc, { id: r.id, selected: false, frame: false });
  if (!routes.some((r) => r.id === selected)) selected = (routes.find((r) => r.est.recommended) || routes[0])?.id ?? null;
  if (selected !== null) {
    map.selectRoute(selected);
    if (frame) map.frameRoute(selected);
  } else if (project.start_lonlat && project.end_lonlat) {
    map.placeMarker("start", project.start_lonlat, "Project site");
    map.placeMarker("end", project.end_lonlat, "Substation");
  }
}

function select(id, { frame = false } = {}) {
  if (!routes.some((r) => r.id === id)) return; // e.g. the validation route
  if (validating) closeValidation();
  selected = id;
  map.selectRoute(id);
  if (frame) map.frameRoute(id);
  for (const c of document.querySelectorAll("#rail .rcard")) {
    const on = Number(c.dataset.id) === id;
    c.classList.toggle("is-selected", on);
    c.setAttribute("aria-pressed", String(on));
  }
  renderLegend();
}

function startPick(kind) {
  mode = kind;
  picks = [];
  $("tools").open = false; // the toolbar HUD would swallow clicks on the map
  map.setPickMode(true);
  render();
}
function cancelPick() {
  mode = "view";
  picks = [];
  map.setPickMode(false);
  draw(); // the first pick cleared the routes off the map
  render();
}

// sync: map.js only honours a literal `false` veto
function onPick(kind, lonlat) {
  if (mode === "view" || busy) return false;
  if (kind === "start") { picks = [lonlat]; renderRail(); return; }
  if (!picks.length) return false;
  picks.push(lonlat);
  map.setPickMode(false);
  savePicks();
}

async function savePicks() {
  const [start_lonlat, end_lonlat] = picks;
  busy = mode === "pick-new" ? "Computing the first route…" : "Recomputing every route for the new endpoints…";
  renderRail();
  try {
    if (mode === "pick-new") selected = (await addRoute(projectId, { preset: "balanced", start_lonlat, end_lonlat })).route_summary.id;
    else await updateProject(projectId, { start_lonlat, end_lonlat });
  } catch (e) {
    busy = "";
    picks = [];
    map.setPickMode(true); // pick again from the project site
    map.toast(`Couldn't save the endpoints: ${errMsg(e)}`);
    renderRail();
    return;
  }
  mode = "view";
  busy = "";
  picks = [];
  await refresh({ frame: true });
  lookupPlaces();
}

// ------------------------------------------------------------------ rendering
function render() {
  document.title = `${project.name} · Gen-tie Router`;
  $("crumb-name").textContent = project.name;
  $("edit-project").hidden = false;
  const add = $("add-route");
  add.hidden = false;
  add.disabled = mode !== "view" || !!busy || !project.start_lonlat || !unusedPresets().length;
  add.title = unusedPresets().length ? "" : "Every strategy already has a route";
  renderBadge();
  renderLegend();
  renderRail();
}

function renderBadge() {
  const n = routes.length;
  $("badge").innerHTML = `<div class="mono-label">${esc(places.from)} → ${esc(places.to)} · ${project.voltage_kv} kV</div>
    <div class="display" style="font-size:28px;margin-top:4px">${n} candidate <span class="it">route${n === 1 ? "" : "s"}</span></div>`;
}

function routeColor(r) {
  const c = map.routeColor?.(r.id);
  if (c) return typeof c === "string" ? c : c.toCssColorString?.();
  // fallback (STYLES §6): selected = --route-1, the others --route-2..4 in order
  const others = routes.filter((o) => o.id !== selected);
  return cssVar(`--route-${r.id === selected ? 1 : Math.min(2 + others.indexOf(r), 4)}`);
}

function renderLegend() {
  $("legend").innerHTML = routes.map((r) =>
    `<span><i style="background:${routeColor(r)}"></i>Route ${r.n} · ${esc(r.name)}</span>`).join("") +
    `<span><i style="background:color-mix(in srgb, var(--acc2) 35%, transparent)"></i>Fire tier 3</span>
     <span><i style="background:color-mix(in srgb, var(--ok) 35%, transparent)"></i>Protected</span>`;
}

function card(r) {
  const e = r.est, p = r.fc.properties, m = e.is_placeholder ? EST : "";
  const [pill, cls] = e.recommended ? ["Recommended", "pill pill--blue"] : e.labels.cheapest_build ? ["Cheapest build", "pill"]
    : e.labels.fastest ? ["Fastest", "pill pill--ok"] : [esc(presets[r.preset]?.label || r.preset), "mono-label"];
  const ug = (p.underground_km || 0) > 0;
  const on = r.id === selected;
  return `<div class="rcard${on ? " is-selected" : ""}" data-id="${r.id}" tabindex="0" role="button" aria-pressed="${on}">
    <div class="top"><span class="${cls}">${pill}</span><div class="score${e.recommended ? " acc" : ""}">${e.score} ${e.is_placeholder ? '<span class="est" title="Placeholder score">est.</span>' : ""}</div></div>
    <h5>Route ${r.n} · <span class="it">${esc(r.name)}</span></h5>
    <div class="mm">
      <div>Upfront<b>${money(p.total_cost_usd)}</b></div>
      <div>Upkeep/yr<b>${money(e.maintenance_per_year)} ${m}</b></div>
      <div>Energize<b>${quarter(e.energize_date)} ${m}</b></div>
      <div>Length<b>${(p.total_km / KM_PER_MI).toFixed(1)} mi</b></div>
      ${ug ? `<div>Vaults<b>${p.inventory?.splice_vault_count_est ?? 0}</b></div>` : `<div>Towers<b>${p.tower_count}</b></div>`}
      <div>Permits<b>${(p.permitting_triggers || []).length}</b></div>
    </div>
    <div class="risk">▲ ${esc(e.top_risk)} ${m}</div>
    <div class="acts">
      <a class="btn btn--sm" href="route.html?id=${r.id}">Open →</a>
      <button class="btn btn--sm" type="button" data-rename="${r.id}">Rename</button>
      <button class="btn btn--sm" type="button" data-delete="${r.id}">Delete</button>
    </div>
  </div>`;
}

function renderRail() {
  const rail = $("rail");
  const status = busy ? `<div class="mono-label"><span class="dot"></span>&nbsp; ${esc(busy)}</div>` : "";
  if (mode !== "view") {
    const step = (i, text) => {
      const done = picks.length > i, active = picks.length === i && !busy;
      return `<p class="${active ? "acc" : "muted"}">${done ? "✓" : `${i + 1}.`} ${text}</p>`;
    };
    rail.innerHTML = `<div class="empty-state">
      <div class="display display--3">${mode === "pick-new" ? 'Draw the <span class="it acc">first route</span>' : 'New <span class="it acc">endpoints</span>'}</div>
      ${step(0, "Click the project site")}${step(1, "Then a substation (clicks snap to substations)")}
      ${status}
      ${mode === "pick-change" && !busy ? '<button class="btn" type="button" data-cancel-pick>Cancel</button>' : ""}
    </div>`;
    return;
  }
  const head = `<div class="rail-head"><div class="mono-label">${routes.length} route${routes.length === 1 ? "" : "s"} · sorted by score</div>
    <button class="btn btn--sm" type="button" data-change-endpoints ${busy ? "disabled" : ""}>Change endpoints</button></div>`;
  if (!routes.length) {
    rail.innerHTML = `${head}${status}<div class="empty-state"><div class="display display--3">No routes <span class="it">yet</span></div>
      <p class="muted">Add a strategy to compute a route between the saved endpoints.</p>
      <button class="btn" type="button" data-add ${busy ? "disabled" : ""}>+ Add route</button></div>`;
    return;
  }
  rail.innerHTML = head + status + [...routes].sort((a, b) => b.est.score - a.est.score || a.n - b.n).map(card).join("");
}

// ------------------------------------------------------------------ actions
const unusedPresets = () => {
  const used = new Set(routes.map((r) => r.preset));
  return (Object.keys(presets).length ? Object.keys(presets) : PRESET_KEYS).filter((k) => !used.has(k));
};

function openAdd() {
  const keys = unusedPresets();
  if (!keys.length || busy) return;
  $("add-choices").innerHTML = keys.map((k) =>
    `<button class="choice" type="button" data-preset="${k}"><b>${esc(presets[k]?.label || k)}</b><span class="muted">${esc(presets[k]?.blurb || "")}</span></button>`).join("");
  $("add-modal").showModal();
}

async function add(preset) {
  $("add-modal").close();
  busy = `Computing the ${presets[preset]?.label || preset} route…`;
  render();
  try {
    selected = (await addRoute(projectId, { preset })).route_summary.id;
  } catch (e) {
    busy = "";
    map.toast(`Couldn't add the route: ${errMsg(e)}`);
    render();
    return;
  }
  busy = "";
  await refresh({ frame: true });
}

let renaming = null;
function openRename(id) {
  renaming = routes.find((r) => r.id === id);
  const f = $("rename-modal").querySelector("form");
  f.name.value = renaming.name;
  $("rename-modal").showModal();
}

async function removeRoute(id) {
  const r = routes.find((x) => x.id === id);
  if (!confirm(`Delete route ${r.n} · ${r.name}? This can't be undone.`)) return;
  try {
    await deleteRoute(id);
  } catch (e) {
    map.toast(`Couldn't delete the route: ${errMsg(e)}`);
    return;
  }
  if (selected === id) selected = null;
  await refresh();
}

async function runValidation() {
  try {
    const v = await map.showValidation();
    if (!v) return;
    validating = true;
    $("valpop").hidden = false;
    $("val-label").textContent = v.label;
    $("val-rows").innerHTML = v.rows;
    $("val-note").innerHTML = v.note;
    $("validate").textContent = `Next case (${(v.index + 1) % v.count + 1}/${v.count})`;
    for (const c of document.querySelectorAll("#rail .rcard")) { c.classList.remove("is-selected"); c.setAttribute("aria-pressed", "false"); }
  } catch (e) {
    map.toast(`Validation failed: ${errMsg(e)}`);
  }
}
function closeValidation() {
  validating = false;
  $("valpop").hidden = true;
  $("validate").textContent = "Compare to real line";
  map.removeRoute("main");
  if (selected !== null) map.selectRoute(selected);
  else draw();
  renderRail();
}

function syncView() {
  const v = map.getView();
  for (const b of document.querySelectorAll("[data-view]")) b.setAttribute("aria-pressed", String(b.dataset.view === v));
  $("exag-row").hidden = v !== "enhanced";
  $("exag").value = String(map.getExaggeration());
}

// Badge names: the nearest town to the site and the nearest substation to the end, if close.
async function lookupPlaces() {
  const near = (feats, ll, maxKm) => {
    let best = null, bestKm = maxKm;
    for (const f of feats) {
      const [lon, lat] = f.geometry.coordinates;
      const km = Math.hypot((lon - ll[0]) * Math.cos(lat * Math.PI / 180), lat - ll[1]) * 111.32;
      if (km < bestKm) [best, bestKm] = [f.properties.name, km];
    }
    return best;
  };
  places = { from: "Site", to: "Substation" };
  if (!project.start_lonlat || !project.end_lonlat) return;
  try {
    const [cities, subs] = await Promise.all(["basemap/cities.geojson", `${window.APP_CONFIG?.API_BASE || ""}/overlays/substations`]
      .map((u) => fetch(u).then((r) => (r.ok ? r.json() : { features: [] }))));
    places = {
      from: near(cities.features, project.start_lonlat, 15) || "Site",
      to: (near(subs.features, project.end_lonlat, 2) || "Substation").replace(/ Substation$/, ""),
    };
  } catch { /* keep generic names */ }
  renderBadge();
}

// ------------------------------------------------------------------ wiring
function wire() {
  $("rail").addEventListener("click", (e) => {
    const t = e.target;
    if (t.closest("[data-rename]")) return openRename(Number(t.closest("[data-rename]").dataset.rename));
    if (t.closest("[data-delete]")) return removeRoute(Number(t.closest("[data-delete]").dataset.delete));
    if (t.closest("[data-add]")) return openAdd();
    if (t.closest("[data-cancel-pick]")) return cancelPick();
    if (t.closest("[data-change-endpoints]")) {
      if (confirm("Pick new endpoints? Every route is recomputed for them.")) startPick("pick-change");
      return;
    }
    if (t.closest("a")) return;
    const c = t.closest(".rcard");
    if (c) select(Number(c.dataset.id), { frame: true });
  });
  $("rail").addEventListener("keydown", (e) => {
    const c = e.target.closest?.(".rcard");
    if (c && e.target === c && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); select(Number(c.dataset.id), { frame: true }); }
  });
  $("add-route").addEventListener("click", openAdd);
  $("add-choices").addEventListener("click", (e) => { const b = e.target.closest("[data-preset]"); if (b) add(b.dataset.preset); });

  const rf = $("rename-modal").querySelector("form");
  $("rename-cancel").addEventListener("click", () => $("rename-modal").close());
  rf.addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = renaming;
    $("rename-modal").close();
    try {
      r.name = (await renameRoute(r.id, rf.name.value.trim())).name;
      render();
    } catch (err) {
      map.toast(`Couldn't rename the route: ${errMsg(err)}`);
    }
  });

  const ef = $("edit-modal").querySelector("form");
  $("edit-project").addEventListener("click", () => {
    ef.name.value = project.name;
    ef.phase.value = project.phase;
    ef.filing_target.value = project.filing_target || "";
    $("edit-modal").showModal();
  });
  $("edit-cancel").addEventListener("click", () => $("edit-modal").close());
  ef.addEventListener("submit", async (e) => {
    e.preventDefault();
    $("edit-modal").close();
    try {
      project = await updateProject(projectId, { name: ef.name.value.trim(), phase: ef.phase.value, filing_target: ef.filing_target.value || null });
      reestimate(); // the filing target moves the placeholder calendar
      render();
    } catch (err) {
      map.toast(`Couldn't save the project: ${errMsg(err)}`);
    }
  });
  $("delete-project").addEventListener("click", async () => {
    if (!confirm(`Delete “${project.name}” and all its routes? This can't be undone.`)) return;
    $("edit-modal").close();
    try {
      await deleteProject(projectId);
      location.href = "dashboard.html";
    } catch (err) {
      map.toast(`Couldn't delete the project: ${errMsg(err)}`);
    }
  });

  for (const cb of document.querySelectorAll("[data-overlay]")) {
    cb.addEventListener("change", () => map.toggleOverlay(cb.dataset.overlay, cb.checked));
    if (cb.checked) map.toggleOverlay(cb.dataset.overlay, true);
  }
  for (const b of document.querySelectorAll("[data-view]")) {
    b.addEventListener("click", () => map.setView(b.dataset.view).then(syncView, (e) => map.toast(errMsg(e))));
  }
  $("exag").addEventListener("change", (e) => map.setExaggeration(Number(e.target.value)).catch((err) => map.toast(errMsg(err))));
  $("validate").addEventListener("click", runValidation);
  $("val-close").addEventListener("click", closeValidation);
  if (matchMedia("(max-width: 900px)").matches) $("tools").open = false;
  onThemeChange(renderLegend);
}

// ------------------------------------------------------------------ boot
async function boot() {
  if (!Number.isInteger(projectId) || projectId <= 0) return notFound("This link has no project id.");
  try {
    [project, presets] = await Promise.all([getProject(projectId), getAssumptions().then((a) => a.PRESETS || {}, () => ({}))]);
  } catch (e) {
    if (e.status === 404) return notFound(`There's no project ${projectId}. It may have been deleted.`);
    emptyPage('Backend <span class="it">unreachable</span>', `Couldn't load the project (${errMsg(e)}). Is the server running?`,
      '<button class="btn" type="button" data-retry>Retry</button>');
    app.querySelector("[data-retry]").addEventListener("click", () => boot().catch(() => {}));
    return;
  }
  app.replaceChildren($("proj-tpl").content.cloneNode(true));
  map = await createMap($("cesium"), { onPick, onRouteClick: (id) => select(id), panelOffset: 0 }); // the rail sits outside the map
  window.__map = map; // handle for smoke --eval checks and the console (module scope isn't visible)
  map.setFlow?.(true);
  wire();
  syncView();
  try {
    await loadRoutes();
  } catch (e) {
    routes = [];
    map.toast(`Couldn't load the routes: ${errMsg(e)}`);
  }
  draw({ frame: true });
  if (!project.start_lonlat || !project.end_lonlat) startPick("pick-new");
  else render();
  lookupPlaces();
  await map.ready.catch(() => {}); // terrain failures already fall back inside map.js
}

try {
  await boot();
} catch (e) {
  emptyPage('Something <span class="it">broke</span>', errMsg(e), '<a class="btn" href="dashboard.html">← Back to projects</a>');
}
document.documentElement.dataset.ready = "1";
