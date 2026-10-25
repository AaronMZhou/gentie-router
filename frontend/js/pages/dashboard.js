import { initThemeToggle } from "../theme.js";
import { listProjects, createProject } from "../api.js";
import { recommendedIndex } from "../estimates.js";

initThemeToggle(document.querySelector(".theme-toggle"));

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const KM_PER_MI = 1.609344;
const PHASES = {
  drafting: ["Drafting", "pill", 15],
  comparing: ["Comparing", "pill pill--blue", 50],
  "permit-risk": ["Permit risk", "pill pill--warn", 35],
  ready: ["Ready to file", "pill pill--ok", 90],
};

const money = (usd) => usd >= 1e9 ? `$${(usd / 1e9).toFixed(2)}B` : `$${Math.round(usd / 1e6)}M`;
// filing_target is a plain date; parse as local so it doesn't shift a day west of UTC
const localDate = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
const shortDate = (dt) => dt.toLocaleDateString("en-US", { month: "short", day: "numeric",
  ...(dt.getFullYear() !== new Date().getFullYear() && { year: "numeric" }) });

// Header: neutral greeting by local time of day (there is no real user).
const now = new Date();
const h = now.getHours();
$("greeting").innerHTML = `Good <span class="it acc">${h < 12 ? "morning" : h < 18 ? "afternoon" : "evening"}.</span>`;
$("portfolio-label").textContent = `Portfolio · ${now.toLocaleDateString("en-US", { month: "short", year: "numeric" })}`;

// Recommended route: route_summary carries every field estimate() reads, so this matches project.js.
function recommended(project) {
  return project.routes[recommendedIndex(project.routes, project)] ?? null;
}

