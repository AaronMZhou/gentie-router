"""
Benchmark matrix: start the server per configuration, drive it with Locust, collect p50/p95/req/s.

    python -m loadtest.run_bench                       # full matrix (~6 min)
    python -m loadtest.run_bench --quick               # 2 configs, 20 s each
    python -m loadtest.run_bench --baseline-dir ../gentie-sync   # also benchmark a checkout of
                                                                  # the old synchronous server

Writes loadtest/results.json and loadtest/RESULTS.md.
"""
import argparse
import csv
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PORT = 8787
HOST = f"http://127.0.0.1:{PORT}"


def start_server(cwd, env_extra):
    env = {**os.environ, **env_extra}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app:app", "--port", str(PORT),
                             "--log-level", "warning"], cwd=cwd, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(240):
        try:
            if requests.get(f"{HOST}/health", timeout=1).ok:
                return proc
        except requests.ConnectionError:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError(f"server did not start ({cwd}, {env_extra})")


def rss_mb(proc):
    p = psutil.Process(proc.pid)
    return sum(q.memory_info().rss for q in [p, *p.children(recursive=True)]) / 2**20


def run_locust(users, seconds, load_env, prefix):
    env = {**os.environ, **load_env}
    subprocess.run([sys.executable, "-m", "locust", "-f", str(HERE / "locustfile.py"), "--headless",
                    "-u", str(users), "-r", str(users), "-t", f"{seconds}s", "--host", HOST,
                    "--csv", str(prefix), "--only-summary", "--loglevel", "WARNING"],
                   env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(f"{prefix}_stats.csv", newline="") as f:
        agg = next(r for r in csv.DictReader(f) if r["Name"] == "Aggregated")
    return {
        "requests": int(agg["Request Count"]), "failures": int(agg["Failure Count"]),
        "rps": round(float(agg["Requests/s"]), 1),
        "p50_ms": int(float(agg["50%"])), "p95_ms": int(float(agg["95%"])), "p99_ms": int(float(agg["99%"])),
    }


def bench(label, cwd, env, users, seconds, load_env, tmp):
    print(f"-> {label}: {users} users, {seconds}s, load {load_env} ...", flush=True)
    proc = start_server(cwd, env)
    try:
        requests.post(f"{HOST}/route", json={"start": [-116.7, 33.1], "end": [-116.85305, 33.01807]}, timeout=60)
        res = run_locust(users, seconds, load_env, tmp / "".join(ch if ch.isalnum() else "_" for ch in label))
        try:
            res["cache"] = requests.get(f"{HOST}/stats", timeout=5).json()["cache"]
        except Exception:
            res["cache"] = None  # the old server has no /stats
        res["server_rss_mb"] = round(rss_mb(proc))
    finally:
        for q in psutil.Process(proc.pid).children(recursive=True):
            q.kill()
        proc.kill()
        time.sleep(1)
    res.update(label=label, users=users, seconds=seconds, load=load_env, server_env=env)
    print(f"   {res['rps']} req/s, p50 {res['p50_ms']} ms, p95 {res['p95_ms']} ms, "
          f"{res['failures']} failures, server RSS {res['server_rss_mb']} MB", flush=True)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--baseline-dir", help="checkout of the pre-async server to benchmark as 'before'")
    ap.add_argument("--users", type=int, default=32)
    ap.add_argument("--seconds", type=int, default=40)
    args = ap.parse_args()
    tmp = HERE / ".locust"
    tmp.mkdir(exist_ok=True)
    u, t = args.users, (20 if args.quick else args.seconds)

    to_sub = {"LOAD_REPEAT_FRACTION": "0"}
    arbitrary = {"LOAD_REPEAT_FRACTION": "0", "LOAD_ARBITRARY_END": "1"}
    no_cache = lambda w: {"ROUTE_WORKERS": str(w), "ROUTE_CACHE_SIZE": "0"}
    plan = []
    if args.baseline_dir:
        plan += [("before: sync endpoint — unique routes to substations", args.baseline_dir, {}, to_sub),
                 ("before: sync endpoint — unique arbitrary routes", args.baseline_dir, {}, arbitrary)]
    workers = [8] if args.quick else [1, 4, 8, 16]
    plan += [(f"async + {w} workers — unique routes to substations", ROOT, no_cache(w), to_sub) for w in workers]
    plan += [(f"async + {w} workers — unique arbitrary routes", ROOT, no_cache(w), arbitrary) for w in ([8] if args.quick else [8, 16])]
    plan.append(("async + 8 workers + cache — 80% repeated routes", ROOT, {"ROUTE_WORKERS": "8"}, {"LOAD_REPEAT_FRACTION": "0.8"}))

    results = [bench(label, cwd, env, u, t, load, tmp) for label, cwd, env, load in plan]
    meta = {"when_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cpu": platform.processor(), "logical_cpus": os.cpu_count(),
            "physical_cores": psutil.cpu_count(logical=False), "python": platform.python_version(),
            "note": "Locust and the server share the machine; closed-loop users with zero think time."}
    (HERE / "results.json").write_text(json.dumps({"meta": meta, "results": results}, indent=2))

    lines = [f"# /route load test\n", f"{meta['when_utc']} · {meta['cpu']} ({meta['physical_cores']} cores / "
             f"{meta['logical_cpus']} threads) · Python {meta['python']} · {u} concurrent users, {t}s per run\n",
             "| Configuration | req/s | p50 ms | p95 ms | p99 ms | failures | server RSS MB |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['label']} | {r['rps']} | {r['p50_ms']} | {r['p95_ms']} | {r['p99_ms']} | "
                     f"{r['failures']}/{r['requests']} | {r['server_rss_mb']} |")
    (HERE / "RESULTS.md").write_text("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
