
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.weather.weather_service import fetch_weather


router = APIRouter(prefix="/weather", tags=["weather"])


class WeatherLocation(BaseModel):
    id: str | None = None
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RouteWeatherRequest(BaseModel):
    locations: list[WeatherLocation] = Field(
        min_length=1,
        max_length=30,
    )
    forecast_days: int = Field(default=3, ge=1, le=16)


@router.post("/route")
def get_route_weather(request: RouteWeatherRequest):
    locations = [
        location.model_dump()
        for location in request.locations
    ]

    try:
        forecasts = fetch_weather(
            locations=locations,
            forecast_days=request.forecast_days,
        )
        return {"locations": forecasts}

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        # Log the full exception on the server in production.
        raise HTTPException(
            status_code=502,
            detail="Unable to retrieve weather forecasts.",
        ) from exc
