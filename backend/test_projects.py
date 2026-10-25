"""
Project store + project/route API checks against a real server process.

    python -m backend.test_projects      # starts uvicorn on :8766 with ROUTE_WORKERS=2 and a temp DB
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

PORT = 8766
BASE = f"http://127.0.0.1:{PORT}"
SITE = [-116.69, 33.10]
CREELMAN = [-116.85294, 33.01815]
SANTA_YSABEL = [-116.67105, 33.11051]


def start_server(db, seed=False):
    env = {**os.environ, "ROUTE_WORKERS": "2", "GENTIE_DB": str(db)}
    env.pop("GENTIE_NO_SEED", None)
    if not seed:
        env["GENTIE_NO_SEED"] = "1"
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app:app", "--port", str(PORT), "--log-level", "warning"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(240):
        try:
            if requests.get(f"{BASE}/health", timeout=1).ok:
                return proc
        except requests.ConnectionError:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("server did not start")


def stop(proc):
    proc.terminate()
    proc.wait(timeout=20)


def api(method, path, body=None):
    r = requests.request(method, BASE + path, json=body, timeout=120)
    return r.status_code, (r.json() if r.content else None)


def main():
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "projects.db"
    proc = start_server(db)
    try:
        code, p = api("POST", "/projects", {"name": "Test Solar", "voltage_kv": 138, "phase": "drafting", "filing_target": "2026-12-01"})
        assert code == 201 and p["routes"] == [] and p["start_lonlat"] is None and p["filing_target"] == "2026-12-01", (code, p)
        pid = p["id"]
        assert any(x["id"] == pid for x in api("GET", "/projects")[1])
        print("1. create -> 201, list contains it")

        code, r = api("POST", f"/projects/{pid}/routes", {"preset": "balanced", "start_lonlat": SITE, "end_lonlat": CREELMAN})
        assert code == 201, (code, r)
        s = r["route_summary"]
        assert s["total_km"] > 0 and 2 <= len(s["thumb"]) <= 40 and s["name"] == "Balanced", s
        assert {"id", "name", "preset", "total_km", "total_cost_usd", "tower_count", "n_triggers", "thumb"} <= s.keys()
        assert {"overhead_km", "underground_km", "n_transitions", "high_fire_km", "overhead_high_fire_km", "protected_km",
                "tribal_km", "permitting_triggers", "row", "inventory"} <= s.keys(), s.keys()
        assert all(t.keys() == {"code", "reason"} for t in s["permitting_triggers"]) and "geometry" not in s
        assert r["result"]["type"] == "FeatureCollection"
        p = api("GET", f"/projects/{pid}")[1]
        assert p["start_lonlat"] == SITE and p["end_lonlat"] == CREELMAN, p
        print(f"2. first route with endpoints -> 201, {s['total_km']} km, {len(s['thumb'])} thumb pts, endpoints saved")

        code, r2 = api("POST", f"/projects/{pid}/routes", {"preset": "avoid_fire"})
        assert code == 201 and r2["route_summary"]["name"] == "Avoid fire", (code, r2)
        assert len(api("GET", f"/projects/{pid}")[1]["routes"]) == 2
        print("3. second preset (project endpoints) -> project has 2 routes")

        rid = s["id"]
        code, full = api("GET", f"/routes/{rid}")
        assert code == 200 and full["result"]["type"] == "FeatureCollection" and full["project_id"] == pid and full["preset"] == "balanced"
        print("4. GET /routes/{id} -> FeatureCollection")

        code, rr = api("PATCH", f"/routes/{rid}", {"name": "Main option"})
        assert code == 200 and rr["name"] == "Main option" and "result" not in rr, rr
        code, p = api("PATCH", f"/projects/{pid}", {"name": "Renamed Solar", "phase": "comparing"})
        assert code == 200 and p["name"] == "Renamed Solar" and p["phase"] == "comparing", p
        assert p["routes"][0]["name"] == "Main option"
        print("5. PATCH route name / project name+phase -> reflected")

        km_before = {x["id"]: x["total_km"] for x in p["routes"]}
        code, p = api("PATCH", f"/projects/{pid}", {"end_lonlat": SANTA_YSABEL})
        assert code == 200 and p["end_lonlat"] == SANTA_YSABEL, (code, p)
        km_after = {x["id"]: x["total_km"] for x in p["routes"]}
        assert km_after.keys() == km_before.keys() and all(km_after[k] != km_before[k] for k in km_before), (km_before, km_after)
        assert api("GET", f"/routes/{r2['route_summary']['id']}")[1]["result"]["properties"]["preset"] == "avoid_fire"
        print(f"6. PATCH end_lonlat -> every route recomputed with its preset {km_before} -> {km_after}")

        # bad_endpoints: 400 with the router's detail, nothing stored (neither route nor endpoints)
        code, err = api("POST", f"/projects/{pid}/routes", {"preset": "cheapest", "start_lonlat": [-118, 33]})
        assert code == 400 and "outside" in err["detail"], (code, err)
        p = api("GET", f"/projects/{pid}")[1]
        assert len(p["routes"]) == 2 and p["start_lonlat"] == SITE, p
        code, err = api("PATCH", f"/projects/{pid}", {"start_lonlat": [-118, 33]})
        assert code == 400 and api("GET", f"/projects/{pid}")[1]["start_lonlat"] == SITE
        code, bare = api("POST", "/projects", {"name": "Bare", "voltage_kv": 69, "phase": "drafting"})
        assert api("POST", f"/projects/{bare['id']}/routes", {"preset": "balanced"})[0] == 400
        print("7. bad_endpoints -> 400, route count and endpoints unchanged; no endpoints -> 400")

        assert api("POST", "/projects", {"name": "x", "voltage_kv": 69, "phase": "bogus"})[0] == 422
        assert api("PATCH", f"/projects/{pid}", {"phase": "bogus"})[0] == 422
        assert api("POST", f"/projects/{pid}/routes", {"preset": "nope"})[0] == 422
        assert api("GET", "/projects/999999")[0] == 404
        assert api("GET", "/routes/999999")[0] == 404
        assert api("PATCH", "/projects/999999", {"name": "x"})[0] == 404
        assert api("POST", "/projects/999999/routes", {"preset": "balanced"})[0] == 404
        assert api("DELETE", "/routes/999999")[0] == 404
        print("8. phase=bogus / preset=nope -> 422; unknown ids -> 404")

        rids = [x["id"] for x in p["routes"]]
        assert api("DELETE", f"/projects/{bare['id']}")[0] == 204
        assert api("DELETE", f"/projects/{pid}")[0] == 204
        assert api("GET", f"/projects/{pid}")[0] == 404
        assert all(api("GET", f"/routes/{x}")[0] == 404 for x in rids)
        print("9. DELETE project -> 204, its routes 404")

        code, keep = api("POST", "/projects", {"name": "Persist", "voltage_kv": 230, "phase": "ready"})
        api("POST", f"/projects/{keep['id']}/routes", {"preset": "balanced", "start_lonlat": SITE, "end_lonlat": CREELMAN})
        stop(proc)
        proc = start_server(db)
        p = api("GET", f"/projects/{keep['id']}")[1]
        assert p["name"] == "Persist" and len(p["routes"]) == 1, p
        print("10. restart on the same DB -> data persists")
        stop(proc)

        t = time.perf_counter()
        proc = start_server(tmp / "seeded.db", seed=True)
        boot = time.perf_counter() - t
        projects = api("GET", "/projects")[1]
        assert len(projects) == 4 and sum(len(x["routes"]) for x in projects) == 11, projects
        assert all(x["start_lonlat"] and x["end_lonlat"] for x in projects)
        for x in projects:
            print(f"    {x['name']} ({x['voltage_kv']} kV, {x['phase']}, filing {x['filing_target']}): " +
                  ", ".join(f"{r['preset']} {r['total_km']} km ${r['total_cost_usd'] / 1e6:.1f}M" for r in x["routes"]))
        print(f"11. fresh DB with seeding -> 4 projects, 11 routes (server ready in {boot:.1f}s)")
        print("\nALL PROJECT CHECKS PASSED")
    finally:
        proc.kill()


if __name__ == "__main__":
    main()
