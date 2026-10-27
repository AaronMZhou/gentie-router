"""
Store benchmark: the same saved routes in MongoDB (2dsphere) and in SQLite (JSON blobs + shapely),
timing the spatial queries and the project listing.

    python -m loadtest.bench_store                # 5000 generated routes (generation cached in data/cache/)
    python -m loadtest.bench_store --routes 1000

Needs MongoDB at MONGO_URL (default mongodb://localhost:27017); uses and drops the database gentie_bench.
The routes are generated, not real projects: random sites in the study region, each routed by the real
router to one of the three substations with a random preset, five routes per project.

Writes loadtest/store_results.json and loadtest/STORE_RESULTS.md.
"""
import argparse
import gzip
import json
import os
import platform
import random
import statistics
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pymongo

import config as C
from backend import spatial
from backend.mongo_store import MongoStore
from backend.store import Store

HERE = Path(__file__).resolve().parent
DB = "gentie_bench"
ROUTES_PER_PROJECT = 5
RADIUS_KM = 2.0       # around a substation: unselective, a third of the routes end there
SPOT, SPOT_KM = (-116.78, 33.08), 0.5  # a spot in the Ramona backcountry: selective
MAX_REPS, MAX_SECONDS = 50, 20


def generate(n):
    """n routes as (start, end, preset, FeatureCollection); cached, since routing them is the slow part."""
    path = C.DATA_DIR / "cache" / f"bench_routes_{n}.jsonl.gz"
    if path.exists():
        with gzip.open(path, "rt") as f:
            return [json.loads(line) for line in f]
    from backend.cost_model import build_cost_grids
    from backend.router import Router, RouteError
    router, rng = Router(build_cost_grids()), random.Random(42)
    subs = [s["geometry"]["coordinates"] for s in spatial.substations()]
    out, t = [], time.perf_counter()
    while len(out) < n:
        start = [rng.uniform(C.BBOX[0], C.BBOX[2]), rng.uniform(C.BBOX[1], C.BBOX[3])]
        end, preset = rng.choice(subs), rng.choice(list(C.PRESETS))
        try:
            out.append([start, end, preset, router.route(start, end, preset=preset)])
        except RouteError:
            continue  # start outside the routable region
        if len(out) % 500 == 0:
            print(f"  routed {len(out)}/{n} ({time.perf_counter() - t:.0f}s)")
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as f:
        f.writelines(json.dumps(r, separators=(",", ":")) + "\n" for r in out)
    return out


def load(store, routes):
    t = time.perf_counter()
    pid = None
    for i, (start, end, preset, fc) in enumerate(routes):
        if i % ROUTES_PER_PROJECT == 0:
            pid = store.create_project(f"Bench project {i // ROUTES_PER_PROJECT + 1}", 138, "drafting")["id"]
            store.update_project(pid, start_lonlat=start, end_lonlat=end)
        store.add_route(pid, C.PRESETS[preset]["label"], preset, fc)
    return time.perf_counter() - t


def timeit(fn):
    """Up to MAX_REPS calls or MAX_SECONDS, whichever first (after one warm-up). -> stats in ms, last result."""
    result = fn()
    times, t0 = [], time.perf_counter()
    while len(times) < MAX_REPS and (time.perf_counter() - t0 < MAX_SECONDS or len(times) < 3):
        t = time.perf_counter()
        result = fn()
        times.append((time.perf_counter() - t) * 1000)
    times.sort()
    return {"p50_ms": round(statistics.median(times), 2), "p95_ms": round(times[max(0, round(0.95 * len(times)) - 1)], 2),
            "reps": len(times)}, result


