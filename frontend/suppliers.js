/* Supplier bids page: mock bids for one material, opened from a route's supply-chain section. */
(() => {
  "use strict";

  const API = (window.APP_CONFIG || {}).API_BASE || "";
  const $ = (id) => document.getElementById(id);
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmtUsd = (x) => (Math.abs(x) >= 1e6 ? `$${(x / 1e6).toFixed(2)}M` : Math.abs(x) >= 1e4
    ? `$${Math.round(x / 1e3)}k` : `$${Math.round(x).toLocaleString()}`);
  const fmtQty = (x) => (x >= 100 ? Math.round(x).toLocaleString() : +x.toFixed(2));
  const MAX_CMP = 3;

  const state = { r: null, picked: new Set() };

  // Came from a route page: step back in history; otherwise the link goes to the project list.
  $("back").addEventListener("click", (e) => {
    if (history.length > 1 && document.referrer && new URL(document.referrer).origin === location.origin) {
      e.preventDefault();
      history.back();
    }
  });

  function fail(msg) {
    $("itemMeta").textContent = "";
    $("error").textContent = msg;
    $("error").hidden = false;
  }

  async function load() {
    const q = new URLSearchParams(location.search);
    if (!q.get("item") || !q.get("qty")) {
      fail("No material selected. Open this page with “View suppliers” in a route’s supply-chain section.");
      return;
    }
    try {
      const res = await fetch(`${API}/inventory/bids?${q}`);
      if (!res.ok) {
        let detail = res.statusText;
        try { detail = (await res.json()).detail || detail; } catch (_) { /* not json */ }
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      state.r = await res.json();
    } catch (err) {
      fail(`Could not load bids: ${err.message}. The bids come from the backend, which must be running.`);
      return;
    }
    // start with the two best-value bids side by side
    state.r.bids.filter((b) => b.meets_spec).slice(0, 2).forEach((b) => state.picked.add(b.supplier_id));
    renderHeader();
    renderPicks();
    renderTable();
    renderCompare();
  }

  // ------------------------------------------------------------------ header + headline picks
  function renderHeader() {
    const { item, params } = state.r;
    document.title = `${item.name} bids · Gen-tie Router`;
    $("itemName").textContent = item.name;
    const meta = [`${fmtQty(item.qty)} ${item.unit}`, `${params.voltage_kv} kV`, params.market_label];
    if (item.need_by_week != null) meta.splice(1, 0, `needed on site by week ${Math.round(item.need_by_week)}`);
    meta.push(`typical lead ${item.lead_weeks[1]} wks (range ${item.lead_weeks[0]}–${item.lead_weeks[2]})`);
    $("itemMeta").textContent = meta.join(" · ");
    if (item.note) { $("itemNote").textContent = item.note; $("itemNote").hidden = false; }
    const w = state.r.weights;
    $("footNote").textContent = `${state.r.note} Score = ${Math.round(100 * w.price)}% price + ${Math.round(100 * w.delivery)}% delivery
      + ${Math.round(100 * w.reliability)}% on-time record, each relative to the best qualified bid; bids that miss the need-by week
      lose 15%. Delivery weeks are counted from today and include haul to the project site.`;
  }

  function renderPicks() {
    const { bids, best } = state.r;
    const by = (id) => bids.find((b) => b.supplier_id === id);
    const cards = [
      ["Best value", best.value, (b) => `Score ${b.score} · ${fmtUsd(b.total_usd)} · week ${Math.round(b.delivery_week)}`],
      ["Lowest total", best.price, (b) => fmtUsd(b.total_usd)],
      ["Fastest delivery", best.delivery, (b) => `Week ${Math.round(b.delivery_week)}${b.meets_need_by === false ? " (still late)" : ""}`],
    ].filter(([, id]) => id);
    if (!cards.length) {
      fail("No supplier that bid meets the spec for this material at this voltage. Change the spec or find a new source.");
    }
    $("picks").innerHTML = cards.map(([label, id, line], i) => {
      const b = by(id);
      return `<button class="pick${i === 0 ? " top" : ""}" data-id="${esc(id)}">
        <span class="k">${label}</span><span class="nm">${esc(b.name)}</span><span class="muted">${esc(line(b))}</span></button>`;
    }).join("");
    $("picks").hidden = !cards.length;
    $("picks").querySelectorAll(".pick").forEach((el) => el.addEventListener("click", () => {
      const row = document.querySelector(`#bidRows tr[data-id="${el.dataset.id}"]`);
      if (!row) { $("onlyOnTime").checked = false; renderTable(); }
      flash(el.dataset.id);
    }));
  }

  function flash(id) {
    const row = document.querySelector(`#bidRows tr[data-id="${id}"]`);
    if (!row) return;
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    row.classList.remove("flash");
    void row.offsetWidth;                        // restart the animation
    row.classList.add("flash");
  }

  // ------------------------------------------------------------------ bids table
  const deliveryChip = (b) => (b.meets_need_by == null ? "" : b.meets_need_by
    ? '<span class="st on-track">ON TIME</span>' : '<span class="st late">LATE</span>');

  function supplierCell(b) {
    const chips = [b.origin === "catalog" ? '<span class="chip">Current supplier</span>' : '<span class="chip new">New bidder</span>'];
    if (!b.domestic) chips.push('<span class="chip">Import</span>');
    if (!b.meets_spec) chips.push('<span class="chip bad">Off-spec</span>');
    return `<div class="nm">${esc(b.name)}</div><div class="muted small">${esc(b.city)} · ${b.distance_km.toLocaleString()} km</div>
      <div class="chips">${chips.join("")}</div>`;
  }

  function renderTable() {
    const { bids, best, item } = state.r;
    const key = $("sort").value, asc = key === "total_usd" || key === "delivery_week";
    const rows = bids
      .filter((b) => !$("onlySpec").checked || b.meets_spec)
      .filter((b) => !$("onlyOnTime").checked || b.meets_need_by !== false)
      .sort((a, b) => {
        const va = a[key] ?? (asc ? Infinity : -Infinity), vb = b[key] ?? (asc ? Infinity : -Infinity);
        return asc ? va - vb : vb - va;
      });
    $("bidCount").textContent = `(${rows.length} of ${bids.length})`;
    $("bidRows").innerHTML = rows.map((b) => {
      const tags = [b.supplier_id === best.value && "Best value", b.supplier_id === best.price && "Lowest",
        b.supplier_id === best.delivery && "Fastest"].filter(Boolean);
      const picked = state.picked.has(b.supplier_id);
      return `<tr data-id="${esc(b.supplier_id)}" class="${picked ? "on" : ""}${b.meets_spec ? "" : " off"}">
        <td class="c"><input type="checkbox" aria-label="Compare ${esc(b.name)}" ${picked ? "checked" : ""}
          ${!picked && state.picked.size >= MAX_CMP ? "disabled" : ""}></td>
        <td class="sup">${supplierCell(b)}${tags.length ? `<div class="chips">${tags.map((t) => `<span class="chip best">${t}</span>`).join("")}</div>` : ""}</td>
        <td>${b.score == null ? '<span class="muted">–</span>' : `<div class="score"><b>${b.score}</b><span class="bar"><i style="width:${b.score}%"></i></span></div>`}</td>
        <td class="n"><b>${fmtUsd(b.total_usd)}</b><div class="muted small">incl. ${fmtUsd(b.freight_usd)} freight</div></td>
        <td class="n">${fmtUsd(b.unit_price_usd)}<div class="muted small">per ${esc(item.unit === "each" ? "unit" : item.unit.replace(/s$/, ""))}</div></td>
        <td class="n">Week ${Math.round(b.delivery_week)} ${deliveryChip(b)}<div class="muted small">quoted lead ${b.quoted_lead_weeks} wks</div></td>
        <td class="n">${b.from_stock_qty > 0 ? `${fmtQty(b.from_stock_qty)} ${esc(item.unit)}` : '<span class="muted">none</span>'}</td>
        <td class="n">${b.on_time_pct}%</td>
        <td class="n">${b.warranty_years} yr</td>
        <td>${esc(b.payment_terms)}</td>
      </tr>`;
    }).join("");
    $("emptyNote").hidden = rows.length > 0;
    $("bidsSec").hidden = false;
    $("bidRows").querySelectorAll("input[type=checkbox]").forEach((cb) => cb.addEventListener("change", () => {
      const id = cb.closest("tr").dataset.id;
      if (cb.checked) state.picked.add(id); else state.picked.delete(id);
      renderTable();
      renderCompare();
    }));
  }
  ["sort", "onlyOnTime", "onlySpec"].forEach((id) => $(id).addEventListener("change", renderTable));

  // ------------------------------------------------------------------ side-by-side comparison
  // [label, value, sort direction for "best" highlight (+1 higher is better, -1 lower is better, 0 none)]
  const FIELDS = [
    ["Best-value score", (b) => b.score, 1, (b) => (b.score == null ? "–" : b.score)],
    ["Total bid", (b) => b.total_usd, -1, (b) => fmtUsd(b.total_usd)],
    ["Unit price", (b) => b.unit_price_usd, -1, (b) => fmtUsd(b.unit_price_usd)],
    ["Freight", (b) => b.freight_usd, -1, (b) => fmtUsd(b.freight_usd)],
    ["Delivered by", (b) => b.delivery_week, -1, (b) => `Week ${Math.round(b.delivery_week)} ${deliveryChip(b)}`],
    ["Quoted lead time", (b) => b.quoted_lead_weeks, -1, (b) => `${b.quoted_lead_weeks} wks`],
    ["Available from stock", (b) => b.from_stock_qty, 1, (b) => (b.from_stock_qty > 0 ? `${fmtQty(b.from_stock_qty)} ${esc(state.r.item.unit)}` : "none")],
    ["On-time record", (b) => b.on_time_pct, 1, (b) => `${b.on_time_pct}%`],
    ["Warranty", (b) => b.warranty_years, 1, (b) => `${b.warranty_years} years`],
    ["Distance to site", (b) => b.distance_km, -1, (b) => `${b.distance_km.toLocaleString()} km`],
    ["Payment terms", null, 0, (b) => esc(b.payment_terms)],
    ["Price basis", null, 0, (b) => esc(b.price_basis)],
    ["Bid valid for", (b) => b.valid_days, 1, (b) => `${b.valid_days} days`],
    ["Origin", null, 0, (b) => (b.domestic ? "Domestic" : "Import")],
    ["Meets spec", null, 0, (b) => (b.meets_spec ? '<span class="good">Yes</span>' : '<span class="bad">No</span>')],
    ["Exceptions", null, 0, (b) => (b.exceptions.length ? `<ul>${b.exceptions.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : '<span class="muted">None</span>')],
  ];

  function renderCompare() {
    const picked = state.r.bids.filter((b) => state.picked.has(b.supplier_id));
    $("cmpSec").hidden = false;
    $("cmpHint").textContent = picked.length >= MAX_CMP ? `Comparing the maximum of ${MAX_CMP}. Untick one to swap.`
      : `Tick up to ${MAX_CMP} bids in the table to compare them.`;
    if (!picked.length) { $("cmpGrid").innerHTML = ""; return; }
    const head = picked.map((b) => `<th><div class="nm">${esc(b.name)}</div><div class="muted small">${esc(b.city)}</div>
      <button class="rm" data-id="${esc(b.supplier_id)}" aria-label="Remove ${esc(b.name)} from comparison">Remove</button></th>`).join("");
    const body = FIELDS.map(([label, val, dir, show]) => {
      let bestVal = null;
      if (dir && picked.length > 1) {
        const vals = picked.filter((b) => b.meets_spec).map(val).filter((v) => v != null);
        if (vals.length) bestVal = dir > 0 ? Math.max(...vals) : Math.min(...vals);
      }
      return `<tr><td>${label}</td>${picked.map((b) => {
        const win = bestVal != null && b.meets_spec && val(b) === bestVal;
        return `<td class="${win ? "win" : ""}">${show(b)}</td>`;
      }).join("")}</tr>`;
    }).join("");
    $("cmpGrid").innerHTML = `<div class="tablewrap"><table class="cmpt"><thead><tr><th></th>${head}</tr></thead><tbody>${body}</tbody></table></div>
      ${picked.length > 1 ? '<div class="muted small">Green marks the best qualified value in each row.</div>' : ""}`;
    $("cmpGrid").querySelectorAll("button.rm").forEach((btn) => btn.addEventListener("click", () => {
      state.picked.delete(btn.dataset.id);
      renderTable();
      renderCompare();
    }));
  }

  load().finally(() => { document.documentElement.dataset.ready = "1"; }); // boot contract: tools/smoke_pages.py
})();
