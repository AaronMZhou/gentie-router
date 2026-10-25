import { initThemeToggle } from "../theme.js";
import { getRoute, getProject, updateProject, getAssumptions } from "../api.js";
import { estimate } from "../estimates.js";
import { renderResults, fmtUsd, fmtKm } from "../panel.js";
import { createMap } from "../map.js";

initThemeToggle(document.querySelector(".theme-toggle"));

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const KM_PER_MI = 1.609344;
const params = new URLSearchParams(location.search);
const app = $("app");
const ready = () => (document.documentElement.dataset.ready = "1");

const money = (usd) => usd >= 1e9 ? `$${(usd / 1e9).toFixed(2)}B` : usd >= 1e6 ? `$${Math.round(usd / 1e6)}M` : fmtUsd(usd);
const mi = (km) => (km / KM_PER_MI).toFixed(1);
const quarter = (iso) => `Q${Math.floor((Number(iso.slice(5, 7)) - 1) / 3) + 1} ${iso.slice(0, 4)}`;
const num = (x) => Math.round(x).toLocaleString("en-US");

let toastTimer;
function toast(msg, error = false) {
  const t = document.querySelector(".toast");
  t.textContent = msg;
  t.className = "toast" + (error ? " toast--error" : "");
  t.setAttribute("role", error ? "alert" : "status");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 5000);
}

// ------------------------------------------------------------------ data
async function load() {
  if (params.get("demo") === "1") { // offline demo: static file only, no API calls
    const r = await fetch("demo_route.json");
    if (!r.ok) throw new Error(`demo_route.json: ${r.statusText}`);
    return { fc: await r.json(), route: { id: null, name: "Demo" }, project: { id: null, name: "Demo", voltage_kv: 230 }, demo: true };
  }
  const id = Number(params.get("id"));
  if (!Number.isInteger(id) || id <= 0) return null;
  const route = await getRoute(id);
  const project = await getProject(route.project_id);
  return { fc: route.result, route, project, demo: false };
}

function emptyState(html) {
  app.innerHTML = `<div class="wrap" style="padding-top:48px;padding-bottom:48px"><div class="empty-state">${html}</div></div>`;
}

// ------------------------------------------------------------------ page sections
const PERMIT_LABELS = {
  CEQA: "CEQA review", FEDERAL_ROW_NEPA: "Federal ROW · NEPA", TRIBAL_CONSENT: "Tribal consent",
  PROTECTED_AREA: "Protected land", STATE_LANDS: "State lands", HFTD: "HFTD construction rules",
  ENCROACHMENT: "Road encroachment",
};
const UPFRONT_LABELS = {
  structures: "Towers & foundations", conductor: "Conductor & hardware", underground_cable: "Underground cable",
  right_of_way: "Right-of-way", fire_mitigation: "Fire mitigation", transitions: "OH/UG transitions",
};
const MAINT_LABELS = {
  overhead_lines: "Overhead line patrol & repair", underground_cable: "Underground cable",
  structures: "Structures", fire_vegetation: "Fire & vegetation mgmt",
};

