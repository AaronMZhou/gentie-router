"""
Locust load test for POST /route.

    locust -f loadtest/locustfile.py --host http://127.0.0.1:8000            # web UI on :8089
    locust -f loadtest/locustfile.py --host http://127.0.0.1:8000 --headless -u 32 -r 32 -t 45s

Traffic mix (env vars):
    LOAD_REPEAT_FRACTION  share of requests drawn from 20 "popular" project/substation pairs
                          (cacheable); the rest use a fresh random project site (cache miss).
                          Default 0 = every request is a unique route.
    LOAD_ARBITRARY_END    1 = unique routes end at a random point instead of a substation
                          (no precomputed substation tree -> windowed search; worst case).
"""
import os
import random

from locust import FastHttpUser, constant, task

BBOX = (-116.85, 32.97, -116.60, 33.18)  # a little inside the study region
SUBSTATIONS = [[-116.85305, 33.01807], [-116.58624, 33.00602]]
REPEAT_FRACTION = float(os.getenv("LOAD_REPEAT_FRACTION", "0"))
ARBITRARY_END = os.getenv("LOAD_ARBITRARY_END", "0") == "1"

_rng = random.Random(42)
POPULAR = [([_rng.uniform(BBOX[0], BBOX[2]), _rng.uniform(BBOX[1], BBOX[3])], _rng.choice(SUBSTATIONS)) for _ in range(20)]


class Planner(FastHttpUser):
    wait_time = constant(0)  # closed loop: each simulated user sends its next request as soon as one returns

    @task
    def route(self):
        if random.random() < REPEAT_FRACTION:
            start, end = random.choice(POPULAR)
            name = "/route [popular]"
        else:
            start = [random.uniform(BBOX[0], BBOX[2]), random.uniform(BBOX[1], BBOX[3])]
            if ARBITRARY_END:
                end = [random.uniform(BBOX[0], BBOX[2]), random.uniform(BBOX[1], BBOX[3])]
                name = "/route [unique, arbitrary end]"
            else:
                end = random.choice(SUBSTATIONS)
                name = "/route [unique]"
        with self.client.post("/route", json={"start": start, "end": end}, name=name, catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f"HTTP {r.status_code}")
