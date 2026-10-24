"""
Concurrency / caching checks against a real server process.

    python -m backend.test_api            # starts uvicorn on :8765 with ROUTE_WORKERS=4

1. many distinct concurrent requests all succeed and match the in-process Router
2. identical concurrent requests are computed once (single-flight), repeats hit the cache
3. errors (400) are returned correctly and never cached
4. the event loop stays responsive (/health) while every worker is busy
"""
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests

from backend.router import Router

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
CREELMAN = [-116.85305, 33.01807]


def start_server(workers=4):
    # keep the real project store (data/projects.db) out of this test
    env = {**os.environ, "ROUTE_WORKERS": str(workers), "ROUTE_CACHE_SIZE": "256", "GENTIE_NO_SEED": "1",
           "GENTIE_DB": os.path.join(tempfile.mkdtemp(), "projects.db")}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app:app", "--port", str(PORT), "--log-level", "warning"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        try:
            if requests.get(f"{BASE}/health", timeout=1).ok:
                return proc
        except requests.ConnectionError:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("server did not start")


def post(start, end):
    r = requests.post(f"{BASE}/route", json={"start": start, "end": end}, timeout=60)
    return r.status_code, r.headers.get("X-Cache"), r.json()


def main():
    rng = np.random.default_rng(3)
    starts = [[float(rng.uniform(-116.85, -116.60)), float(rng.uniform(32.97, 33.18))] for _ in range(40)]
    proc = start_server()
    try:
        stats0 = requests.get(f"{BASE}/stats").json()
        print(f"server up: {stats0['executor']}, {stats0['workers']} workers {stats0['worker_pids']}")

        # 1. distinct concurrent requests match the in-process router
        t = time.perf_counter()
        with ThreadPoolExecutor(20) as ex:
            results = list(ex.map(lambda s: post(s, CREELMAN), starts))
        wall = time.perf_counter() - t
        assert all(code == 200 for code, _, _ in results), [c for c, _, _ in results]
        direct = Router()
        for s, (_, _, fc) in zip(starts[:8], results[:8]):
            ref = direct.route(s, CREELMAN)["properties"]
            assert abs(fc["properties"]["total_cost_usd"] - ref["total_cost_usd"]) <= 1, "pool result != direct"
            assert fc["properties"]["total_km"] == ref["total_km"]
        print(f"1. {len(starts)} distinct routes, 20 concurrent clients: all 200, match in-process Router ({wall:.1f}s wall)")

        # 2. single-flight + cache
        s = [-116.70, 33.12]
        before = requests.get(f"{BASE}/stats").json()["cache"]
        with ThreadPoolExecutor(20) as ex:
            states = [st for _, st, _ in ex.map(lambda _: post(s, CREELMAN), range(20))]
        after = requests.get(f"{BASE}/stats").json()["cache"]
        assert after["misses"] - before["misses"] == 1, (before, after)
        assert states.count("MISS") == 1 and set(states) <= {"MISS", "COALESCED", "HIT"}, states
        assert post(s, CREELMAN)[1] == "HIT"
        print(f"2. 20 identical concurrent requests -> computed once {dict((k, states.count(k)) for k in set(states))}; repeat = HIT")

        # 3. errors
        code, cache_state, body = post([-118.0, 33.0], CREELMAN)
        assert code == 400 and "outside" in body["detail"], body
        assert post([-118.0, 33.0], CREELMAN)[1] == "MISS", "errors must not be cached"
        assert requests.post(f"{BASE}/route", json={"start": [1]}).status_code == 422
        print("3. out-of-region -> 400 (not cached), malformed -> 422")

        # 3b. presets: default == balanced (and == in-process Router), avoid_fire differs, unknown -> 400
        s = [-116.69, 33.10]
        r = requests.post(f"{BASE}/route", json={"start": s, "end": CREELMAN, "preset": "avoid_fire"}, timeout=60)
        assert r.status_code == 200 and r.json()["properties"]["preset"] == "avoid_fire", r.text[:200]
        fire = r.json()
        code, _, default = post(s, CREELMAN)
        ref = direct.route(s, CREELMAN)
        for fc in (default, ref):
            fc["properties"].pop("compute_ms")
        assert code == 200 and default == ref, "default /route != in-process Router"
        assert default["properties"]["preset"] == "balanced"
        assert fire["features"] != default["features"], "avoid_fire gave the balanced route"
        r = requests.post(f"{BASE}/route", json={"start": s, "end": CREELMAN, "preset": "nope"}, timeout=60)
        assert r.status_code == 400 and "preset" in r.json()["detail"], r.text
        print("3b. preset=avoid_fire -> 200 + differs; omitted == in-process balanced; preset=nope -> 400")

        # 4. event loop stays responsive while all workers are busy
        busy = [[float(rng.uniform(-116.85, -116.60)), float(rng.uniform(32.97, 33.18))] for _ in range(24)]
        with ThreadPoolExecutor(24) as ex:
            futs = [ex.submit(post, b, [-116.58624, 33.00602]) for b in busy]
            time.sleep(0.3)
            lat = []
            for _ in range(10):
                t = time.perf_counter()
                requests.get(f"{BASE}/health", timeout=5)
                lat.append((time.perf_counter() - t) * 1000)
            assert all(f.result()[0] == 200 for f in futs)
        print(f"4. /health while 24 routes queued on 4 workers: max {max(lat):.0f} ms, median {np.median(lat):.0f} ms")
        assert max(lat) < 250, lat
        print("\nALL API CHECKS PASSED")
    finally:
        proc.kill()


if __name__ == "__main__":
    main()