function page({ fc, route, project, demo }, est) {
  const p = fc.properties;
  const mk = (title = "Placeholder estimate, not a real model") => est.is_placeholder ? `<span class="est" title="${esc(title)}">est.</span>` : "";
  const n = demo ? "Demo" : String((project.routes || []).findIndex((r) => r.id === route.id) + 1).padStart(2, "0");
  const modeWord = !p.underground_km ? "overhead" : !p.overhead_km ? "underground" : "hybrid";
  const towers = p.inventory?.tower_count ?? p.tower_count ?? 0;

  // donut: top three upfront categories + "other"
  const parts = Object.entries(est.upfront_breakdown).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const slices = parts.slice(0, 3).map(([k, v]) => [UPFRONT_LABELS[k] || k, v]);
  if (parts.length > 3) slices.push(["Other", parts.slice(3).reduce((a, [, v]) => a + v, 0)]);
  const total = est.upfront_total || 1;
  let cum = 0;
  const cuts = slices.map(([, v]) => (cum += (100 * v) / total)).map((c) => `${c.toFixed(1)}%`);
  const donutStyle = `--a:${cuts[0] || "100%"};--b:${cuts[1] || "100%"};--c:${cuts[2] || "100%"}`;
  const donutLabel = slices.map(([k, v]) => `${k} ${Math.round((100 * v) / total)}%`).join(", ");

  // inventory (real) + lead times (est.)
  const inv = p.inventory || {};
  const invRows = [
    ["Overhead structures", num(towers)],
    ["Conductor", `${num(inv.conductor_km_est || 0)} km`],
    ...(inv.cable_km_est ? [["Underground cable", `${num(inv.cable_km_est)} km`]] : []),
    ...(inv.splice_vault_count_est ? [["Splice vaults", num(inv.splice_vault_count_est)]] : []),
    ...(inv.underground_in_road_km ? [["Cable in road", fmtKm(inv.underground_in_road_km)]] : []),
    ["OH/UG transitions", num(inv.transition_count ?? p.n_transitions ?? 0)],
  ];
  const leads = [...est.lead_times].sort((a, b) => b.weeks - a.weeks);

  // maintenance: cumulative lifetime cost, years 0-40 (a straight line by construction: placeholder upkeep is flat)
  const y = (v) => 100 - (v / est.lifetime_cost_40yr) * 90;
  const spark = `M0 ${y(est.upfront_total).toFixed(1)} L300 ${y(est.lifetime_cost_40yr).toFixed(1)}`;
  const maintRows = Object.entries(est.maintenance_breakdown).filter(([, v]) => v > 0);

  // calendar: whole years, labels evenly spaced so they line up with year boundaries
  const cal = est.calendar || [];
  const first = cal[0]?.start ?? new Date().toISOString().slice(0, 10), last = cal.at(-1)?.end ?? first;
  const y0 = Number(first.slice(0, 4)), yEnd = Number(last.slice(0, 4)) + 1;
  const step = Math.ceil((yEnd - y0) / 6);
  const yN = y0 + Math.ceil((yEnd - y0) / step) * step;
  const T0 = Date.UTC(y0, 0, 1), span = Date.UTC(yN, 0, 1) - T0;
  const pct = (iso) => ((Date.parse(iso) - T0) / span) * 100;
  const years = [];
  for (let yr = y0; yr <= yN; yr += step) years.push(`<span>${yr}</span>`);
  const barCls = (phase) => /permit/i.test(phase) ? "o" : /construct|energiz/i.test(phase) ? "g" : "";

  // permits: real triggers + est. durations; tribal/protected shown as "not triggered" when absent
  const trig = new Set(est.permits.map((x) => x.code));
  const permitRows = est.permits.map((x) => `<div class="permit"><span>${esc(PERMIT_LABELS[x.code] || x.code)}
      <span class="muted" style="display:block;font-size:12px;margin-top:2px">${esc(x.reason)}</span></span>
      <span class="pill${x.months_max >= 12 ? " pill--warn" : ""}">${x.months_min}–${x.months_max} mo${mk("Placeholder review duration by trigger type")}</span></div>`)
    .concat(["TRIBAL_CONSENT", "PROTECTED_AREA"].filter((c) => !trig.has(c))
      .map((c) => `<div class="permit"><span>${PERMIT_LABELS[c]}</span><span class="pill pill--ok">Not triggered</span></div>`));

  const elev = (p.profile || []).map((q) => q[1]);
  const crumb = demo ? `<a href="dashboard.html">/ Projects</a> / Demo`
    : `<a href="dashboard.html">/ Projects</a> / <a href="project.html?id=${project.id}">${esc(project.name)}</a> / ${esc(route.name)}`;
  $("crumb").innerHTML = crumb;
  document.title = `${route.name} · Gen-tie Router`;

  return `
  <div class="rhead">
    <div><div class="mono-label">Route ${n} · ${esc(project.voltage_kv)} kV ${modeWord} · ${mi(p.total_km)} mi</div>
      <h1 class="display display--1">${esc(route.name)} <span class="it acc">route</span></h1></div>
    <div class="figs">
      <div><div class="mono-label">Upfront</div><div class="display">${money(p.total_cost_usd)}</div></div>
      <div><div class="mono-label">40-yr total</div><div class="display">${money(est.lifetime_cost_40yr)}${mk("Placeholder: upfront + 40 yr of placeholder upkeep, undiscounted")}</div></div>
      <div><div class="mono-label">Energize</div><div class="display it acc2">${quarter(est.energize_date)}${mk("Placeholder: end of the placeholder deployment calendar")}</div></div>
    </div>
  </div>
  <nav class="tabs tabs--sticky" aria-label="Route sections">
    <a href="#overview" class="is-active" aria-current="true">Overview</a><a href="#costs">Costs</a><a href="#inventory">Inventory</a>
    <a href="#maintenance">Maintenance</a><a href="#calendar">Calendar</a><a href="#permits">Permits &amp; legal</a><a href="#analysis">Route analysis</a>
  </nav>
  <section class="three grid-bg sect" id="overview" aria-label="3D route view">
    <div class="map" id="map"></div>
    <div class="hud" style="left:20px;top:20px;max-width:calc(100% - 180px)" id="terrainHud">
      <div class="mono-label" id="terrainLabel">3D terrain · Cesium</div>
      <div style="margin-top:6px">${elev.length ? `Elevation ${num(Math.min(...elev))}–${num(Math.max(...elev))} m` : "No elevation profile"}</div>
      <div class="mono-label" id="dataBadge" style="margin-top:6px"></div>
    </div>
    <div class="hud hud--glass layers" style="right:20px;top:20px">
      <details id="layers"><summary class="mono-label">Layers</summary>
        <div class="row" role="group" aria-label="View mode">
          <button class="btn btn--sm" type="button" data-view="top">Top</button>
          <button class="btn btn--sm" type="button" data-view="enhanced">Enhanced</button>
        </div>
        <label id="exagRow">Terrain height <select id="exag">
          <option value="1">1×</option><option value="1.5">1.5×</option><option value="2">2×</option><option value="3">3×</option></select></label>
        <label><input type="checkbox" data-layer="towers" checked> Towers (${num(towers)})</label>
        <label><input type="checkbox" data-layer="hftd"> <span class="acc2">Fire tier</span>&nbsp;(HFTD)</label>
        <label><input type="checkbox" data-layer="protected"> <span class="ok">Protected</span></label>
        <details><summary class="mono-label">More</summary>
          <label><input type="checkbox" data-layer="fire"> Wildfire hazard (WHP)</label>
          <label><input type="checkbox" data-layer="tribal"> Tribal land</label>
          <label><input type="checkbox" data-layer="lines" checked> Existing lines</label>
          <label><input type="checkbox" data-layer="baseline" checked> Straight-line baseline</label>
          <label><input type="checkbox" data-layer="contours"> Contours (100 m)</label>
          <label><input type="checkbox" data-layer="cities" checked> Cities &amp; towns</label>
          <label><input type="checkbox" data-layer="borders" checked> Borders</label>
        </details>
      </details>
    </div>
    <div class="hud" style="left:50%;bottom:20px;transform:translateX(-50%);display:flex;gap:14px;align-items:center;white-space:nowrap">
      <button class="btn btn--sm" type="button" id="fly" disabled>▶ Fly along</button>
      <span class="mono-label" id="flyCount">${num(towers)} towers · ${mi(p.total_km)} mi</span>
    </div>
    <div class="hud" id="segCallout" hidden style="pointer-events:none"></div>
  </section>

  <div class="bento">
    <div class="cell sect" id="costs" style="grid-column:span 4"><div class="mono-label">Upfront cost ${mk("Category split is a placeholder; the total is the real model cost")}</div>
      <h6>Where the <span class="it">${money(p.total_cost_usd)}</span> goes</h6>
      <div class="donut" style="${donutStyle}" role="img" aria-label="${esc(donutLabel)}"></div>
      <div class="lg">${slices.map(([k, v], i) => `<span><span><i style="background:var(--series-${i + 1})"></i>${esc(k)}</span>${money(v)}${mk()}</span>`).join("")}</div>
    </div>
    <div class="cell sect" id="inventory" style="grid-column:span 4"><div class="mono-label">Inventory</div>
      <h6>Bill of <span class="it">materials</span></h6>
      <table class="inv">
        ${invRows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}
        ${leads.map((l) => `<tr><td>${esc(l.item)} lead time</td><td>${l.weeks} wks${mk("Placeholder lead time")}</td></tr>`).join("")}
      </table>
      ${leads.length ? `<div class="risk" style="margin-top:14px">▲ ${esc(leads[0].item)} lead time ${leads[0].weeks} wks${mk("Placeholder lead time")}</div>` : ""}
    </div>
    <div class="cell sect" id="maintenance" style="grid-column:span 4"><div class="mono-label">Maintenance ${mk("Placeholder: $ per km and per tower, scaled by voltage")}</div>
      <h6><span class="it">${money(est.maintenance_per_year)}</span>${mk()} a year</h6>
      <svg viewBox="0 0 300 110" class="spark" preserveAspectRatio="none" role="img" aria-label="Lifetime cost from ${money(est.upfront_total)} at year 0 to ${money(est.lifetime_cost_40yr)} at year 40">
        <path d="${spark} V110 H0Z" fill="var(--series-1)" opacity=".12"/><path d="${spark}" fill="none" stroke="var(--series-1)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>
      <div class="mono-label" style="display:flex;justify-content:space-between;margin:4px 0 8px"><span>Yr 0 · ${money(est.upfront_total)}</span><span>Yr 40 · ${money(est.lifetime_cost_40yr)}${mk()}</span></div>
      <table class="inv">${maintRows.map(([k, v]) => `<tr><td>${MAINT_LABELS[k] || esc(k)}</td><td>${fmtUsd(v)}/yr${mk()}</td></tr>`).join("")}</table>
    </div>
    <div class="cell sect" id="calendar" style="grid-column:span 7"><div class="mono-label">Deployment calendar ${mk("Placeholder durations, driven by the real permit triggers")}</div>
      <h6>Energized by <span class="it acc2">${quarter(est.energize_date)}</span></h6>
      <div class="gantt">
        <span></span><div class="years mono-label">${years.join("")}</div>
        ${cal.map((c) => `<span>${esc(c.phase)}</span><div class="track" title="${c.start} → ${c.end}"><i class="${barCls(c.phase)}" style="left:${pct(c.start).toFixed(1)}%;width:${Math.max(0.8, pct(c.end) - pct(c.start)).toFixed(1)}%"></i></div>`).join("")}
      </div>
      <p class="muted" style="font-size:12px;margin-top:12px">Starts ${demo || !project.filing_target ? "today" : `at the filing target, ${esc(project.filing_target)}`}.</p>
    </div>
    <div class="cell sect" id="permits" style="grid-column:span 5"><div class="mono-label">Permits &amp; legal</div>
      <h6>${est.permits.length} <span class="it">trigger${est.permits.length === 1 ? "" : "s"}</span></h6>
      ${permitRows.join("")}
    </div>
    ${analysisCell()}
  </div>`;
}

