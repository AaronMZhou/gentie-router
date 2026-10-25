// Route weather: 3-day forecast (route map + point inspector) and a 92-day historical outlook
// (click a hazard to chart its history). Data: POST /weather/route and /weather/outlook.
import { apiSend } from "./api.js";

const $ = (id) => document.getElementById(id);
const T = { wind: 50, gust: 70, precip: 7.5, rainProb: 80, cold: -10, heat: 35 }; // screening limits
const CAUTION = 0.7;       // "caution" starts at 70% of a limit
const TEMP_MARGIN = 5;     // °C from the cold/heat limit that counts as caution
const SAMPLE_KM = 10, MAX_POINTS = 50, FORECAST_DAYS = 3, PLAY_MS = 350, HORIZON = 14, PAST = 92;
const LV = ["ok", "warn", "bad"], PILL = ["Clear", "Caution", "Problem"], STATUS = ["OK", "Near limit", "Over limit"];
const OSTATUS = ["Rare", "Possible", "Likely"];

const num = (v) => (v == null || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));
const r = (v, d = 1) => (v === null || v === undefined ? null : Number(v.toFixed(d)));
const f = (v, unit = "") => (v === null ? "N/A" : `${r(v)}${unit}`);

export const weatherCell = () => `
  <div class="cell sect" id="weather" style="grid-column:span 12"><div class="mono-label">Weather · Open-Meteo</div>
    <h6>Route <span class="it">weather</span></h6>
    <div class="wx-bar">
      <label class="wx-ctl">Points along route
        <select id="wxPoints">
          <option value="auto" selected>Auto (every ~10 km)</option>
          <option value="5">5 points</option><option value="10">10 points</option><option value="20">20 points</option>
          <option value="30">30 points</option><option value="50">50 points</option>
        </select>
      </label>
      <button id="btnWeather" class="btn btn--sm" type="button">Check forecast along route</button>
    </div>
    <p id="weatherStatus" class="muted small"></p>
    <div id="weatherBody" class="wx-body" hidden>
      <div class="wx-head"><div id="wxCounts" class="wx-counts"></div></div>
      <div class="wx-scrub">
        <b id="wxTimeLabel">Worst case, next 3 days</b>
        <div><input id="wxTime" type="range" min="0" max="0" value="0" step="1" aria-label="Forecast hour">
          <div class="wx-axis"><span>WORST CASE</span><span>+1 DAY</span><span>+2 DAYS</span><span>+3 DAYS</span></div></div>
        <button id="wxPlay" class="btn btn--sm" type="button">▶ Play</button>
      </div>
      <div class="wx-split">
        <div class="wx-map"><svg id="wxMap" viewBox="0 0 300 220" role="group" aria-label="Route points: select one to inspect"></svg></div>
        <div class="wx-insp" id="wxInsp"></div>
      </div>
    </div>

    <div class="wx-sub"><b>Historical outlook</b><span class="muted small">Estimated from the last 3 months. Not a forecast.</span></div>
    <div class="wx-bar"><button id="btnOutlook" class="btn btn--sm" type="button">Analyze past 3 months → 14-day outlook</button></div>
    <p id="outlookStatus" class="muted small"></p>
    <div id="outlookBody" class="wx-body" hidden>
      <div class="wx-rings" id="wxRings" role="group" aria-label="Hazard to chart"></div>
      <div><div class="mono-label" id="wxHistTitle"></div><div id="wxHist"></div></div>
      <div><div class="mono-label" id="wxStripTitle"></div><div id="outlookStrip" class="wx-strip"></div>
        <div class="wx-axis"><span>PROJECT SITE</span><span>SUBSTATION</span></div></div>
      <p id="outlookNote" class="muted small"></p>
    </div>
  </div>`;

