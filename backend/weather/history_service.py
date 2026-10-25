import math
from datetime import datetime, timezone

from backend.weather.weather_service import WEATHER_API_URL, _client, _lock

DAILY_VARIABLES = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
]
RAIN_DAY_MM = 10.0     # a "heavy rain day"
RECENT_DAYS = 30
RECENT_WEIGHT = 0.6    # weight of the last 30 days vs. the whole period


def _clean(v: float):
    return None if math.isnan(v) else round(float(v), 2)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _stat(values, test, horizon):
    """How often `test` was true, and the expected number of such days in the horizon."""
    all_f = [test(v) for v in values if v is not None]
    rec_f = [test(v) for v in values[-RECENT_DAYS:] if v is not None]
    if not all_f:
        return {"days_over_all": 0, "days_over_recent": 0, "rate": None, "expected_days": None}
    r_all = sum(all_f) / len(all_f)
    r_rec = sum(rec_f) / len(rec_f) if rec_f else r_all
    rate = RECENT_WEIGHT * r_rec + (1 - RECENT_WEIGHT) * r_all
    return {
        "days_over_all": sum(all_f),
        "days_over_recent": sum(rec_f),
        "rate": round(rate, 3),
        "expected_days": round(rate * horizon, 2),
    }


def _trend(values):
    recent, before = _mean(values[-RECENT_DAYS:]), _mean(values[:-RECENT_DAYS])
    return {
        "recent": None if recent is None else round(recent, 2),
        "before": None if before is None else round(before, 2),
    }


def fetch_outlook(locations, limits, past_days=92, horizon_days=14):
    params = {
        "latitude": [p["lat"] for p in locations],
        "longitude": [p["lon"] for p in locations],
        "daily": DAILY_VARIABLES,
        "past_days": past_days,
        "forecast_days": 1,
        "timezone": "auto",
        "wind_speed_unit": "kmh",
        "temperature_unit": "celsius",
        "precipitation_unit": "mm",
    }
    with _lock:
        responses = _client.weather_api(WEATHER_API_URL, params=params)
    if len(responses) != len(locations):
        raise RuntimeError("Weather provider returned an unexpected number of locations.")

    dates, points = [], []
    for location, response in zip(locations, responses):
        daily = response.Daily()
        raw = [daily.Variables(i).ValuesAsNumpy().tolist() for i in range(len(DAILY_VARIABLES))]
        tmax, tmin, rain, wind, gust = ([_clean(v) for v in col] for col in raw)

        if not dates:
            start, step = daily.Time() + response.UtcOffsetSeconds(), daily.Interval()
            dates = [
                datetime.fromtimestamp(start + i * step, tz=timezone.utc).date().isoformat()
                for i in range(len(gust))
            ]

        stats = {
            "gust": _stat(gust, lambda v: v >= limits["gust"], horizon_days),
            "wind": _stat(wind, lambda v: v >= limits["wind"], horizon_days),
            "heat": _stat(tmax, lambda v: v >= limits["heat"], horizon_days),
            "cold": _stat(tmin, lambda v: v <= limits["cold"], horizon_days),
            "rain": _stat(rain, lambda v: v >= RAIN_DAY_MM, horizon_days),
        }
        points.append({
            "id": location.get("id"),
            "series": {"gust": gust, "wind": wind, "tmax": tmax, "tmin": tmin, "rain": rain},
            "stats": stats,
            "trend": {"gust": _trend(gust), "tmax": _trend(tmax)},
        })

    return {
        "dates": dates,
        "horizon_days": horizon_days,
        "recent_days": RECENT_DAYS,
        "rain_day_mm": RAIN_DAY_MM,
        "points": points,
    }