// Every route's thumb projected linearly into the 300x150 box (equirectangular, aspect kept).
function thumbSvg(project, rec) {
  const W = 300, H = 150, PAD = 22;
  const routes = project.routes.filter((r) => r.thumb?.length > 1);
  const pts = routes.flatMap((r) => r.thumb);
  let paths = "";
  if (pts.length) {
    const lons = pts.map((p) => p[0]), lats = pts.map((p) => p[1]);
    const [x0, x1, y0, y1] = [Math.min(...lons), Math.max(...lons), Math.min(...lats), Math.max(...lats)];
    const kx = Math.cos(((y0 + y1) / 2) * Math.PI / 180);
    const s = Math.min((W - 2 * PAD) / ((x1 - x0) * kx || 1e-9), (H - 2 * PAD) / ((y1 - y0) || 1e-9));
    const ox = (W - (x1 - x0) * kx * s) / 2, oy = (H - (y1 - y0) * s) / 2;
    const d = (r) => "M" + r.thumb.map(([lon, lat]) =>
      `${(ox + (lon - x0) * kx * s).toFixed(1)} ${(H - oy - (lat - y0) * s).toFixed(1)}`).join(" L");
    // others first (route-2..4, thinner, translucent; route-2 dashed), the recommended on top with the flowing line
    routes.filter((r) => r !== rec).forEach((r, i) => {
      const c = 2 + (i % 3);
      paths += `<path d="${d(r)}" stroke="var(--route-${c})" stroke-width="1.2" fill="none" opacity=".65"${c === 2 ? ' stroke-dasharray="4 4"' : ""}/>`;
    });
    if (rec && routes.includes(rec)) paths +=
      `<path d="${d(rec)}" stroke="var(--route-1)" stroke-width="2" fill="none" style="filter:drop-shadow(0 0 3px var(--route-1))"/>` +
      `<path d="${d(rec)}" stroke="var(--text)" stroke-width="1.5" fill="none" class="flow"/>`;
  }
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="100%" aria-hidden="true">${paths}</svg>`;
}

function card(p) {
  const rec = recommended(p);
  const [label, cls, pct] = PHASES[p.phase] || PHASES.drafting;
  const words = esc(p.name).split(" ");
  const name = words.length > 1 ? `${words.slice(0, -1).join(" ")} <span class="it">${words.at(-1)}</span>` : words[0];
  const capex = p.routes.length ? money(Math.min(...p.routes.map((r) => r.total_cost_usd))) : "—";
  const length = rec ? `${Math.round(rec.total_km / KM_PER_MI)} mi` : "—";
  const n = p.routes.length;
  return `<a class="pcard" data-id="${p.id}" href="project.html?id=${p.id}">
    <div class="thumb grid-bg">${thumbSvg(p, rec)}</div>
    <div class="body">
      <div class="top"><span class="${cls}">${label}</span><span class="mono-label">${n} route${n === 1 ? "" : "s"}</span></div>
      <h4>${name}</h4>
      <div class="meta"><div class="mono-label">Capex<b>${capex}</b></div><div class="mono-label">Voltage<b>${p.voltage_kv} kV</b></div><div class="mono-label">Length<b>${length}</b></div></div>
      <div class="bar"><i style="width:${pct}%${p.phase === "ready" ? ";background:var(--ok)" : ""}"></i></div>
    </div>
  </a>`;
}

const NEW_CARD = `<button class="pcard pcard--new" type="button" data-new><span><span class="display it" style="font-size:40px">+</span><span class="mono-label" style="display:block;margin-top:8px">Start a project</span></span></button>`;

function renderKpis(projects) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const routes = projects.reduce((a, p) => a + p.routes.length, 0);
  const capex = projects.reduce((a, p) => a + (p.routes.length ? Math.min(...p.routes.map((r) => r.total_cost_usd)) : 0), 0);
  const next = projects.filter((p) => p.filing_target).map((p) => localDate(p.filing_target))
    .filter((d) => d >= today).sort((a, b) => a - b)[0];
  $("kpi-projects").textContent = $("nav-projects").textContent = projects.length;
  $("kpi-routes").textContent = $("nav-routes").textContent = routes;
  $("kpi-capex").textContent = capex ? money(capex) : "—";
  $("kpi-filing").textContent = next ? shortDate(next) : "—";
}

async function load() {
  const box = $("projects");
  try {
    const projects = await listProjects();
    renderKpis(projects);
    box.innerHTML = projects.length
      ? `<div class="pgrid">${projects.map(card).join("")}${NEW_CARD}</div>`
      : `<div class="empty-state"><div class="display display--3">No projects <span class="it">yet</span></div>
          <p class="muted">Start one to draft and compare candidate routes.</p>
          <button class="btn" type="button" data-new>+ Start a project</button></div>`;
  } catch (e) {
    box.innerHTML = `<div class="empty-state"><div class="display display--3">Backend <span class="it">unreachable</span></div>
      <p class="muted">Couldn't load your projects (${esc(e.message)}). Is the server running?</p>
      <button class="btn" type="button" data-retry>Retry</button> <a class="btn" href="route.html?demo=1">Open the offline demo</a></div>`;
  }
}

// New-project modal
const modal = $("new-project-modal");
const form = modal.querySelector("form");
const toast = document.querySelector(".toast");
let toastTimer;
function showError(msg) {
  toast.textContent = msg;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.hidden = true), 5000);
}
const openModal = () => { form.reset(); modal.showModal(); };
$("new-project").addEventListener("click", openModal);
$("np-cancel").addEventListener("click", () => modal.close());
$("projects").addEventListener("click", (e) => {
  if (e.target.closest("[data-new]")) openModal();
  else if (e.target.closest("[data-retry]")) load();
});
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(form);
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = true;
  try {
    const p = await createProject({
      name: f.get("name").trim(),
      voltage_kv: Number(f.get("voltage_kv")),
      phase: f.get("phase"),
      filing_target: f.get("filing_target") || null,
    });
    location.href = `project.html?id=${p.id}`;
  } catch (err) {
    showError(`Couldn't create the project: ${err.detail || err.message}`);
    submit.disabled = false;
  }
});

await load();
document.documentElement.dataset.ready = "1";