// ---------------------------------------------------------------- route sampling
function sampleRoute(segments, override) {
  const coords = [];
  for (const seg of segments) {
    for (const c of seg.geometry.coordinates) {
      const prev = coords[coords.length - 1];
      if (!prev || prev[0] !== c[0] || prev[1] !== c[1]) coords.push(c);
    }
  }
  if (coords.length < 2) throw new Error("The route has too few coordinates.");
  const rad = (d) => (d * Math.PI) / 180;
  const distKm = (a, b) => {
    const h = Math.sin(rad(b[1] - a[1]) / 2) ** 2 + Math.cos(rad(a[1])) * Math.cos(rad(b[1])) * Math.sin(rad(b[0] - a[0]) / 2) ** 2;
    return 6371 * 2 * Math.asin(Math.sqrt(Math.min(1, h)));
  };
  const cum = [0];
  for (let i = 1; i < coords.length; i++) cum.push(cum[i - 1] + distKm(coords[i - 1], coords[i]));
  const total = cum[cum.length - 1];
  if (!(total > 0)) throw new Error("Could not calculate route length.");
  const count = override ? Math.min(MAX_POINTS, Math.max(2, override)) : Math.min(30, Math.max(2, Math.ceil(total / SAMPLE_KM) + 1));
  const at = (d) => {
    for (let i = 1; i < cum.length; i++) {
      if (d <= cum[i]) {
        const t = cum[i] > cum[i - 1] ? (d - cum[i - 1]) / (cum[i] - cum[i - 1]) : 0;
        return [coords[i - 1][0] + (coords[i][0] - coords[i - 1][0]) * t, coords[i - 1][1] + (coords[i][1] - coords[i - 1][1]) * t];
      }
    }
    return coords[coords.length - 1];
  };
  return {
    total, coords,
    points: Array.from({ length: count }, (_, i) => {
      const km = (total * i) / (count - 1), [lon, lat] = at(km);
      return { id: `km ${km.toFixed(1)}`, km, lat, lon };
    }),
  };
}

// ---------------------------------------------------------------- forecast analysis
function analyze(hours, allHours) {
  const col = (k) => hours.map((h) => num(h && h[k])).filter((v) => v !== null);
  const max = (a) => (a.length ? Math.max(...a) : null), min = (a) => (a.length ? Math.min(...a) : null);
  const temp = col("temperature_2m");
  const m = { minTemp: min(temp), maxTemp: max(temp), wind: max(col("wind_speed_10m")), gust: max(col("wind_gusts_10m")),
    precip: max(col("precipitation")), prob: max(col("precipitation_probability")) };
  const grade = (v, limit) => (v === null ? 0 : v >= limit ? 2 : v >= limit * CAUTION ? 1 : 0);
  const levels = {
    temp: m.minTemp === null ? 0 : m.minTemp <= T.cold || m.maxTemp >= T.heat ? 2
      : m.minTemp <= T.cold + TEMP_MARGIN || m.maxTemp >= T.heat - TEMP_MARGIN ? 1 : 0,
    wind: grade(m.wind, T.wind), gust: grade(m.gust, T.gust), precip: grade(m.precip, T.precip), prob: grade(m.prob, T.rainProb),
  };
  return { ...m, levels, level: Math.max(...Object.values(levels)), hours: allHours };
}

function tileHtml(label, value, unit, limit, level) {
  const pct = value === null ? 0 : Math.min((value / limit / 1.25) * 100, 100);
  return `<div class="wx-tile lv-${LV[level]}"><span class="k">${label}</span><span class="v">${f(value)}<small>${unit}</small></span>
    <div class="wx-track"><i style="width:${Math.max(2, pct)}%"></i><u style="left:80%"></u></div><span class="k">${STATUS[level]} · limit ${limit} ${unit}</span></div>`;
}

