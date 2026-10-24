"""
Sample projects for an empty database (skipped when GENTIE_NO_SEED=1).

Starts are sites inside config.BBOX; ends are real substations from overlays/substations.geojson.
"""
import asyncio
import json
import os
from datetime import date, timedelta

import config as C

CREELMAN = [-116.85294, 33.01815]      # Creelman Substation, SDG&E 69/138 kV
SANTA_YSABEL = [-116.67105, 33.11051]  # Santa Ysabel Substation, SDG&E 69 kV
BOULDER_CREEK = [-116.58621, 33.00599]  # Boulder Creek Substation, SDG&E 69 kV

# name, kV, phase, filing target (days from today), start, end, presets
PROJECTS = [
    ("Ramona Solar Gen-tie", 230, "comparing", 49, [-116.79, 33.13], CREELMAN, ["balanced", "avoid_fire", "cheapest"]),
    ("Santa Ysabel Reinforcement", 138, "permit-risk", 102, [-116.62, 33.17], SANTA_YSABEL, ["balanced", "underground"]),
    ("Julian Wind Interconnect", 230, "drafting", None, [-116.60, 33.08], SANTA_YSABEL, ["balanced", "avoid_fire", "underground"]),
    ("Boulder Creek BESS Tie", 69, "ready", 27, [-116.65, 33.04], BOULDER_CREEK, ["balanced", "cheapest", "underground"]),
]


async def seed_if_empty(store, compute):
    """compute(start, end, preset) -> (status, json_bytes, cache_state): the app's cached_route."""
    if os.getenv("GENTIE_NO_SEED") == "1" or store.list_projects():
        return
    jobs = [(p, preset) for p in PROJECTS for preset in p[6]]
    results = await asyncio.gather(*[compute(p[4], p[5], preset) for p, preset in jobs])  # all at once: the pool parallelises
    ids = {}
    for (p, preset), (status, body, _) in zip(jobs, results):
        name, kv, phase, days, start, end, _ = p
        if name not in ids:
            target = (date.today() + timedelta(days=days)).isoformat() if days is not None else None
            ids[name] = store.create_project(name, kv, phase, target)["id"]
            store.update_project(ids[name], start_lonlat=start, end_lonlat=end)
        if status != 200:
            print(f"[gentie] seed: {name} / {preset} failed: {body.decode()}")
            continue
        store.add_route(ids[name], C.PRESETS[preset]["label"], preset, json.loads(body))
    print(f"[gentie] seeded {len(ids)} sample projects")
