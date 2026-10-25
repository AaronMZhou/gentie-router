// Supply-chain simulation cell (POST /inventory): verdict, long-lead order plan, links to mock supplier bids.
import { apiSend } from "./api.js";
import { fmtUsd } from "./panel.js";

const $ = (id) => document.getElementById(id);
const esc = (t) => String(t).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const INPUTS = ["invMw", "invKv", "invNeed", "invMarket"];

export const supplyCell = (voltageKv) => `
  <div class="cell sect" id="supply" style="grid-column:span 12"><div class="mono-label">Supply chain <span class="est" title="Simulated suppliers, placeholder lead times and prices">sim.</span></div>
    <h6>Materials &amp; <span class="it">long-lead procurement</span></h6>
    <div class="fx-grid invctl">
      <label>Project MW <input id="invMw" type="number" min="5" max="1000" value="100"></label>
      <label>Line kV <select id="invKv"><option value="69"${voltageKv === 69 ? " selected" : ""}>69</option><option value="138"${voltageKv !== 69 ? " selected" : ""}>138</option></select></label>
      <label>Construction starts in (wks) <input id="invNeed" type="number" min="0" max="520" value="104"></label>
      <label>Supply market <select id="invMarket"><option value="current">Current</option><option value="tight">Tight</option><option value="crisis">Crisis</option></select></label>
    </div>
    <div id="invSummary" class="note"></div>
    <ul id="invList" class="inv"></ul>
    <div id="invNote" class="note"></div>
  </div>`;

// route: {start, end, preset}; demo routes have no backend, so only a notice is shown.
export function initSupply(route, { demo }) {
  let seq = 0;
  const sum = $("invSummary"), list = $("invList");

  async function refresh() {
    if (demo) { sum.textContent = "The supplier simulation needs the backend (not available in demo mode)."; return; }
    const mine = ++seq;
    sum.textContent = "Checking supplier availability…";
    list.innerHTML = "";
    try {
      const r = await apiSend("POST", "/inventory", {
        start: route.start, end: route.end, preset: route.preset,
        capacity_mw: +$("invMw").value || 100, voltage_kv: +$("invKv").value,
        need_by_weeks: +$("invNeed").value || 0, market: $("invMarket").value,
      });
      if (mine === seq) render(r); // ignore answers superseded by a newer request
    } catch (err) {
      if (mine === seq) sum.textContent = `Inventory check failed: ${err.detail || err.message}`;
    }
  }

  function render(r) {
    const s = r.summary, e = s.earliest_construction_start_weeks;
    const cls = { VIABLE: "good", "AT RISK": "mid", "NOT VIABLE": "bad" }[s.verdict];
    const parts = [`<b class="${cls}">${s.verdict}</b>: ${Math.round(100 * s.p_materials_on_time)}% chance every item arrives in time for construction to start in ${r.params.need_by_weeks} wks.`];
    if (e.p50 != null) parts.push(`Earliest start the materials allow: week ${Math.round(e.p50)} typical, week ${Math.round(e.p90)} cautious.`);
    if (s.unqualified.length) parts.push(`<span class="bad">No qualified supplier:</span> ${s.unqualified.map(esc).join(", ")}.`);
    if (s.critical_path_drivers.length) {
      parts.push(`Holding up the schedule: ${s.critical_path_drivers.map((d) => `${esc(d.name)} (${Math.round(100 * d.share)}%)`).join(", ")}.`);
    }
    parts.push(`Materials ≈ ${fmtUsd(s.est_material_cost_usd)} (placeholder prices).`);
    sum.innerHTML = parts.join("<br>");
    const byId = Object.fromEntries(r.lines.map((l) => [l.id, l]));
    const bidsUrl = (l) => "suppliers.html?" + new URLSearchParams({
      item: l.id, qty: l.qty, voltage_kv: r.params.voltage_kv, capacity_mw: r.params.capacity_mw, market: r.params.market,
      need_by_week: l.need_by_week, site_lon: route.start[0], site_lat: route.start[1],
    });
    list.innerHTML = r.order_plan.map((o) => {
      const l = byId[o.id];
      const eta = l.eta_weeks.p50 == null ? "no ETA" : `ETA ${Math.round(l.eta_weeks.p50)}–${Math.round(l.eta_weeks.p90)} wks`;
      const stock = l.in_stock_qty > 0 ? ` · ${l.in_stock_qty} ${l.unit} in stock` : "";
      return `<li><div class="r1"><span class="nm">${o.long_lead ? '<span class="ll">LONG-LEAD</span>' : ""}${esc(l.name)}</span>
        <span class="st ${l.status.toLowerCase().replaceAll(" ", "-")}">${l.status}</span></div>
        <div class="r2 muted">${l.qty} ${l.unit} · ${eta}${stock}</div>
        <div class="r3"><span class="r2">${esc(o.action)}</span>
        <a class="vs" href="${esc(bidsUrl(l))}">View suppliers</a></div></li>`;
    }).join("");
    $("invNote").textContent = `Simulated suppliers and placeholder lead times (${r.params.trials} Monte Carlo trials; market: ${r.params.market_label}). Edit data/inventory/catalog.json.`;
  }

  INPUTS.forEach((id) => $(id).addEventListener("change", refresh));
  return refresh();
}
