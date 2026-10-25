"""
Weather-informed maintenance.

    weather_maint(route_fc) -> {"multiplier", "drivers", ...} or None when the archive is unreachable

Pipeline: a few points along the route -> ~5 years of daily Open-Meteo archive data (ERA5 reanalysis) ->
days per year over each hazard limit -> an uplift per hazard (config.WEATHER_MAINT) -> one multiplier on the
weather-exposed upkeep (overhead lines, structures, vegetation; the frontend leaves underground cable alone).
Per-location results are cached in config.CLIMATE_CACHE, so a repeat route costs no network call.
"""
import json
import math
import threading
import time
from datetime import date

import requests

import config as C
from backend.inventory import haversine_km

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DAILY = ["temperature_2m_max", "temperature_2m_min", "precipitation_sum", "wind_speed_10m_max", "wind_gusts_10m_max"]
APPLIES_TO = ["overhead_lines", "structures", "fire_vegetation"]
_lock = threading.Lock()


def rates_from_daily(daily):
    """Open-Meteo `daily` block -> hazard days per year."""
    lim = C.WEATHER_LIMITS
    tests = {"gust": ("wind_gusts_10m_max", lambda v: v >= lim["gust"]), "wind": ("wind_speed_10m_max", lambda v: v >= lim["wind"]),
             "heat": ("temperature_2m_max", lambda v: v >= lim["heat"]), "cold": ("temperature_2m_min", lambda v: v <= lim["cold"]),
             "rain": ("precipitation_sum", lambda v: v >= lim["rain_mm"])}
    years = max(1.0, len(daily["time"]) / 365.25)
    return {k: round(sum(1 for v in daily[col] if v is not None and test(v)) / years, 2) for k, (col, test) in tests.items()}


def multiplier(rates):
    """Hazard days/yr -> {multiplier, drivers}; each hazard's uplift grows linearly to its cap at the reference days."""
    drivers = []
    for hazard, (ref_days, max_uplift) in C.WEATHER_MAINT.items():
        uplift = max_uplift * min(1.0, rates[hazard] / ref_days)
        drivers.append({"hazard": hazard, "days_per_year": rates[hazard], "uplift": round(uplift, 3)})
    drivers.sort(key=lambda d: -d["uplift"])
    return {"multiplier": round(min(C.WEATHER_MAINT_CAP, 1 + sum(d["uplift"] for d in drivers)), 3), "drivers": drivers}


def sample_points(route_fc):
    """Evenly spaced [lon, lat] points along the route's segments."""
    coords = []
    for f in route_fc["features"]:
        if f["properties"].get("kind") == "segment":
            for c in f["geometry"]["coordinates"]:
                if not coords or coords[-1][:2] != c[:2]:
                    coords.append(c[:2])
    cum = [0.0]
    for a, b in zip(coords, coords[1:]):
        cum.append(cum[-1] + haversine_km(a[1], a[0], b[1], b[0]))
    n = min(C.CLIMATE_MAX_POINTS, max(2, math.ceil(cum[-1] / C.CLIMATE_SAMPLE_KM) + 1))
    return [coords[min(range(len(cum)), key=lambda i: abs(cum[i] - cum[-1] * k / (n - 1)))] for k in range(n)]


def _load_cache():
    try:
        return json.loads(C.CLIMATE_CACHE.read_text())
    except (OSError, ValueError):
        return {}


def _fetch(points, first_year, last_year):
    """One archive request for all points -> list of `daily` blocks."""
    params = {
        "latitude": ",".join(str(p[1]) for p in points), "longitude": ",".join(str(p[0]) for p in points),
        "start_date": f"{first_year}-01-01", "end_date": f"{last_year}-12-31", "daily": ",".join(DAILY),
        "wind_speed_unit": "kmh", "temperature_unit": "celsius", "precipitation_unit": "mm", "timezone": "auto"}
    for wait in (15, 30, 60, None):      # the free tier rate-limits bursts (429): back off and retry
        r = requests.get(ARCHIVE_URL, timeout=60, params=params)
        if r.status_code != 429 or wait is None:
            break
        time.sleep(wait)
    r.raise_for_status()
    body = r.json()
    return [b["daily"] for b in (body if isinstance(body, list) else [body])]


def weather_maint(route_fc):
    try:
        last = date.today().year - 1
        first = last - C.CLIMATE_YEARS + 1
        pts = sample_points(route_fc)
        keys = [f"{p[1]:.2f},{p[0]:.2f},{first}-{last}" for p in pts]
        with _lock:
            cache = _load_cache()
            missing = [i for i, k in enumerate(keys) if k not in cache]
            if missing:
                for i, daily in zip(missing, _fetch([pts[i] for i in missing], first, last)):
                    cache[keys[i]] = rates_from_daily(daily)
                C.CLIMATE_CACHE.parent.mkdir(parents=True, exist_ok=True)
                C.CLIMATE_CACHE.write_text(json.dumps(cache))
        rates = {h: round(sum(cache[k][h] for k in keys) / len(keys), 2) for h in C.WEATHER_MAINT}
        return {**multiplier(rates), "years": f"{first}-{last}", "points": len(pts), "applies_to": APPLIES_TO,
                "source": "Open-Meteo archive (ERA5 reanalysis)"}
    except Exception as e:  # weather is an enhancement: a failure leaves the flat maintenance estimate
        print(f"[gentie] weather-informed maintenance unavailable: {e}")
        return None