// The legacy results panel (panel.js renderResults fills every data-r target).
function analysisCell() {
  const kv = (rows) => `<table class="inv">${rows.map(([k, r]) => `<tr><td>${k}</td><td data-r="${r}">–</td></tr>`).join("")}</table>`;
  const cmp = [["Length", "Len"], ["Cost", "Cost"], ["Protected", "Prot"], ["Tribal land", "Trib"], ["OH in high hazard", "Fire"], ["Mean hazard", "Risk"]];
  return `<div class="cell sect analysis-cell" id="analysis" style="grid-column:span 12">
    <div class="mono-label">Route analysis <span class="pill" data-r="demoTag" hidden>Demo · precomputed</span></div>
    <h6>Every <span class="it">metric</span></h6>
    <div class="analysis">
      <div>
        <div style="display:flex;gap:32px;margin-bottom:8px"><div><div class="mono-label">Model cost</div><div class="display" style="font-size:38px" data-r="rCost">–</div></div>
          <div><div class="mono-label">Length</div><div class="display" style="font-size:38px" data-r="rKm">–</div></div></div>
        ${kv([["Overhead", "rOh"], ["Underground", "rUg"], ["High wildfire hazard", "rRisk"], ["OH in high hazard", "rOhRisk"],
              ["HFTD Tier 2/3", "rFire"], ["Mean hazard (0–1)", "rMeanRisk"], ["Protected land", "rProt"],
              ["OH/UG transitions", "rTrans"], ["Tribal land", "rTribal"]])}
        <div class="risk" data-r="tribalNote" hidden style="margin-top:10px"></div>
      </div>
      <div>
        <div class="mono-label">Right-of-way &amp; permitting</div>
        ${kv([["Landholder negotiations", "rLandholders"], ["Structures", "rStructures"]])}
        <ul class="triggers" data-r="triggers"></ul>
        <div class="mono-label" style="margin-top:20px">vs. straight overhead line</div>
        <table class="inv cmp"><thead><tr><th></th><th>Route</th><th>Straight</th></tr></thead><tbody>
          ${cmp.map(([k, r]) => `<tr><td>${k}</td><td data-r="c${r}R"></td><td data-r="c${r}B"></td></tr>`).join("")}
        </tbody></table>
        <p class="note" data-r="cmpNote"></p>
      </div>
      <div>
        <div class="mono-label">Elevation profile</div>
        <svg class="profile" data-r="profile" viewBox="0 0 300 90" preserveAspectRatio="none" aria-hidden="true"></svg>
        <div class="mono-label" style="display:flex;justify-content:space-between"><span>0 km</span><span data-r="profEnd"></span></div>
        <div class="mono-label" style="margin-top:20px">Segments</div>
        <ol class="segments" data-r="segments"></ol>
        <p class="note" id="assumptions"></p>
        <p class="note">Enhanced view: 3D towers and conductors appear when zoomed in (&lt; 9 km). Left-drag pan · Right-drag look around ·
          Wheel zoom · Ctrl/middle-drag tilt · Hover or click the line for a segment readout.</p>
      </div>
    </div>
    <div class="story" data-r="demoStory" hidden></div>
  </div>`;
}

