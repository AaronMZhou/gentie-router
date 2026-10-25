
from backend.weather.weather_service import (
    fetch_weather,
    HOURLY_VARIABLES,
)


def main():
    locations = [
        {
            "id": "sample-a",
            "lat": 32.95,
            "lon": -116.80,
        },
        {
            "id": "sample-b",
            "lat": 33.05,
            "lon": -116.65,
        },
    ]

    print("Requesting weather forecasts...")

    forecasts = fetch_weather(
        locations=locations,
        forecast_days=3,
    )

    print(f"\nReceived {len(forecasts)} location forecasts.")

    for forecast in forecasts:
        print("\n" + "=" * 40)
        print(f"Location: {forecast['id']}")
        print(
            f"Coordinates: {forecast['latitude']}, "
            f"{forecast['longitude']}"
        )
        
        hourly = forecast["hourly"]

        print(f"Hourly records: {len(hourly)}")

        if not hourly:
            print("No hourly data returned.")
            continue

        # Print the first three forecast hours.
        for hour in hourly[:3]:
            print(f"\nTime: {hour['time']}")

            for variable in HOURLY_VARIABLES:
                print(f"  {variable}: {hour[variable]}")

    # Basic assertions to catch unexpected response shapes.
    assert len(forecasts) == len(locations)

    for forecast in forecasts:
        assert forecast["hourly"]
        assert "temperature_2m" in forecast["hourly"][0]
        assert "wind_speed_10m" in forecast["hourly"][0]
        assert "precipitation_probability" in forecast["hourly"][0]

    print("\nPASS: Weather service returned the expected data.")


if __name__ == "__main__":
    main()
