"""
Weather-informed maintenance checks.   python -m backend.test_climate
Offline logic first; the last step calls the live Open-Meteo archive (skipped with a note if unreachable).
"""
import json

import config as C
from backend import climate, inventory


def main():
    # hazard days per year from a fake 2-year daily block
    n = 730
    daily = {"time": list(range(n)), "wind_gusts_10m_max": [80] * 20 + [10] * (n - 20), "wind_speed_10m_max": [0] * n,
             "temperature_2m_max": [36] * 4 + [20] * (n - 4), "temperature_2m_min": [-11] + [5] * (n - 1), "precipitation_sum": [None] * n}
    rates = climate.rates_from_daily(daily)
    assert rates["gust"] == round(20 / (730 / 365.25), 2) and rates["wind"] == 0 and rates["rain"] == 0, rates
    assert rates["heat"] > 0 and rates["cold"] > 0
    print("1. rates_from_daily OK:", rates)

    calm = climate.multiplier({h: 0 for h in C.WEATHER_MAINT})
    assert calm["multiplier"] == 1.0
    wild = climate.multiplier({h: 1000 for h in C.WEATHER_MAINT})
    assert wild["multiplier"] == C.WEATHER_MAINT_CAP
    mid = climate.multiplier({"gust": 5, "wind": 0, "heat": 0, "cold": 0, "rain": 0})
    assert 1.0 < mid["multiplier"] < 1.2 and mid["drivers"][0]["hazard"] == "gust", mid
    print("2. multiplier OK: calm 1.0, mid", mid["multiplier"], ", capped", wild["multiplier"])

    demo = json.loads(C.DEMO_ROUTE_PATH.read_text())
    pts = climate.sample_points(demo)
    assert 2 <= len(pts) <= C.CLIMATE_MAX_POINTS
    print(f"3. sample_points OK: {len(pts)} points")

    # the supply market only moves the route cost by its difference from the current market
    cur = inventory.assess(demo, market="current")["summary"]
    crisis = inventory.assess(demo, market="crisis")["summary"]
    assert cur["market_cost_delta_usd"] == 0, cur["market_cost_delta_usd"]
    assert crisis["market_cost_delta_usd"] > 0
    print(f"4. market delta OK: current $0, crisis +${crisis['market_cost_delta_usd']:,}")

    wm = climate.weather_maint(demo)
    if wm is None:
        print("5. live archive unreachable: skipped")
    else:
        assert 1.0 <= wm["multiplier"] <= C.WEATHER_MAINT_CAP and wm["points"] == len(pts)
        print(f"5. live archive OK: x{wm['multiplier']} {wm['years']}; drivers", [(d["hazard"], d["days_per_year"]) for d in wm["drivers"][:3]])
    print("\nALL CLIMATE CHECKS PASSED")


if __name__ == "__main__":
    main()