const NOTE_OFFLINE = "Costs are rough planning assumptions ($2M/mi overhead, $10M/mi underground, scaled by slope, wildfire tier, corridors & land status). Not an engineering estimate.";
async function assumptionsNote(demo) {
  if (!demo) {
    try {
      const a = await getAssumptions();
      return `Costs are rough planning assumptions (${fmtUsd(a.OVERHEAD_COST_PER_MILE)}/mi overhead, ${fmtUsd(a.UNDERGROUND_COST_PER_MILE)}/mi underground, ` +
        `${fmtUsd(a.TRANSITION_COST_USD)} per OH/UG transition, scaled by slope, wildfire tier, corridors & land status). Not an engineering estimate.`;
    } catch { /* backend gone: fall back to the static text */ }
  }
  return NOTE_OFFLINE;
}

// ------------------------------------------------------------------ actions
function wireActions(data, est) {
  const exp = $("export"), mark = $("mark");
  exp.disabled = false;
  exp.onclick = () => {
    const { fc, route, project } = data;
    const packet = { exported_at: new Date().toISOString(), project: data.demo ? null : project,
      route: { ...route, result: fc }, estimate: est };
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(packet, null, 2)], { type: "application/json" }));
    a.download = `${(data.demo ? "demo" : `${project.name}-${route.name}`).toLowerCase().replace(/[^a-z0-9]+/g, "-")}-packet.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  const sync = () => {
    const done = data.project.phase === "ready";
    mark.disabled = data.demo || done;
    mark.textContent = done ? "Marked for filing" : "Mark for filing";
    mark.title = data.demo ? "Demo route: not part of a project" : "";
  };
  sync();
  mark.onclick = async () => {
    mark.disabled = true;
    try {
      data.project = await updateProject(data.project.id, { phase: "ready" });
      toast(`${data.project.name} is marked ready to file.`);
    } catch (e) {
      toast(`Couldn't mark for filing: ${e.detail || e.message}`, true);
    }
    sync();
  };
}