function tempTileHtml(a) {
  const LO = -20, HI = 45, pct = (x) => Math.max(0, Math.min(100, ((x - LO) / (HI - LO)) * 100)), level = a.levels.temp;
  const text = a.minTemp === null ? "N/A" : a.minTemp === a.maxTemp ? `${r(a.minTemp)}°C` : `${r(a.minTemp)}° to ${r(a.maxTemp)}°C`;
  const range = a.minTemp === null ? "" : `<i style="left:${pct(a.minTemp)}%;width:${Math.max(pct(a.maxTemp) - pct(a.minTemp), 1.5)}%"></i>`;
  return `<div class="wx-tile lv-${LV[level]}"><span class="k">Temperature</span><span class="v wx-vsm">${text}</span>
    <div class="wx-track"><b class="z" style="left:0;width:${pct(T.cold)}%"></b><b class="z" style="left:${pct(T.heat)}%;right:0"></b>${range}</div>
    <span class="k">${STATUS[level]} · limits ${T.cold}° / ${T.heat}°C</span></div>`;
}

// 3-day wind, gusts and rain probability for one point, with the selected hour marked
function sparkHtml(hours, nowIdx) {
  const n = hours.length;
  if (n < 2) return "";
  const W = 320, H = 70, X = (i) => ((i / (n - 1)) * W).toFixed(1);
  const yMax = Math.max(T.gust * 1.1, ...hours.map((h) => num(h.wind_gusts_10m) ?? 0));
  const Y = (v) => (H - 6 - (v / yMax) * (H - 12)).toFixed(1);
  const line = (key) => { let d = "", pen = false;
    hours.forEach((h, i) => { const v = num(h[key]); if (v === null) { pen = false; return; } d += `${pen ? "L" : "M"}${X(i)} ${Y(v)} `; pen = true; });
    return d; };
  const prob = hours.map((h, i) => `L${X(i)} ${(H - ((num(h.precipitation_probability) ?? 0) / 100) * (H - 6)).toFixed(1)}`).join(" ");
  const days = [24, 48].filter((i) => i < n).map((i) => `<line x1="${X(i)}" x2="${X(i)}" y1="0" y2="${H}" class="day"/>`).join("");
  return `<svg viewBox="0 0 ${W} ${H + 12}" width="100%" role="img" aria-label="Wind, gusts and rain probability over the next 3 days at this point">
    ${days}<path d="M0 ${H} ${prob} L${W} ${H} Z" class="prob"/><line class="limit" x1="0" x2="${W}" y1="${Y(T.gust)}" y2="${Y(T.gust)}"/>
    <path d="${line("wind_speed_10m")}" class="wind"/><path d="${line("wind_gusts_10m")}" class="gust"/>
    ${nowIdx >= 0 && nowIdx < n ? `<line x1="${X(nowIdx)}" x2="${X(nowIdx)}" y1="0" y2="${H}" class="now"/>` : ""}
    <text x="0" y="${H + 10}">now</text><text x="${W / 2}" y="${H + 10}" text-anchor="middle">+1.5 days</text><text x="${W}" y="${H + 10}" text-anchor="end">+3 days</text></svg>
    <div class="wx-key"><span class="k-gust">Gusts</span><span class="k-wind">Wind</span><span class="k-prob">Rain chance</span><span class="k-limit">Gust limit ${T.gust}</span></div>`;
}

// ---------------------------------------------------------------- map markers on the 3D view
function mapMarkers(viewer) {
  let layer = null;
  const pump = (ms = 2000) => { const s = viewer.scene, until = performance.now() + ms;
    const p = () => { s.requestRender(); if (performance.now() < until) setTimeout(p, 120); }; p(); };
  const COLORS = { ok: "#3ddc84", warn: "#ffb020", bad: "#ff5a5a" };
  return {
    draw(rows) {
      if (!layer) { layer = new Cesium.CustomDataSource("weather"); viewer.dataSources.add(layer); }
      layer.entities.suspendEvents();
      layer.entities.removeAll();
      for (const row of rows) {
        layer.entities.add({
          position: Cesium.Cartesian3.fromDegrees(row.point.lon, row.point.lat),
          point: { pixelSize: row.a.level === 2 ? 18 : row.a.level ? 14 : 10, color: Cesium.Color.fromCssColorString(COLORS[LV[row.a.level]]),
            outlineColor: Cesium.Color.WHITE, outlineWidth: 2, heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
            disableDepthTestDistance: Number.POSITIVE_INFINITY },
        });
      }
      layer.entities.resumeEvents();
      pump();
    },
    clear() { if (layer) viewer.dataSources.remove(layer, true); layer = null; },
    flyTo(p) {
      viewer.camera.flyToBoundingSphere(new Cesium.BoundingSphere(Cesium.Cartesian3.fromDegrees(p.lon, p.lat, 0), 500),
        { offset: new Cesium.HeadingPitchRange(viewer.camera.heading, Cesium.Math.toRadians(-40), 6000), duration: 1.2 });
      pump(1800);
    },
  };
}

