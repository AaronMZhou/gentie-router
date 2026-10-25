import math
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import openmeteo_requests
import requests_cache
from retry_requests import retry

WEATHER_API_URL = "https://api.open-meteo.com/v1/forecast"

HOURLY_VARIABLES = [
    "temperature_2m",
    "precipitation_probability",
    "precipitation",
    "wind_speed_10m",
    "wind_gusts_10m",
]

_session = retry(
    requests_cache.CachedSession(
        str(Path(__file__).resolve().parent / ".cache"), expire_after=3600
    ),
    retries=5,
    backoff_factor=0.2,
)
_client = openmeteo_requests.Client(session=_session)
_lock = Lock()  # the shared session isn't guaranteed thread-safe


def _clean(value: float):
    """Open-Meteo uses NaN for missing values; NaN is not valid JSON."""
    return None if math.isnan(value) else round(value, 2)


def fetch_weather(locations: list[dict], forecast_days: int = 3) -> list[dict]:
    """
    locations: [{"id": "km 10.0", "lat": 32.95, "lon": -116.80}, ...]
    Returns one {"id", "latitude", "longitude", "hourly": [...]} per location, in order.
    """
    if not locations:
        raise ValueError("At least one location is required.")
    if not 1 <= forecast_days <= 16:
        raise ValueError("forecast_days must be between 1 and 16.")

    params = {
        "latitude": [p["lat"] for p in locations],
        "longitude": [p["lon"] for p in locations],
        "hourly": HOURLY_VARIABLES,
        "forecast_days": forecast_days,
        "timezone": "auto",
        "wind_speed_unit": "kmh",
        "temperature_unit": "celsius",
        "precipitation_unit": "mm",
    }
    with _lock:
        responses = _client.weather_api(WEATHER_API_URL, params=params)

    if len(responses) != len(locations):
        raise RuntimeError("Weather provider returned an unexpected number of locations.")

    results = []
    for location, response in zip(locations, responses):
        hourly = response.Hourly()
        # variable order matches HOURLY_VARIABLES
        series = {
            name: hourly.Variables(i).ValuesAsNumpy().tolist()
            for i, name in enumerate(HOURLY_VARIABLES)
        }
        start, step = hourly.Time(), hourly.Interval()
        count = len(series[HOURLY_VARIABLES[0]])

        results.append({
            "id": location.get("id"),
            "latitude": response.Latitude(),
            "longitude": response.Longitude(),
            "hourly": [
                {
                    "time": datetime.fromtimestamp(start + i * step, tz=timezone.utc).isoformat(),
                    **{name: _clean(values[i]) for name, values in series.items()},
                }
                for i in range(count)
            ],
        })
    return results