// ------------------------------------------------------------------ tabs: scroll + active section
function wireTabs() {
  const links = [...document.querySelectorAll(".tabs a")];
  const setActive = (id) => links.forEach((a) => {
    const on = a.hash === `#${id}`;
    a.classList.toggle("is-active", on);
    if (on) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current");
  });
  const io = new IntersectionObserver((entries) => {
    const vis = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
    if (vis) setActive(vis.target.id);
  }, { rootMargin: "-60px 0px -55% 0px" });
  document.querySelectorAll(".sect").forEach((s) => io.observe(s));
}

// ------------------------------------------------------------------ map hero
async function initMap(data) {
  const p = data.fc.properties;
  let map;
  const fail = (e) => map?.toast(`Map error: ${e?.message || e}`);
  try {
    map = await createMap($("map"), {
      panelOffset: 0, // no side panel here: centre the route in the hero
      onFlyChange: (on) => { $("fly").textContent = on ? "■ Stop" : "▶ Fly along"; if (on) showSegment(null); },
      onSegment: (info, pos) => showSegment(info, pos),
    });
  } catch (e) {
    $("terrainLabel").textContent = "3D map unavailable";
    $("dataBadge").textContent = e.message || String(e);
    return null;
  }
  window.__map = map; // console / smoke handle

  const b = map.bounds();
  $("terrainLabel").textContent = window.APP_CONFIG?.CESIUM_ION_TOKEN ? "3D terrain · Cesium" : "Flat terrain · OSM imagery";
  $("dataBadge").textContent = b.offline ? "Offline · precomputed route"
    : b.synthetic_layers?.length ? `Synthetic data: ${b.synthetic_layers.join(", ")}` : "Real data · 3DEP · PAD-US · WHP · HFTD · OSM";
  if (b.synthetic_layers?.length) $("dataBadge").classList.add("acc2");

  map.showRoute(data.fc);
  map.setFlow?.(true);

  // layers
  const applyTowers = () => map.setTowers(document.querySelector('[data-layer="towers"]').checked);
  for (const box of document.querySelectorAll("[data-layer]")) {
    const name = box.dataset.layer;
    if (name === "towers") { box.onchange = applyTowers; continue; }
    if (name === "lines" && b.offline) box.checked = false; // backend overlay: unavailable offline
    box.onchange = () => Promise.resolve(map.toggleOverlay(name, box.checked)).catch(fail);
    if (box.checked) box.onchange();
  }

  // view switch + terrain height
  const syncView = () => {
    const enh = map.getView() === "enhanced";
    for (const btn of document.querySelectorAll("[data-view]")) {
      const on = btn.dataset.view === map.getView();
      btn.classList.toggle("btn--fill", on);
      btn.setAttribute("aria-pressed", String(on));
    }
    $("exagRow").hidden = !enh;
    $("exag").value = String(map.getExaggeration());
  };
  for (const btn of document.querySelectorAll("[data-view]")) {
    btn.onclick = () => map.setView(btn.dataset.view).then(syncView).catch(fail);
  }
  $("exag").onchange = (e) => map.setExaggeration(Number(e.target.value)).catch(fail);
  syncView();
  $("layers").open = matchMedia("(min-width: 900px)").matches;

  // fly along
  $("fly").disabled = false;
  $("fly").onclick = () => Promise.resolve(map.startFly()).catch(fail);
  map.onFlyProgress((passed, total, km) => {
    $("flyCount").textContent = `Tower ${passed} / ${total} · mi ${mi(km)}`;
  });

  try { await map.ready; } catch (e) { fail(e); }
  return map;
}

