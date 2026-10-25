/* Historical outlook: summarizes the last ~3 months at points along the route and projects
   how many days might cross your limits over the next 2 weeks. A statistical estimate, not a forecast. */
(() => {
  "use strict";

  const API = (window.APP_CONFIG && window.APP_CONFIG.API_BASE) || "";
  const $ = (id) => document.getElementById(id);

  const need = ["btnOutlook", "outlookStatus", "outlookBody", "outlookTiles",
                "outlookChart", "outlookStrip", "outlookNote"];
  const missing = need.filter((id) => !$(id));
  if (missing.length) {
    console.error("outlook.js: missing elements in index.html:", missing.join(", "));
    return;
  }
  const button = $("btnOutlook"), statusEl = $("outlookStatus"), body = $("outlookBody");
  const tiles = $("outlookTiles"), chart = $("outlookChart"), strip = $("outlookStrip"), note = $("outlookNote");

  const HORIZON = 14, PAST = 92;
  const LEVELS = ["ok", "warn", "bad"];
  const ICON = ["✓", "!", "⚠"];
  const STATUS = ["Rare", "Possible", "Likely"];
  const COLORS = { ok: "#3ddc84", warn: "#ffb020", bad: "#ff5a5a" };
  const NS = "http://www.w3.org/2000/svg";

  const r = (v, d = 3) => (v === null || v === undefined ? null : Number(v.toFixed(d)));
  // expected days over a limit during the horizon -> 0 rare, 1 possible, 2 likely
  const levelOf = (days) => (days === null || days === undefined ? 0 : days >= 2 ? 2 : days >= 0.5 ? 1 : 0);

  function el(tag, className, text) {
    const e = document.createElement(tag);
    if (className) e.className = className;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function svg(tag, attrs = {}) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    return e;
  }

  const metrics = (T, d) => [
    { key: "gust", label: "Strong gusts", limit: `≥ ${T.gust} km/h` },
    { key: "wind", label: "High winds", limit: `≥ ${T.wind} km/h` },
    { key: "heat", label: "Heat", limit: `max ≥ ${T.heat}°C` },
    { key: "cold", label: "Cold", limit: `min ≤ ${T.cold}°C` },
    { key: "rain", label: "Heavy rain days", limit: `≥ ${d.rain_day_mm} mm/day` },
  ];

  function drawTiles(d, T) {
    tiles.replaceChildren();
    for (const m of metrics(T, d)) {
      let worst = null;
      for (const p of d.points) {
        const s = p.stats[m.key];
        if (s.expected_days !== null && (!worst || s.expected_days > worst.s.expected_days)) worst = { p, s };
      }
      const level = worst ? levelOf(worst.s.expected_days) : 0;
      const t = el("div", `wx-tile lvl-${LEVELS[level]}`);
      const top = el("div", "wx-top");
      top.append(el("span", "k", m.label), el("span", "wx-state", `${ICON[level]} ${STATUS[level]}`));
      const track = el("div", "wx-track");
      const fill = el("i", LEVELS[level]);
      fill.style.width = `${Math.min(100, worst ? (worst.s.expected_days / d.horizon_days) * 100 : 0)}%`;
      track.appendChild(fill);
      t.append(
        top,
        el("div", "wx-val", worst ? `${r(worst.s.expected_days)} of ${d.horizon_days} days` : "N/A"),
        track,
        el("div", "muted small", worst
          ? `worst point ${worst.p.id} · ${m.limit} · hit on ${worst.s.days_over_all} of the last ${d.dates.length} days`
          : m.limit)
      );
      tiles.appendChild(t);
    }
  }

  function drawChart(d, T) {
    chart.replaceChildren();
    const n = d.dates.length;
    if (n < 2) return;
    const W = 300, H = 80, pad = 6;
    // worst gust across the sampled points, per day
    const daily = d.dates.map((_, i) => {
      const vals = d.points.map((p) => p.series.gust[i]).filter((v) => v !== null && v !== undefined);
      return vals.length ? Math.max(...vals) : 0;
    });
    const yMax = Math.max(T.gust * 1.15, ...daily);
    const X = (i) => pad + (i / (n - 1)) * (W - 2 * pad);
    const Y = (v) => H - pad - (v / yMax) * (H - 2 * pad);

    const rs = Math.max(0, n - d.recent_days);
    chart.appendChild(svg("rect", { x: X(rs), y: 0, width: W - X(rs), height: H, class: "wx-recent" }));
    chart.appendChild(svg("line", { x1: 0, x2: W, y1: Y(T.gust), y2: Y(T.gust), class: "wx-limit" }));
    chart.appendChild(svg("path", {
      d: daily.map((v, i) => `${i ? "L" : "M"}${X(i).toFixed(1)} ${Y(v).toFixed(1)}`).join(" "),
      class: "wx-gust",
    }));
    daily.forEach((v, i) => {
      if (v < T.gust) return;
      chart.appendChild(svg("circle", {
        cx: X(i).toFixed(1), cy: Y(v).toFixed(1), r: 3, fill: COLORS.bad,
        stroke: "#12161c", "stroke-width": 1, "vector-effect": "non-scaling-stroke",
      }));
    });
  }

  function drawStrip(d, T, points) {
    strip.replaceChildren();
    const ms = metrics(T, d);
    d.points.forEach((p, i) => {
      const lv = ms.map((m) => levelOf(p.stats[m.key].expected_days));
      const level = Math.max(...lv);
      const cell = el("div", `wx-cell ${LEVELS[level]}`);
      const flagged = ms.filter((m, k) => lv[k] > 0)
        .map((m) => `${m.label}: ~${r(p.stats[m.key].expected_days)} days`);
      const km = points[i] ? `${r(points[i].km, 1)} km` : p.id;
      cell.title = `${p.id || km}: ${flagged.length ? flagged.join("; ") : "rare"}`;
      strip.appendChild(cell);
    });
  }

  function drawNote(d) {
    const avg = (get) => {
      const xs = d.points.map(get).filter((v) => v !== null && v !== undefined);
      return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
    };
    const line = (label, unit, key) => {
      const rec = avg((p) => p.trend[key].recent), bef = avg((p) => p.trend[key].before);
      if (rec === null || bef === null) return "";
      const arrow = rec > bef + 0.5 ? "↑" : rec < bef - 0.5 ? "↓" : "→";
      return `${label}: last ${d.recent_days} days avg ${r(rec, 1)}${unit} vs ${r(bef, 1)}${unit} before ${arrow}. `;
    };
    note.textContent =
      line("Max gust", " km/h", "gust") + line("Max temp", "°C", "tmax") +
      `Statistical estimate, not a forecast: it assumes the next ${d.horizon_days} days resemble the last ` +
      `${d.dates.length} (weighted toward the most recent ${d.recent_days}). It cannot see seasonal change, ` +
      `such as fall wind or winter rain. For the next 3 days, use the forecast above.`;
  }

  function reset() {
    body.hidden = true;
    tiles.replaceChildren();
    chart.replaceChildren();
    strip.replaceChildren();
    note.textContent = "";
    statusEl.textContent = "";
  }
  document.addEventListener("gentie:routechange", reset);

  button.addEventListener("click", async () => {
    const route = window.gentie && window.gentie.state.route;
    if (!route) { statusEl.textContent = "Compute or load a route first."; return; }
    const wx = window.gentieWx;
    if (!wx) { statusEl.textContent = "Weather panel did not load (see console)."; return; }

    button.disabled = true;
    try {
      reset();
      statusEl.textContent = "Analyzing the last 3 months…";
      const choice = wx.pointsSel.value === "auto" ? null : Number(wx.pointsSel.value);
      const { points } = wx.sampleRoute(route.segments, choice ? Math.min(choice, 20) : 10);
      const T = wx.T;
      const res = await fetch(`${API}/weather/outlook`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          locations: points.map(({ id, lat, lon }) => ({ id, lat, lon })),
          limits: { wind: T.wind, gust: T.gust, heat: T.heat, cold: T.cold },
          past_days: PAST,
          horizon_days: HORIZON,
        }),
      });
      if (!res.ok) {
        let detail = res.statusText;
        try { detail = (await res.json()).detail || detail; } catch (_) { /* not json */ }
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      const d = await res.json();
      if (window.gentie.state.route !== route) return; // route changed while loading
      if (!d.points.length) { statusEl.textContent = "No history returned."; return; }

      drawTiles(d, T);
      drawChart(d, T);
      drawStrip(d, T, points);
      drawNote(d);
      body.hidden = false;
      statusEl.textContent = `${d.points.length} points · ${d.dates.length} days of history · ${HORIZON}-day outlook`;
    } catch (err) {
      console.error("Outlook request failed:", err);
      statusEl.textContent = `Unable to load outlook: ${err.message}`;
    } finally {
      button.disabled = false;
    }
  });
})();