// ---------------------------------------------------------------- page wiring
export function initWeather(map, fc) {
  const segments = fc.features.filter((x) => x.properties.kind === "segment");
  const markers = mapMarkers(map.viewer);
  const choice = () => ($("wxPoints").value === "auto" ? null : Number($("wxPoints").value));
  const err = (el, e) => { console.error(e); el.textContent = e.detail || e.message || String(e); };
  let sampled = null; // last sampled route, shared with the outlook

  // ---------- forecast
  let data = null, sel = 0, cur = 0, playTimer = null;
  const status = $("weatherStatus"), slider = $("wxTime");
  const rowsFor = (s) => data.items.map((it) => ({ point: it.point, a: analyze(s === 0 ? it.hours : [it.hours[s - 1]], it.hours) }));
  const hourText = (s) => {
    if (s === 0) return "Worst case, next 3 days";
    const t = data.items[0].hours[s - 1]?.time;
    return t ? new Date(t).toLocaleString([], { weekday: "short", hour: "numeric" }) : `Hour ${s}`;
  };

  function drawRouteMap(rows) {
    const co = sampled.coords, k = Math.cos((co[0][1] * Math.PI) / 180);
    const xs = co.map((c) => c[0] * k), ys = co.map((c) => c[1]);
    const x0 = Math.min(...xs), y0 = Math.min(...ys), dx = Math.max(...xs) - x0 || 1e-6, dy = Math.max(...ys) - y0 || 1e-6;
    const W = 300, H = 220, pad = 22, s = Math.min((W - 2 * pad) / dx, (H - 2 * pad) / dy);
    const ox = (W - dx * s) / 2, oy = (H - dy * s) / 2;
    const P = (lon, lat) => [ox + (lon * k - x0) * s, H - oy - (lat - y0) * s];
    const step = Math.max(1, Math.ceil(co.length / 160));
    const path = co.filter((_, i) => i % step === 0 || i === co.length - 1).map((c) => P(c[0], c[1]).map((v) => v.toFixed(1)).join(",")).join(" ");
    const last = rows.length - 1, a = P(rows[0].point.lon, rows[0].point.lat), b = P(rows[last].point.lon, rows[last].point.lat);
    $("wxMap").innerHTML = `<polyline points="${path}" class="route"/>
      <text x="${a[0]}" y="${a[1] + 22}" text-anchor="middle">SITE</text><text x="${b[0]}" y="${b[1] - 16}" text-anchor="middle">SUBSTATION</text>` +
      rows.map((row, i) => { const [x, y] = P(row.point.lon, row.point.lat);
        return `<g class="pt lv-${LV[row.a.level]}" data-i="${i}" role="button" tabindex="0" aria-pressed="${i === cur}" aria-label="${row.point.id}, ${PILL[row.a.level]}">
          <circle class="ring" cx="${x}" cy="${y}" r="12"/><circle class="dot" cx="${x}" cy="${y}" r="${row.a.level === 2 ? 9 : row.a.level ? 7.5 : 6}"/></g>`; }).join("");
  }

  function drawInspector(rows) {
    const row = rows[cur], a = row.a;
    $("wxInsp").innerHTML = `<div class="wx-insp-h"><b>${row.point.id}</b><span class="wx-pill lv-${LV[a.level]}">${PILL[a.level]}</span></div>
      <div class="wx-tiles">${tempTileHtml(a)}${tileHtml("Wind", a.wind, "km/h", T.wind, a.levels.wind)}${tileHtml("Gusts", a.gust, "km/h", T.gust, a.levels.gust)}
        ${tileHtml("Precipitation", a.precip, "mm/h", T.precip, a.levels.precip)}${tileHtml("Rain probability", a.prob, "%", T.rainProb, a.levels.prob)}</div>
      ${sparkHtml(a.hours, sel - 1)}`;
  }

  function render() {
    const rows = rowsFor(sel);
    const n = [0, 1, 2].map((l) => rows.filter((x) => x.a.level === l).length);
    $("wxCounts").innerHTML = [2, 1, 0].map((l) => `<span class="wx-pill lv-${LV[l]}"><b>${n[l]}</b> ${PILL[l]}</span>`).join("");
    $("wxTimeLabel").textContent = hourText(sel);
    drawRouteMap(rows);
    drawInspector(rows);
    markers.draw(rows);
  }

  function pick(i) {
    cur = i;
    render();
    markers.flyTo(data.items[i].point);
  }
  $("wxMap").addEventListener("click", (e) => { const g = e.target.closest(".pt"); if (g) pick(+g.dataset.i); });
  $("wxMap").addEventListener("keydown", (e) => {
    const g = e.target.closest(".pt");
    if (g && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); pick(+g.dataset.i); }
  });

  const stopPlay = () => { clearInterval(playTimer); playTimer = null; $("wxPlay").textContent = "▶ Play"; };
  const setSel = (v) => { sel = v; slider.value = String(v); render(); };
  slider.addEventListener("input", () => { stopPlay(); setSel(Number(slider.value)); });
  $("wxPlay").addEventListener("click", () => {
    if (!data) return;
    if (playTimer) return stopPlay();
    $("wxPlay").textContent = "■ Stop";
    playTimer = setInterval(() => setSel(sel >= data.n ? 1 : sel + 1), PLAY_MS);
  });

  $("btnWeather").addEventListener("click", async () => {
    const btn = $("btnWeather");
    btn.disabled = true;
    try {
      stopPlay(); markers.clear(); data = null; sel = 0;
      $("weatherBody").hidden = true;
      status.textContent = "Loading forecast…";
      sampled = sampleRoute(segments, choice());
      const res = await apiSend("POST", "/weather/route", {
        locations: sampled.points.map(({ id, lat, lon }) => ({ id, lat, lon })), forecast_days: FORECAST_DAYS });
      const locations = res.locations || [];
      if (!locations.length) { status.textContent = "No weather data returned."; return; }
      const items = locations.map((loc, i) => ({ point: sampled.points[i], hours: loc.hourly || [] }));
      data = { items, n: Math.min(...items.map((it) => it.hours.length)) };
      slider.max = String(data.n);
      slider.value = "0";
      const rows = rowsFor(0); // open on the worst point
      cur = rows.reduce((best, x, i) => (x.a.level > rows[best].a.level || (x.a.level === rows[best].a.level && (x.a.gust ?? 0) > (rows[best].a.gust ?? 0)) ? i : best), 0);
      $("weatherBody").hidden = false;
      render();
      status.textContent = `${items.length} points · ${FORECAST_DAYS}-day forecast · pick a point on the route, or drag the slider to see a specific hour`;
    } catch (e) {
      err(status, e);
      status.textContent = `Unable to load weather: ${status.textContent}`;
    } finally {
      btn.disabled = false;
    }
  });

  // ---------- historical outlook
  let od = null, metric = "wind";
  const ostatus = $("outlookStatus");
  const levelOf = (days) => (days == null ? 0 : days >= 2 ? 2 : days >= 0.5 ? 1 : 0);
  const metrics = () => [
    { key: "gust", label: "Strong gusts", unit: "km/h", limit: T.gust, dir: 1, series: "gust", text: `≥ ${T.gust} km/h` },
    { key: "wind", label: "High winds", unit: "km/h", limit: T.wind, dir: 1, series: "wind", text: `≥ ${T.wind} km/h` },
    { key: "heat", label: "Heat", unit: "°C", limit: T.heat, dir: 1, series: "tmax", text: `max ≥ ${T.heat}°C` },
    { key: "cold", label: "Cold", unit: "°C", limit: T.cold, dir: -1, series: "tmin", text: `min ≤ ${T.cold}°C` },
    { key: "rain", label: "Heavy rain days", unit: "mm/day", limit: od.rain_day_mm, dir: 1, series: "rain", text: `≥ ${od.rain_day_mm} mm/day` },
  ];
  const fmtDate = (iso) => new Date(`${iso}T00:00`).toLocaleDateString([], { month: "short", day: "numeric" });

  function drawRings() {
    $("wxRings").innerHTML = metrics().map((m) => {
      let worst = null;
      for (const p of od.points) { const s = p.stats[m.key]; if (s.expected_days != null && (!worst || s.expected_days > worst.s.expected_days)) worst = { p, s }; }
      const e = worst ? worst.s.expected_days : null, l = levelOf(e), frac = e ? Math.max(3, Math.min(100, (e / od.horizon_days) * 100)) : 0;
      return `<button type="button" class="wx-ring lv-${LV[l]}" data-m="${m.key}" aria-pressed="${m.key === metric}">
        <span class="wx-donut" style="--p:${frac}" data-v="${e === null ? "–" : r(e)}"></span>
        <span class="nm">${m.label}<span>${OSTATUS[l]} · ${m.text}</span><span>${e === null ? "" : `of ${od.horizon_days} days`}</span></span></button>`;
    }).join("");
  }

  // worst point per day for the selected hazard, over the last 92 days
  function drawHistory() {
    const m = metrics().find((x) => x.key === metric), n = od.dates.length;
    $("wxHistTitle").textContent = `${m.label} along route, last ${n} days (worst point each day)`;
    const daily = od.dates.map((_, i) => {
      const vals = od.points.map((p) => p.series[m.series][i]).filter((v) => v != null);
      return vals.length ? (m.dir === 1 ? Math.max(...vals) : Math.min(...vals)) : null;
    });
    const real = daily.filter((v) => v !== null);
    if (n < 2 || !real.length) { $("wxHist").innerHTML = `<p class="muted small">No daily data.</p>`; return; }
    const W = 640, H = 130, L = 40, R = 8, top = 8, bot = 18;
    let lo = Math.min(...real, m.limit), hi = Math.max(...real, m.limit);
    if (m.dir === 1 && m.series !== "tmax") lo = Math.min(0, lo); // speeds and rain start at zero; temperatures keep their own range
    const padv = (hi - lo) * 0.08 || 1; lo -= m.series === "tmax" || m.series === "tmin" ? padv : 0; hi += padv;
    const X = (i) => L + (i / (n - 1)) * (W - L - R), Y = (v) => H - bot - ((v - lo) / (hi - lo)) * (H - bot - top);
    const over = (v) => v !== null && (m.dir === 1 ? v >= m.limit : v <= m.limit);
    const rs = Math.max(0, n - od.recent_days);
    let d = "", pen = false;
    daily.forEach((v, i) => { if (v === null) { pen = false; return; } d += `${pen ? "L" : "M"}${X(i).toFixed(1)} ${Y(v).toFixed(1)} `; pen = true; });
    $("wxHist").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${m.label}, worst daily value over the last ${n} days">
      <rect x="${X(rs)}" y="${top}" width="${W - R - X(rs)}" height="${H - bot - top}" class="recent"/>
      <line class="limit" x1="${L}" x2="${W - R}" y1="${Y(m.limit)}" y2="${Y(m.limit)}"/>
      <text x="${L - 4}" y="${Y(hi) + 8}" text-anchor="end">${r(hi, 0)}</text><text x="${L - 4}" y="${Y(lo) + 3}" text-anchor="end">${r(lo, 0)}</text>
      <text x="${L - 4}" y="${Y(m.limit) + 3}" text-anchor="end" class="lim">${m.limit}</text>
      <text x="${W - R}" y="${Y(m.limit) + (m.dir === 1 ? -4 : 11)}" text-anchor="end">limit ${m.limit} ${m.unit}</text>
      <path d="${d}" class="series"/>
      ${daily.map((v, i) => (over(v) ? `<circle cx="${X(i).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="3.2" class="hit"><title>${fmtDate(od.dates[i])}: ${r(v)} ${m.unit}</title></circle>` : "")).join("")}
      <text x="${L}" y="${H - 3}">${fmtDate(od.dates[0])}</text><text x="${X(rs) + 4}" y="${top + 8}">last ${od.recent_days} days</text>
      <text x="${W - R}" y="${H - 3}" text-anchor="end">${fmtDate(od.dates[n - 1])}</text></svg>
      <div class="wx-key"><span class="k-series">Daily worst</span><span class="k-hit">Passed the limit</span><span class="k-limit">Limit</span></div>`;
  }

  function drawOutlookStrip() {
    const m = metrics().find((x) => x.key === metric);
    $("wxStripTitle").textContent = `Outlook by point · ${m.label.toLowerCase()}`;
    $("outlookStrip").innerHTML = od.points.map((p, i) => {
      const e = p.stats[m.key].expected_days, l = levelOf(e), km = sampled.points[i] ? `${r(sampled.points[i].km)} km` : p.id;
      return `<span class="wx-cell lv-${LV[l]}" title="${p.id || km}: ~${e === null ? "N/A" : r(e)} of ${od.horizon_days} days (${OSTATUS[l].toLowerCase()})"></span>`;
    }).join("");
  }

  function drawNote() {
    const avg = (get) => { const xs = od.points.map(get).filter((v) => v != null); return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null; };
    const line = (label, unit, key) => {
      const rec = avg((p) => p.trend[key].recent), bef = avg((p) => p.trend[key].before);
      if (rec === null || bef === null) return "";
      return `${label}: last ${od.recent_days} days avg ${r(rec)}${unit} vs ${r(bef)}${unit} before ${rec > bef + 0.5 ? "↑" : rec < bef - 0.5 ? "↓" : "→"}. `;
    };
    $("outlookNote").textContent = line("Max gust", " km/h", "gust") + line("Max temp", "°C", "tmax") +
      `Statistical estimate, not a forecast: it assumes the next ${od.horizon_days} days resemble the last ${od.dates.length} (weighted toward the most recent ${od.recent_days}). ` +
      "It cannot see seasonal change, such as fall wind or winter rain. For the next 3 days, use the forecast above.";
  }

  $("wxRings").addEventListener("click", (e) => {
    const b = e.target.closest(".wx-ring");
    if (!b) return;
    metric = b.dataset.m;
    $("wxRings").querySelectorAll(".wx-ring").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    drawHistory();
    drawOutlookStrip();
  });

  $("btnOutlook").addEventListener("click", async () => {
    const btn = $("btnOutlook");
    btn.disabled = true;
    try {
      $("outlookBody").hidden = true;
      ostatus.textContent = "Analyzing the last 3 months…";
      const ch = choice();
      sampled = sampleRoute(segments, ch ? Math.min(ch, 20) : 10); // the outlook API takes at most 20 points
      od = await apiSend("POST", "/weather/outlook", {
        locations: sampled.points.map(({ id, lat, lon }) => ({ id, lat, lon })),
        limits: { wind: T.wind, gust: T.gust, heat: T.heat, cold: T.cold }, past_days: PAST, horizon_days: HORIZON });
      if (!od.points.length) { ostatus.textContent = "No history returned."; return; }
      metric = "wind"; // default view
      drawRings(); drawHistory(); drawOutlookStrip(); drawNote();
      $("outlookBody").hidden = false;
      ostatus.textContent = `${od.points.length} points · ${od.dates.length} days of history · ${HORIZON}-day outlook · click a hazard to chart it`;
    } catch (e) {
      err(ostatus, e);
      ostatus.textContent = `Unable to load outlook: ${ostatus.textContent}`;
    } finally {
      btn.disabled = false;
    }
  });
}