// Segment callout: map.js reports the selected-route segment under the cursor (hover) or clicked (pinned).
function showSegment(s, pos) {
  const el = $("segCallout");
  if (!s) { el.hidden = true; return; }
  const fire = s.mode === "overhead" && s.high_fire_km > 0.05;
  el.className = "hud" + (fire ? " hud--alert" : "");
  el.innerHTML = `<div class="mono-label${fire ? " acc2" : ""}">Segment ${s.index + 1} of ${s.count}</div>
    <div style="margin-top:4px">${esc(s.mode)} · ${fmtKm(s.length_km)} (${mi(s.length_km)} mi)<br>${fmtUsd(s.est_cost_usd)}` +
    `${s.cost_per_mile_usd ? ` · ${fmtUsd(s.cost_per_mile_usd)}/mi` : ""}${fire ? `<br>${fmtKm(s.high_fire_km)} in HFTD` : ""}</div>`;
  el.hidden = false;
  const box = el.parentElement;
  el.style.left = `${Math.max(8, Math.min(pos.x + 16, box.clientWidth - el.offsetWidth - 8))}px`;
  el.style.top = `${Math.max(8, Math.min(pos.y + 16, box.clientHeight - el.offsetHeight - 8))}px`;
}

// ------------------------------------------------------------------ boot
async function boot() {
  let data;
  try {
    data = await load();
  } catch (e) {
    if (e.status !== 404) {
      emptyState(`<div class="display display--3">Backend <span class="it">unreachable</span></div>
        <p class="muted">Couldn't load this route (${esc(e.detail || e.message)}). Is the server running?</p>
        <button class="btn" type="button" id="retry">Retry</button>`);
      $("retry").onclick = run;
      return;
    }
  }
  if (!data) {
    emptyState(`<div class="display display--3">Route not <span class="it">found</span></div>
      <p class="muted">It may have been deleted, or the link is wrong.</p>
      <a class="btn" href="dashboard.html">← Back to projects</a>`);
    return;
  }
  const est = estimate(data.fc.properties, data.project);
  app.innerHTML = page(data, est);
  renderResults($("analysis"), data.fc.properties, data.fc.features, { isDemo: data.demo });
  $("assumptions").textContent = await assumptionsNote(data.demo);
  wireActions(data, est);
  wireTabs();
  await initMap(data);
}

// Boot (and Retry): any error that escapes boot is shown, not swallowed.
async function run() {
  try { await boot(); } catch (e) {
    emptyState(`<div class="display display--3">Something went <span class="it">wrong</span></div><p class="muted">${esc(e.message)}</p>`);
  }
}
await run();
ready();
