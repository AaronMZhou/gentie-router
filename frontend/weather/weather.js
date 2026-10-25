/* Route weather: samples points along the route, fetches forecasts, and shows color-coded severity
   (worst case or a chosen hour) as banner, strip, chart, map markers and cards. */
(() => {
  "use strict";

  const API = (window.APP_CONFIG && window.APP_CONFIG.API_BASE) || "";
  const $ = (id) => document.getElementById(id);

  const need = ["btnWeather", "weatherStatus", "weatherSummary", "weatherStrip", "weatherChart",
                "weatherChartEnd", "weatherResults", "wxPoints", "wxTime", "wxTimeLabel", "wxPlay"];
  const missing = need.filter((id) => !$(id));
  if (missing.length) {
    console.error("weather.js: missing elements in index.html:", missing.join(", "));
    const b = $("btnWeather");
    if (b) b.addEventListener("click", () => alert("Weather panel is missing: " + missing.join(", ")));
    return;
  }
  const button = $("btnWeather"), statusEl = $("weatherStatus");
  const summary = $("weatherSummary"), strip = $("weatherStrip"), chart = $("weatherChart");
  const results = $("weatherResults"), pointsSel = $("wxPoints");
  const slider = $("wxTime"), timeLabel = $("wxTimeLabel"), playBtn = $("wxPlay");

  // Screening thresholds - adjust for your project.
  const T = {
    wind: 50,      // km/h
    gust: 70,      // km/h
    precip: 7.5,   // mm in one hour
    rainProb: 80,  // %
    cold: -10,     // °C
    heat: 35,      // °C
  };
  const CAUTION = 0.7;       // "caution" starts at 70% of a limit
  const TEMP_MARGIN = 5;     // °C from the cold/heat limit that counts as caution
  const DECIMALS = 3;        // max decimals shown for any number
  const SAMPLE_KM = 10, MAX_POINTS = 50, FORECAST_DAYS = 3;
  const PLAY_MS = 350;       // time per hour while playing

  const LEVELS = ["ok", "warn", "bad"];
  const STATUS = ["OK", "Near limit", "Over limit"];
  const ICON = ["✓", "!", "⚠"];
  const PILL = ["Clear", "Caution", "Problem"];
  const COLORS = { ok: "#3ddc84", warn: "#ffb020", bad: "#ff5a5a" };
  const NS = "http://www.w3.org/2000/svg";

  const num = (v) => (v == null || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));
  const r = (v, d = DECIMALS) => (v === null ? null : Number(v.toFixed(d))); // rounds and trims trailing zeros
  const fmt = (v, unit) => (v === null ? "N/A" : `${r(v)}${unit}`);

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

  // ---- sample evenly spaced points along the route (segments are in route order)
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
      const h = Math.sin(rad(b[1] - a[1]) / 2) ** 2 +
        Math.cos(rad(a[1])) * Math.cos(rad(b[1])) * Math.sin(rad(b[0] - a[0]) / 2) ** 2;
      return 6371 * 2 * Math.asin(Math.sqrt(Math.min(1, h)));
    };
    const cum = [0];
    for (let i = 1; i < coords.length; i++) cum.push(cum[i - 1] + distKm(coords[i - 1], coords[i]));
    const total = cum[cum.length - 1];
    if (!(total > 0)) throw new Error("Could not calculate route length.");

    const count = override
      ? Math.min(MAX_POINTS, Math.max(2, override))
      : Math.min(30, Math.max(2, Math.ceil(total / SAMPLE_KM) + 1));
    const at = (d) => {
      for (let i = 1; i < cum.length; i++) {
        if (d <= cum[i]) {
          const f = cum[i] > cum[i - 1] ? (d - cum[i - 1]) / (cum[i] - cum[i - 1]) : 0;
          return [coords[i - 1][0] + (coords[i][0] - coords[i - 1][0]) * f,
                  coords[i - 1][1] + (coords[i][1] - coords[i - 1][1]) * f];
        }
      }
      return coords[coords.length - 1];
    };
    return {
      total,
      points: Array.from({ length: count }, (_, i) => {
        const km = (total * i) / (count - 1);
        const [lon, lat] = at(km);
        return { id: `km ${km.toFixed(1)}`, km, lat, lon };
      }),
    };
  }

  // ---- summarize the given hours (all 72 = worst case, or a single hour); allHours feeds the sparkline
  function analyze(hours, allHours) {
    const col = (k) => hours.map((h) => num(h && h[k])).filter((v) => v !== null);
    const max = (a) => (a.length ? Math.max(...a) : null);
    const min = (a) => (a.length ? Math.min(...a) : null);
    const temp = col("temperature_2m");
    const m = {
      minTemp: min(temp), maxTemp: max(temp),
      wind: max(col("wind_speed_10m")), gust: max(col("wind_gusts_10m")),
      precip: max(col("precipitation")), prob: max(col("precipitation_probability")),
    };
    const grade = (v, limit) => (v === null ? 0 : v >= limit ? 2 : v >= limit * CAUTION ? 1 : 0);
    const tempLevel =
      m.minTemp === null ? 0
      : m.minTemp <= T.cold || m.maxTemp >= T.heat ? 2
      : m.minTemp <= T.cold + TEMP_MARGIN || m.maxTemp >= T.heat - TEMP_MARGIN ? 1 : 0;
    const levels = {
      temp: tempLevel,
      wind: grade(m.wind, T.wind), gust: grade(m.gust, T.gust),
      precip: grade(m.precip, T.precip), prob: grade(m.prob, T.rainProb),
    };
    const names = {
      temp: m.minTemp !== null && (m.minTemp <= T.cold + TEMP_MARGIN && m.minTemp < (T.heat + T.cold) / 2) ? "Low temperatures" : "High temperatures",
      wind: "High winds", gust: "Strong wind gusts", precip: "Heavy precipitation", prob: "High rain probability",
    };
    const alerts = Object.keys(levels).filter((k) => levels[k] === 2).map((k) => names[k]);
    const cautions = Object.keys(levels).filter((k) => levels[k] === 1).map((k) => names[k]);
    const level = Math.max(...Object.values(levels));
    return { ...m, levels, alerts, cautions, level, hours: allHours };
  }

  // ---- visual pieces
  // One metric: label + status, big value, bar (tick = limit), limit text.
  function tile(label, value, unit, limit, level) {
    const t = el("div", `wx-tile lvl-${LEVELS[level]}`);
    const top = el("div", "wx-top");
    top.append(el("span", "k", label), el("span", "wx-state", `${ICON[level]} ${STATUS[level]}`));
    const track = el("div", "wx-track");
    const ratio = value === null ? 0 : value / limit;
    const fill = el("i", LEVELS[level]);
    fill.style.width = `${Math.min(ratio / 1.25, 1) * 100}%`;
    track.append(fill, el("b", "wx-tick"));
    t.append(top, el("div", "wx-val", fmt(value, unit)), track, el("div", "muted small", `limit ${limit}${unit}`));
    return t;
  }

  // Temperature as a min-max range on a fixed -20..45 °C scale with cold/heat zones.
  function tempTile(a) {
    const LO = -20, HI = 45, pct = (x) => Math.max(0, Math.min(100, ((x - LO) / (HI - LO)) * 100));
    const level = a.levels.temp;
    const t = el("div", `wx-tile wide lvl-${LEVELS[level]}`);
    const top = el("div", "wx-top");
    top.append(el("span", "k", "Temperature"), el("span", "wx-state", `${ICON[level]} ${STATUS[level]}`));
    const track = el("div", "wx-track wx-temp");
    const cold = el("u", "wx-zone cold"); cold.style.width = `${pct(T.cold)}%`;
    const heat = el("u", "wx-zone heat"); heat.style.left = `${pct(T.heat)}%`; heat.style.right = "0";
    track.append(cold, heat);
    if (a.minTemp !== null) {
      const range = el("i", LEVELS[level]);
      range.style.left = `${pct(a.minTemp)}%`;
      range.style.width = `${Math.max(pct(a.maxTemp) - pct(a.minTemp), 1.5)}%`;
      track.appendChild(range);
    }
    const text = a.minTemp === null ? "N/A"
      : a.minTemp === a.maxTemp ? `${r(a.minTemp)}°C`
      : `${r(a.minTemp)}° to ${r(a.maxTemp)}°C`;
    t.append(top, el("div", "wx-val", text), track, el("div", "muted small", `limits ${T.cold}°C / ${T.heat}°C`));
    return t;
  }

  // 3-day sparkline: rain probability (area), wind and gusts (lines), gust threshold (dashed),
  // and a marker for the selected hour.
  function sparkline(hours, nowIdx) {
    const n = hours.length;
    if (n < 2) return null;
    const W = 240, H = 56;
    const wind = hours.map((h) => num(h.wind_speed_10m));
    const gust = hours.map((h) => num(h.wind_gusts_10m));
    const prob = hours.map((h) => num(h.precipitation_probability) ?? 0);
    const yMax = Math.max(T.gust * 1.1, ...gust.filter((v) => v !== null));
    const X = (i) => ((i / (n - 1)) * W).toFixed(1);
    const Y = (v) => (H - (v / yMax) * H).toFixed(1);
    const line = (vals) => {
      let d = "", pen = false;
      vals.forEach((v, i) => {
        if (v === null) { pen = false; return; }
        d += `${pen ? "L" : "M"}${X(i)} ${Y(v)} `;
        pen = true;
      });
      return d;
    };
    const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", class: "wx-spark" });
    for (let i = 24; i < n; i += 24) s.appendChild(svg("line", { x1: X(i), x2: X(i), y1: 0, y2: H, class: "wx-day" }));
    s.appendChild(svg("path", {
      d: `M0 ${H} ` + prob.map((p, i) => `L${X(i)} ${(H - (p / 100) * H).toFixed(1)}`).join(" ") + ` L${W} ${H} Z`,
      class: "wx-prob",
    }));
    s.appendChild(svg("line", { x1: 0, x2: W, y1: Y(T.gust), y2: Y(T.gust), class: "wx-limit" }));
    s.appendChild(svg("path", { d: line(wind), class: "wx-wind" }));
    s.appendChild(svg("path", { d: line(gust), class: "wx-gust" }));
    if (nowIdx >= 0 && nowIdx < n) s.appendChild(svg("line", { x1: X(nowIdx), x2: X(nowIdx), y1: 0, y2: H, class: "wx-now" }));

    const wrap = el("div", "wx-sparkwrap");
    const legend = el("div", "wx-legend small muted");
    legend.innerHTML = '<span class="l-wind">wind</span><span class="l-gust">gusts</span><span class="l-prob">rain %</span><span class="l-limit">gust limit</span>';
    wrap.append(el("div", "k", "Next 3 days"), s, legend);
    return wrap;
  }

  function card(point, a, nowIdx) {
    const c = el("article", `weather-card lvl-${LEVELS[a.level]}`);
    const head = el("div", "weather-head");
    head.append(el("h4", "", point.id), el("span", `wx-pill ${LEVELS[a.level]}`, `${ICON[a.level]} ${PILL[a.level]}`));
    c.append(head, el("div", "muted small weather-coords", `${r(point.lat)}, ${r(point.lon)}`));

    if (a.alerts.length || a.cautions.length) {
      const chips = el("div", "chips");
      a.alerts.forEach((t) => chips.appendChild(el("span", "chip bad", t)));
      a.cautions.forEach((t) => chips.appendChild(el("span", "chip best", `${t} (near limit)`)));
      c.appendChild(chips);
    }

    const tiles = el("div", "wx-tiles");
    tiles.append(
      tempTile(a),
      tile("Wind", a.wind, " km/h", T.wind, a.levels.wind),
      tile("Gusts", a.gust, " km/h", T.gust, a.levels.gust),
      tile("Precipitation", a.precip, " mm/h", T.precip, a.levels.precip),
      tile("Rain probability", a.prob, "%", T.rainProb, a.levels.prob)
    );
    c.appendChild(tiles);
    const sp = sparkline(a.hours, nowIdx);
    if (sp) c.appendChild(sp);
    return c;
  }

  // Big Problem / Caution / Clear counts + a key.
  function drawBanner(counts) {
    summary.querySelector(".wx-banner")?.remove();
    const b = el("div", "wx-banner");
    const stats = el("div", "wx-stats");
    [2, 1, 0].forEach((l) => {
      const s = el("div", `wx-stat ${LEVELS[l]}`);
      s.append(el("div", "wx-count", String(counts[l])), el("div", "wx-statlabel", `${ICON[l]} ${PILL[l]}`));
      stats.appendChild(s);
    });
    b.append(stats, el("div", "muted small",
      `Key: red = at/over a limit · amber = within ${Math.round((1 - CAUTION) * 100)}% of a limit · green = clear`));
    summary.prepend(b);
  }

  // Colored cells along the route; click to focus a point.
  function drawStrip(rows) {
    strip.replaceChildren();
    rows.forEach((row, i) => {
      const cell = el("button", `wx-cell ${LEVELS[row.a.level]}`);
      cell.type = "button";
      const detail = [...row.a.alerts, ...row.a.cautions.map((t) => `${t} (near limit)`)].join(", ");
      cell.title = `${row.point.id}: ${detail || "clear"}`;
      cell.setAttribute("aria-label", cell.title);
      cell.addEventListener("click", () => pick(i));
      strip.appendChild(cell);
    });
  }

  // Gust vs distance, dots colored by severity.
  function drawChart(rows, total) {
    chart.replaceChildren();
    const W = 300, H = 80, pad = 6;
    const gusts = rows.map((row) => row.a.gust ?? 0);
    const yMax = Math.max(T.gust * 1.15, ...gusts);
    const X = (km) => pad + (km / total) * (W - 2 * pad);
    const Y = (v) => H - pad - (v / yMax) * (H - 2 * pad);
    chart.appendChild(svg("line", { x1: 0, x2: W, y1: Y(T.gust), y2: Y(T.gust), class: "wx-limit" }));
    chart.appendChild(svg("path", {
      d: rows.map((row, i) => `${i ? "L" : "M"}${X(row.point.km).toFixed(1)} ${Y(gusts[i]).toFixed(1)}`).join(" "),
      class: "wx-gust",
    }));
    rows.forEach((row, i) => {
      chart.appendChild(svg("circle", {
        cx: X(row.point.km).toFixed(1), cy: Y(gusts[i]).toFixed(1), r: 3.5,
        fill: COLORS[LEVELS[row.a.level]], stroke: "#12161c", "stroke-width": 1,
        "vector-effect": "non-scaling-stroke",
      }));
    });
    $("weatherChartEnd").textContent = `${r(total)} km · limit ${T.gust} km/h`;
  }

  // ---- map markers (one data source, refilled when the selected hour changes)
  let mapLayer = null;
  function clearMap() {
    if (mapLayer && window.gentie) window.gentie.viewer.dataSources.remove(mapLayer, true);
    mapLayer = null;
  }
  function pumpRender(ms = 2000) {
    const scene = window.gentie.viewer.scene, until = performance.now() + ms;
    const p = () => { scene.requestRender(); if (performance.now() < until) setTimeout(p, 120); };
    p();
  }
  function drawMap(rows) {
    const { viewer } = window.gentie;
    if (!mapLayer) {
      mapLayer = new Cesium.CustomDataSource("weather");
      viewer.dataSources.add(mapLayer);
    }
    mapLayer.entities.suspendEvents();
    mapLayer.entities.removeAll();
    for (const row of rows) {
      const flagged = row.a.level > 0;
      mapLayer.entities.add({
        position: Cesium.Cartesian3.fromDegrees(row.point.lon, row.point.lat),
        point: {
          pixelSize: row.a.level === 2 ? 18 : flagged ? 14 : 10,
          color: Cesium.Color.fromCssColorString(COLORS[LEVELS[row.a.level]]),
          outlineColor: Cesium.Color.WHITE, outlineWidth: 2,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
        label: flagged ? {
          text: (row.a.level === 2 ? row.a.alerts : row.a.cautions).join(" · "),
          font: "600 12px system-ui, sans-serif",
          fillColor: Cesium.Color.fromCssColorString(row.a.level === 2 ? "#ffb3b3" : "#ffd98a"),
          outlineColor: Cesium.Color.BLACK, outlineWidth: 3,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE, pixelOffset: new Cesium.Cartesian2(0, -22),
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
          distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 40000),
        } : undefined,
      });
    }
    mapLayer.entities.resumeEvents();
    pumpRender();
  }
  function flyTo(point) {
    const { viewer } = window.gentie;
    viewer.camera.flyToBoundingSphere(
      new Cesium.BoundingSphere(Cesium.Cartesian3.fromDegrees(point.lon, point.lat, 0), 500),
      { offset: new Cesium.HeadingPitchRange(viewer.camera.heading, Cesium.Math.toRadians(-40), 6000), duration: 1.2 }
    );
    pumpRender(1800);
  }

    window.gentieWx = { T, sampleRoute, pointsSel };
    
  // ---- state + rendering
  let data = null;      // { total, items: [{ point, hours }], n }
  let sel = 0;          // 0 = worst case over all hours, k>0 = hour k-1
  let cardEls = [];
  let cardTimer = null;
  let playTimer = null;

  function rowsFor(selection) {
    return data.items.map((it) => ({
      point: it.point,
      a: analyze(selection === 0 ? it.hours : [it.hours[selection - 1]], it.hours),
    }));
  }

  function hourText(selection) {
    if (selection === 0) return "Worst case, next 3 days";
    const t = data.items[0].hours[selection - 1]?.time;
    if (!t) return `Hour ${selection}`;
    return new Date(t).toLocaleString([], { weekday: "short", hour: "numeric" });
  }

  function drawCards(rows) {
    const wasOpen = results.querySelector("details")?.open ?? false;
    results.replaceChildren();
    cardEls = [];
    const nowIdx = sel - 1;
    rows.forEach((row, i) => { cardEls[i] = card(row.point, row.a, nowIdx); });

    // problems first, then cautions (both visible); clear points collapsed
    const flagged = rows.map((row, i) => i).filter((i) => rows[i].a.level > 0)
      .sort((x, y) => rows[y].a.level - rows[x].a.level || rows[x].point.km - rows[y].point.km);
    const okIdx = rows.map((row, i) => i).filter((i) => rows[i].a.level === 0);
    flagged.forEach((i) => results.appendChild(cardEls[i]));
    if (okIdx.length) {
      const d = el("details", "weather-clear");
      d.open = wasOpen;
      d.appendChild(el("summary", "", `✓ ${okIdx.length} clear point${okIdx.length > 1 ? "s" : ""} (tap to expand)`));
      const list = el("div", "weather-list");
      okIdx.forEach((i) => list.appendChild(cardEls[i]));
      d.appendChild(list);
      results.appendChild(d);
    }
  }

  function render() {
    const rows = rowsFor(sel);
    const counts = [0, 1, 2].map((l) => rows.filter((row) => row.a.level === l).length);
    timeLabel.textContent = hourText(sel);
    drawBanner(counts);
    drawStrip(rows);
    drawChart(rows, data.total);
    drawMap(rows);
    // cards are heavier, so wait until the slider settles (immediately the first time)
    clearTimeout(cardTimer);
    if (!results.children.length) drawCards(rows);
    else cardTimer = setTimeout(() => drawCards(rows), 150);
  }

  function pick(i) {
    const c = cardEls[i];
    if (!c) return;
    flyTo(data.items[i].point);
    const d = c.closest("details");
    if (d) d.open = true;
    c.scrollIntoView({ behavior: "smooth", block: "nearest" });
    c.classList.remove("flash"); void c.offsetWidth; c.classList.add("flash");
  }

  function setSel(v) {
    sel = v;
    slider.value = String(v);
    render();
  }

  function stopPlay() {
    clearInterval(playTimer);
    playTimer = null;
    playBtn.textContent = "▶ Play";
  }
  function togglePlay() {
    if (!data) return;
    if (playTimer) return stopPlay();
    playBtn.textContent = "■ Stop";
    playTimer = setInterval(() => setSel(sel >= data.n ? 1 : sel + 1), PLAY_MS);
  }
  slider.addEventListener("input", () => { stopPlay(); setSel(Number(slider.value)); });
  playBtn.addEventListener("click", togglePlay);

  // ---- reset
  function reset() {
    stopPlay();
    clearTimeout(cardTimer);
    data = null;
    sel = 0;
    cardEls = [];
    slider.max = "0";
    slider.value = "0";
    results.replaceChildren();
    strip.replaceChildren();
    chart.replaceChildren();
    summary.querySelector(".wx-banner")?.remove();
    summary.hidden = true;
    statusEl.textContent = "";
    clearMap();
  }
  document.addEventListener("gentie:routechange", reset);

  // ---- main
  button.addEventListener("click", async () => {
    const route = window.gentie && window.gentie.state.route;
    if (!route) { statusEl.textContent = "Compute or load a route first."; return; }

    button.disabled = true;
    try {
      reset();
      statusEl.textContent = "Loading forecast…";
      const choice = pointsSel.value === "auto" ? null : Number(pointsSel.value);
      const { total, points } = sampleRoute(route.segments, choice);
      const res = await fetch(`${API}/weather/route`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          locations: points.map(({ id, lat, lon }) => ({ id, lat, lon })),
          forecast_days: FORECAST_DAYS,
        }),
      });
      if (!res.ok) {
        let detail = res.statusText;
        try { detail = (await res.json()).detail || detail; } catch (_) { /* not json */ }
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      const { locations = [] } = await res.json();
      if (window.gentie.state.route !== route) return; // route changed while loading
      if (!locations.length) { statusEl.textContent = "No weather data returned."; return; }

      // results come back in request order
      const items = locations.map((loc, i) => ({ point: points[i], hours: loc.hourly || [] }));
      data = { total, items, n: Math.min(...items.map((it) => it.hours.length)) };
      slider.max = String(data.n);
      summary.hidden = false;
      setSel(0);
      statusEl.textContent = `${items.length} points · ${FORECAST_DAYS}-day forecast · drag the slider to see a specific hour`;
    } catch (err) {
      console.error("Weather request failed:", err);
      statusEl.textContent = `Unable to load weather: ${err.message}`;
    } finally {
      button.disabled = false;
    }
  });
})();