def explain(coll, query):
    stats = coll.find(query).explain()["executionStats"]
    stage = stats["executionStages"]
    while "inputStage" in stage and stage["stage"] not in ("IXSCAN", "COLLSCAN"):
        stage = stage["inputStage"]
    return {"plan": stage["stage"], "docs_examined": stats["totalDocsExamined"], "keys_examined": stats["totalKeysExamined"],
            "returned": stats["nReturned"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--routes", type=int, default=5000)
    args = ap.parse_args()
    url = os.getenv("MONGO_URL", "mongodb://localhost:27017")

    print(f"generating {args.routes} routes (cached after the first run)...")
    routes = generate(args.routes)
    n_projects = -(-len(routes) // ROUTES_PER_PROJECT)

    client = pymongo.MongoClient(url)
    client.drop_database(DB)
    mongo = MongoStore(url, DB, client=client)
    sqlite_path = Path(tempfile.mkdtemp()) / "bench.db"
    sqlite = Store(sqlite_path)
    load_s = {"mongodb": load(mongo, routes), "sqlite": load(sqlite, routes)}
    print(f"loaded {len(routes)} routes / {n_projects} projects: mongodb {load_s['mongodb']:.1f}s, sqlite {load_s['sqlite']:.1f}s")

    # the same documents without the 2dsphere index ($out copies documents, not indexes)
    mongo.db.routes.aggregate([{"$out": "routes_noidx"}])
    indexed, unindexed = mongo.db.routes, mongo.db.routes_noidx
    sub = next(s for s in spatial.substations() if s["name"] == "Santa Ysabel Substation")
    lon, lat = sub["geometry"]["coordinates"]
    land_name, land = next((n, g) for n, g in spatial.tribal_lands() if n == "Santa Ysabel Reservation")
    q_near = {"geometry": {"$geoIntersects": {"$geometry": spatial.circle(lon, lat, RADIUS_KM)}}}
    q_spot = {"geometry": {"$geoIntersects": {"$geometry": spatial.circle(*SPOT, SPOT_KM)}}}
    q_land = {"geometry": {"$geoIntersects": {"$geometry": land}}}

    rows = []

    def bench(group, label, fn, plan=None):
        stats, result = timeit(fn)
        rows.append({"query": group, "method": label, **stats, "results": len(result), **(plan or {})})
        print(f"  {group:34s} {label:44s} p50 {stats['p50_ms']:9.2f} ms  p95 {stats['p95_ms']:9.2f} ms  "
              f"({len(result)} results{', ' + plan['plan'] + ' ' + str(plan['docs_examined']) + ' docs' if plan else ''})")
        return result

    print("\nqueries:")
    g = f"routes within {RADIUS_KM:g} km of {sub['name']}"
    a = bench(g, "MongoDB $geoIntersects, 2dsphere index", lambda: list(indexed.find(q_near, {"_id": 1})), explain(indexed, q_near))
    b = bench(g, "MongoDB $geoIntersects, no index", lambda: list(unindexed.find(q_near, {"_id": 1})), explain(unindexed, q_near))
    c = bench(g, "MongoDB $geoNear endpoint (sorted + joined)", lambda: mongo.routes_near(lon, lat, RADIUS_KM, limit=100000))
    d = bench(g, "SQLite + shapely brute force endpoint", lambda: sqlite.routes_near(lon, lat, RADIUS_KM, limit=100000))
    g = f"routes within {SPOT_KM:g} km of a spot ({SPOT[1]}, {SPOT[0]})"
    s1 = bench(g, "MongoDB $geoIntersects, 2dsphere index", lambda: list(indexed.find(q_spot, {"_id": 1})), explain(indexed, q_spot))
    s2 = bench(g, "MongoDB $geoIntersects, no index", lambda: list(unindexed.find(q_spot, {"_id": 1})), explain(unindexed, q_spot))
    s3 = bench(g, "MongoDB $geoNear endpoint (sorted + joined)", lambda: mongo.routes_near(*SPOT, SPOT_KM, limit=100000))
    s4 = bench(g, "SQLite + shapely brute force endpoint", lambda: sqlite.routes_near(*SPOT, SPOT_KM, limit=100000))
    g = f"routes crossing {land_name}"
    e = bench(g, "MongoDB $geoIntersects, 2dsphere index", lambda: list(indexed.find(q_land, {"_id": 1})), explain(indexed, q_land))
    f = bench(g, "MongoDB $geoIntersects, no index", lambda: list(unindexed.find(q_land, {"_id": 1})), explain(unindexed, q_land))
    g = "routes crossing any tribal land (6)"
    h = bench(g, "MongoDB endpoint (2dsphere, per land)", mongo.tribal_crossings)
    i = bench(g, "SQLite + shapely brute force endpoint", sqlite.tribal_crossings)
    g = f"list all {n_projects} projects + route summaries"
    bench(g, "MongoDB (summaries stored at write time)", mongo.list_projects)
    bench(g, "SQLite (re-parses every route's JSON)", sqlite.list_projects)

    # agreement: index vs scan must be identical; MongoDB (spherical) vs shapely (planar UTM) may differ at the edges
    ids = lambda xs: {x["_id"] if "_id" in x else x["id"] for x in xs}
    assert ids(a) == ids(b) and ids(e) == ids(f) and ids(s1) == ids(s2), "index and collection scan disagree"
    near_mongo, near_sqlite = ids(c) | ids(s3), ids(d) | ids(s4)
    tribal_mongo = {x["id"]: set(x["tribal_lands"]) for x in h}
    tribal_sqlite = {x["id"]: set(x["tribal_lands"]) for x in i}
    agree = {
        "near_mongo_vs_shapely": f"{len(near_mongo & near_sqlite)} common, {len(near_mongo - near_sqlite)} only MongoDB, "
                                 f"{len(near_sqlite - near_mongo)} only shapely",
        "near_circle_vs_geoNear": f"{len(ids(a) & ids(c)) + len(ids(s1) & ids(s3))} common, "
                                  f"{len(ids(a) ^ ids(c)) + len(ids(s1) ^ ids(s3))} differ",
        "tribal_mongo_vs_shapely": f"{sum(tribal_mongo.get(k) == v for k, v in tribal_sqlite.items())}/{len(tribal_sqlite)} "
                                   f"shapely routes match; {len(set(tribal_mongo) - set(tribal_sqlite))} only MongoDB",
    }
    print("\nagreement:", json.dumps(agree, indent=2))

    size = mongo.db.command("collStats", "routes")
    env = {"routes": len(routes), "projects": n_projects, "mongodb": client.server_info()["version"] + " (Docker)",
           "sqlite_file_mb": round(sqlite_path.stat().st_size / 1e6, 1),
           "mongo_routes_storage_mb": round(size["storageSize"] / 1e6, 1), "mongo_routes_avg_doc_kb": round(size["avgObjSize"] / 1e3, 1),
           "python": platform.python_version(), "cpu": platform.processor(), "os": platform.platform(),
           "date": datetime.now(timezone.utc).isoformat(timespec="seconds"), "load_seconds": {k: round(v, 1) for k, v in load_s.items()}}
    (HERE / "store_results.json").write_text(json.dumps({"env": env, "rows": rows, "agreement": agree}, indent=2))
    write_md(env, rows, agree)
    client.drop_database(DB)
    print(f"\nwrote {HERE / 'STORE_RESULTS.md'}")


def write_md(env, rows, agree):
    lines = ["# Store benchmark: MongoDB (2dsphere) vs SQLite (JSON + shapely)", "",
             f"`python -m loadtest.bench_store --routes {env['routes']}` on {env['date'][:10]}: {env['routes']} saved routes "
             f"in {env['projects']} projects, MongoDB {env['mongodb']}, Python {env['python']}, {env['os']}.", "",
             "The routes are **generated**, not real projects: random sites in the study region, each routed by the real "
             "router to one of the three substations with a random preset. Latency is in-process, store call to "
             f"Python result, after one warm-up; up to {MAX_REPS} repetitions or {MAX_SECONDS} s per row.", "",
             "| Query | Method | p50 ms | p95 ms | Results | Plan | Docs examined |", "|---|---|---:|---:|---:|---|---:|"]
    for r in rows:
        lines.append(f"| {r['query']} | {r['method']} | {r['p50_ms']:,} | {r['p95_ms']:,} | {r['results']} | "
                     f"{r.get('plan', '')} | {r.get('docs_examined', '')} |")
    lines += ["", "Agreement between methods:", ""] + [f"- {k.replace('_', ' ')}: {v}" for k, v in agree.items()]
    lines += ["", f"Storage: MongoDB routes collection {env['mongo_routes_storage_mb']} MB on disk "
                  f"(avg document {env['mongo_routes_avg_doc_kb']} KB, compressed by WiredTiger); SQLite file {env['sqlite_file_mb']} MB.",
              f"Bulk load (one insert per route through the store API): MongoDB {env['load_seconds']['mongodb']} s, "
              f"SQLite {env['load_seconds']['sqlite']} s.", ""]
    (HERE / "STORE_RESULTS.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
