/* Route results rendering (ported from the legacy app.js (removed): renderPanel / renderDemoStory / renderProfile).
   Writes into elements marked data-r="…" under root. */
export const fmtUsd = (x) => (Math.abs(x) >= 1e6 ? `$${(x / 1e6).toFixed(1)}M` : `$${Math.round(x / 1e3)}k`);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
export const fmtKm = (x) => `${x.toFixed(x < 10 ? 2 : 1)} km`;

export function renderResults(root, p, features, { isDemo = false } = {}) {
  const $ = (k) => root.querySelector(`[data-r="${k}"]`);
  const b = p.baseline;
  $("demoTag").hidden = !isDemo;
  $("demoStory").hidden = !isDemo;
  if (isDemo) renderDemoStory($("demoStory"), p, features);
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
  const tribalList = (lands) => (lands || []).map((t) => `${esc(t.name)} (${fmtKm(t.km)})`).join(", ");
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
      .map((t) => `<li><code>${esc(t.code)}</code>${esc(t.reason)}</li>`).join("");
  }
  renderProfile($("profile"), $("profEnd"), p.profile || []);

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
function renderDemoStory(el, p, features) {
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
  el.innerHTML = `
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

function renderProfile(svg, endEl, profile) {
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
  area.setAttribute("fill", "currentColor"); // follows the theme text colour
  area.setAttribute("fill-opacity", "0.08");
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
  endEl.textContent =
    `${dMax.toFixed(1)} km · ${Math.round(zMin)}–${Math.round(zMax)} m · +${Math.round(climb)} / −${Math.round(descent)} m`;
}
