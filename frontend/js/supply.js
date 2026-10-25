// Supply-chain simulation cell (POST /inventory): verdict, long-lead order plan, links to mock supplier bids.
import { apiSend } from "./api.js";
import { fmtUsd } from "./panel.js";

const $ = (id) => document.getElementById(id);
const pressed = (id, key) => $(id).querySelector("[aria-pressed=true]").dataset[key];
const esc = (t) => String(t).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const MARKETS = [["current", "Current", "Lead times as published today."], ["tight", "Tight", "Longer waits, less stock on hand."],
  ["crisis", "Crisis", "Severe shortages, some items unavailable."]];

export const supplyCell = (voltageKv) => {
  const kv = voltageKv === 69 ? 69 : 138;
  const seg = (name, items, on) => items.map(([v, big, small]) =>
    `<button type="button" data-${name}="${v}" aria-pressed="${String(v) === String(on)}"><b>${big}</b>${small ? `<span>${small}</span>` : ""}</button>`).join("");
  return `
  <div class="cell sect" id="supply" style="grid-column:span 12"><div class="mono-label">Supply chain <span class="est" title="Simulated suppliers, placeholder lead times and prices">sim.</span></div>
    <h6>Materials &amp; <span class="it">long-lead procurement</span></h6>
    <div class="b-row">
      <div class="b-field">
        <span class="lbl" id="invKvL">Line voltage</span>
        <div class="seg two" role="group" aria-labelledby="invKvL" id="invKv">${seg("kv", [[69, "69 kV"], [138, "138 kV"]], kv)}</div>
        <span class="hint">Voltage of the new line. It sets the towers and equipment you need to buy.</span>
      </div>
      <div class="b-field">
        <div class="b-top"><label class="lbl" for="invMw">Project size</label><span class="b-val"><span id="invMwV">100</span><small>MW</small></span></div>
        <input id="invMw" type="range" min="5" max="1000" step="5" value="100">
        <div class="ticks"><span>5</span><span>250</span><span>500</span><span>750</span><span>1000</span></div>
        <span class="hint">Generating capacity. Bigger projects need a bigger step-up transformer.</span>
      </div>
      <div class="b-field wide">
        <div class="b-top"><label class="lbl" for="invNeed">Construction starts in</label><span class="b-val"><span id="invNeedV">104</span><small id="invNeedU">WEEKS · ≈ 2.0 YEARS</small></span></div>
        <input id="invNeed" type="range" min="0" max="520" step="4" value="104">
        <div class="ticks"><span>NOW</span><span>1 YR</span><span>2 YR</span><span>5 YR</span><span>10 YR</span></div>
        <span class="hint">When crews need materials on site. The sooner it is, the more long-lead items miss the date.</span>
      </div>
      <div class="b-field wide">
        <span class="lbl" id="invMarketL">Supply market</span>
        <div class="seg" role="group" aria-labelledby="invMarketL" id="invMarket">${seg("market", MARKETS, "current")}</div>
      </div>
    </div>
    <div id="invSummary" class="note"></div>
    <ul id="invList" class="inv"></ul>
    <div id="invNote" class="note"></div>
  </div>`;
};

// route: {start, end, preset}; demo routes have no backend, so only a notice is shown.
export function initSupply(route, { demo }) {
  let seq = 0;
  const sum = $("invSummary"), list = $("invList");

  async function refresh() {
    if (demo) {
      sum.textContent = "The supplier simulation needs the backend (not available in demo mode).";
      document.dispatchEvent(new CustomEvent("gentie:supply", { detail: { none: true } }));
      return;
    }
    const mine = ++seq;
    sum.textContent = "Checking supplier availability…";
    list.innerHTML = "";
    try {
      const r = await apiSend("POST", "/inventory", {
        start: route.start, end: route.end, preset: route.preset,
        capacity_mw: +$("invMw").value || 100, voltage_kv: +pressed("invKv", "kv"),
        need_by_weeks: +$("invNeed").value || 0, market: pressed("invMarket", "market"),
      });
      if (mine === seq) render(r); // ignore answers superseded by a newer request
    } catch (err) {
      if (mine === seq) {
        sum.textContent = `Inventory check failed: ${err.detail || err.message}`;
        document.dispatchEvent(new CustomEvent("gentie:supply", { detail: { none: true } }));
      }
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
    const delta = s.market_cost_delta_usd;
    parts.push(delta ? `<b>${delta > 0 ? "+" : "−"}${fmtUsd(Math.abs(delta))}</b> on the route's upfront cost from the ${esc(r.params.market_label.split(" ")[0].toLowerCase())} market (the route cost assumes current prices).`
      : "No change to the route's upfront cost: the route cost already assumes current-market prices.");
    document.dispatchEvent(new CustomEvent("gentie:supply", { detail: { delta, market: r.params.market, label: r.params.market_label, summary: s, lines: r.lines, params: r.params } }));
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

  for (const id of ["invKv", "invMarket"]) {
    $(id).addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      $(id).querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      refresh();
    });
  }
  // sliders: readout follows the thumb, the (slow) simulation reruns on release
  $("invMw").addEventListener("input", (e) => ($("invMwV").textContent = e.target.value));
  $("invNeed").addEventListener("input", (e) => {
    $("invNeedV").textContent = e.target.value;
    $("invNeedU").textContent = `WEEKS · ≈ ${(e.target.value / 52).toFixed(1)} YEARS`;
  });
  for (const id of ["invMw", "invNeed"]) $(id).addEventListener("change", refresh);
  return refresh();
}
