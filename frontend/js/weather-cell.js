// Route weather + historical outlook. The two classic scripts in weather/ read window.gentie ({viewer, state.route.segments}).
export const weatherCell = () => `
  <div class="cell sect" id="weather" style="grid-column:span 12"><div class="mono-label">Weather · Open-Meteo</div>
    <h6>Route <span class="it">weather</span></h6>
    <label class="wx-ctl">Points along route
      <select id="wxPoints">
        <option value="auto" selected>Auto (every ~10 km)</option>
        <option value="5">5 points</option><option value="10">10 points</option><option value="20">20 points</option>
        <option value="30">30 points</option><option value="50">50 points</option>
      </select>
    </label>
    <div class="fx-actions"><button id="btnWeather" class="btn btn--sm" type="button">Check forecast along route</button></div>
    <p id="weatherStatus" class="muted small"></p>
    <div id="weatherSummary" hidden>
      <div class="wx-time">
        <div class="wx-timehead"><b id="wxTimeLabel">Worst case, next 3 days</b><button id="wxPlay" class="btn btn--sm" type="button">▶ Play</button></div>
        <input id="wxTime" type="range" min="0" max="0" value="0" step="1" aria-label="Forecast hour">
        <div class="profile-axis muted"><span>Worst case</span><span>+3 days</span></div>
      </div>
      <div id="weatherStrip" class="wx-strip"></div>
      <div class="profile-axis muted"><span>Project site</span><span>Substation</span></div>
      <div class="mono-label" style="margin-top:12px">Gusts along route</div>
      <svg id="weatherChart" class="wx-chart" viewBox="0 0 300 80" preserveAspectRatio="none"></svg>
      <div class="profile-axis muted"><span>0 km</span><span id="weatherChartEnd"></span></div>
    </div>
    <div id="weatherResults"></div>
    <div class="mono-label" style="margin-top:24px">Historical outlook (last 3 months)</div>
    <div class="fx-actions"><button id="btnOutlook" class="btn btn--sm" type="button">Analyze past 3 months → 14-day outlook</button></div>
    <p id="outlookStatus" class="muted small"></p>
    <div id="outlookBody" hidden>
      <div id="outlookTiles" class="wx-tiles"></div>
      <div class="mono-label" style="margin-top:12px">Worst daily gust along route, last 92 days</div>
      <svg id="outlookChart" class="wx-chart" viewBox="0 0 300 80" preserveAspectRatio="none"></svg>
      <div class="profile-axis muted"><span>92 days ago</span><span>today</span></div>
      <div class="mono-label" style="margin-top:12px">Outlook by point</div>
      <div id="outlookStrip" class="wx-strip"></div>
      <div class="profile-axis muted"><span>Project site</span><span>Substation</span></div>
      <p id="outlookNote" class="muted small"></p>
    </div>
  </div>`;

const loadScript = (src) => new Promise((ok, fail) => {
  const s = document.createElement("script");
  s.src = src; s.onload = ok; s.onerror = () => fail(new Error(`couldn't load ${src}`));
  document.head.append(s);
});

// Call once the weatherCell markup is in the page and the map exists.
export async function initWeather(map, fc) {
  const segments = fc.features.filter((f) => f.properties.kind === "segment");
  window.gentie = { viewer: map.viewer, state: { route: { segments } } };
  await loadScript("weather/weather.js");
  await loadScript("weather/outlook.js");
}
