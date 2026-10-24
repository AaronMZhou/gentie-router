"""
FastAPI backend.

    python -m uvicorn backend.app:app --port 8000

POST /route            {start: [lon, lat], end: [lon, lat]} -> GeoJSON FeatureCollection
GET  /stats            executor, worker pids, cache hit/miss counters
POST /inventory        {start, end, voltage_kv?, capacity_mw?, need_by_weeks?, market?, include_terminals?}
                       -> BOM, supplier fill, long-lead order plan, on-time probability (see backend/inventory.py)
GET  /inventory/catalog  the simulated supplier catalog (suppliers, stock, lead-time bands, market scenarios)
GET  /inventory/bids     ?item=&qty=&voltage_kv=&capacity_mw=&market=&need_by_week=&site_lon=&site_lat=
                         -> mock supplier bids for one material, scored for comparison
GET  /bounds           study-region bbox + data provenance
GET  /overlays/{name}  firerisk (PNG over the bbox) | fire | protected | tribal | powerlines | roads | substations (GeoJSON)
GET  /assumptions      cost-model constants
GET  /                 the Cesium frontend (static files in frontend/)

Concurrency: /route is async; routing (CPU-bound) runs in a ProcessPoolExecutor whose
workers each load the cost grids once at startup and return pre-serialized JSON.
Finished responses go in an in-memory LRU, and identical requests already in flight
share one computation (single-flight).  Environment variables:

    ROUTE_WORKERS     worker processes (default: auto, up to 8 as CPUs and free memory allow;
                      0 = inline in a thread pool)
    ROUTE_CACHE_SIZE  cached responses (default 1024; 0 disables)
"""
import asyncio
import json
import os
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config as C
from backend import inventory, worker

LonLat = Annotated[list[float], Field(min_length=2, max_length=2, examples=[[-116.69, 33.10]])]
OVERLAYS = {"fire": "fire.geojson", "firerisk": "firerisk.png", "protected": "protected.geojson",
            "tribal": "tribal.geojson", "powerlines": "powerlines.geojson", "roads": "roads.geojson",
            "substations": "substations.geojson"}

MAX_AUTO_WORKERS = 8
WORKER_MB = 350         # measured peak ~260 MB per worker (each loads its own cost grids), plus headroom


def auto_workers():
    """As many workers as CPUs and free memory allow (capped at MAX_AUTO_WORKERS); 0 = run inline."""
    try:
        import psutil
        free_mb = psutil.virtual_memory().available / 1e6
    except ImportError:
        return 2
    return max(0, min(MAX_AUTO_WORKERS, os.cpu_count() or 1, int(free_mb // WORKER_MB) - 1))  # -1 leaves room for the API


WORKERS = int(os.getenv("ROUTE_WORKERS") or auto_workers())
CACHE_SIZE = int(os.getenv("ROUTE_CACHE_SIZE", "1024"))
CACHE_KEY_DECIMALS = 5  # ~1 m: requests for the same points share a cache entry


class RouteCache:
    """LRU of serialized /route responses, plus single-flight for identical in-flight requests."""

    def __init__(self, maxsize):
        self.maxsize = maxsize
        self.data = OrderedDict()   # key -> (status, body)
        self.inflight = {}          # key -> asyncio.Task
        self.hits = self.misses = self.coalesced = 0

    async def get_or_compute(self, key, compute):
        if key in self.data:
            self.data.move_to_end(key)
            self.hits += 1
            return (*self.data[key], "HIT")
        task = self.inflight.get(key)
        if task is not None:
            self.coalesced += 1
            state = "COALESCED"
        else:
            self.misses += 1
            state = "MISS"
            # a task, not a bare await: a client disconnect must not cancel work others are waiting on
            task = asyncio.ensure_future(compute())
            self.inflight[key] = task
            task.add_done_callback(lambda t: self._done(key, t))
        status, body = await asyncio.shield(task)
        return status, body, state

    def _done(self, key, task):
        self.inflight.pop(key, None)
        if task.cancelled() or task.exception() is not None or not self.maxsize:
            return
        status, body = task.result()
        if status == 200:
            self.data[key] = (status, body)
            while len(self.data) > self.maxsize:
                self.data.popitem(last=False)

    def stats(self):
        lookups = self.hits + self.misses + self.coalesced
        return {"size": len(self.data), "maxsize": self.maxsize, "hits": self.hits, "misses": self.misses,
                "coalesced": self.coalesced, "hit_rate": round((self.hits + self.coalesced) / lookups, 3) if lookups else None,
                "inflight": len(self.inflight)}


state = {"pool": None, "pids": [], "meta": None, "cache": RouteCache(CACHE_SIZE)}


@asynccontextmanager
async def lifespan(_app):
    state["meta"] = json.loads(C.META_PATH.read_text())
    mode = None
    if WORKERS > 0:
        pool = ProcessPoolExecutor(max_workers=WORKERS, initializer=worker.init_worker)
        loop = asyncio.get_running_loop()
        try:
            # spawn every worker (each loads the cost grids) before taking traffic
            state["pids"] = sorted(set(await asyncio.gather(*[loop.run_in_executor(pool, worker.warm) for _ in range(WORKERS)])))
            state["pool"] = pool
            mode = f"process pool, {len(state['pids'])} workers"
        except BrokenProcessPool:                     # typically out of memory: serve inline instead of failing
            pool.shutdown(cancel_futures=True)
            state["pids"] = []
            print(f"[gentie] could not start {WORKERS} route workers (low memory?); falling back to inline routing")
    if mode is None:
        worker.init_worker()
        mode = "inline (thread pool, single process)"
    print(f"[gentie] {mode}; cache {CACHE_SIZE} entries; synthetic layers: {state['meta']['synthetic_layers'] or 'none'}")
    yield
    if state["pool"]:
        state["pool"].shutdown(cancel_futures=True)


app = FastAPI(title="Gen-tie Router", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class RouteRequest(BaseModel):
    start: LonLat
    end: LonLat


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/bounds")
def bounds():
    meta = state["meta"]
    w, s, e, n = C.BBOX
    return {
        "region_name": C.REGION_NAME,
        "bbox": [w, s, e, n],
        "center": [(w + e) / 2, (s + n) / 2],
        "crs": meta["crs"],
        "cell_size_m": meta["cell_size_m"],
        "grid_shape": meta["shape"],
        "synthetic_layers": meta["synthetic_layers"],
        "sources": meta["sources"],
    }


@app.get("/assumptions")
def assumptions():
    return {k: getattr(C, k) for k in [
        "OVERHEAD_COST_PER_MILE", "UNDERGROUND_COST_PER_MILE", "OH_SLOPE_K", "UG_SLOPE_K",
        "UG_MAX_SLOPE_DEG", "UG_STEEP_PENALTY", "WHP_REF", "WHP_EXPONENT", "OH_FIRE_RISK_MAX_MULT",
        "OH_FIRE_TIER_MULT", "UG_FIRE_RISK_K", "HIGH_RISK_THRESHOLD", "OH_POWERLINE_MULT",
        "OH_ROAD_MULT", "UG_POWERLINE_MULT", "UG_ROAD_MULT", "MANAGED_LAND_MULT", "PROTECTED_PENALTY", "TRIBAL_PENALTY",
        "TRANSITION_COST_USD", "MIN_SEGMENT_M"]}


async def _compute(start, end):
    if state["pool"]:
        return await asyncio.get_running_loop().run_in_executor(state["pool"], worker.route_json, start, end)
    return await run_in_threadpool(worker.route_json, start, end)


async def _route_cached(start, end):
    key = tuple(round(v, CACHE_KEY_DECIMALS) for v in (*start, *end))
    return await state["cache"].get_or_compute(key, lambda: _compute(start, end))


@app.post("/route")
async def route(req: RouteRequest):
    status, body, cache_state = await _route_cached(req.start, req.end)
    return Response(content=body, status_code=status, media_type="application/json", headers={"X-Cache": cache_state})


class InventoryRequest(BaseModel):
    start: LonLat
    end: LonLat
    voltage_kv: Literal[69, 138] = 69
    capacity_mw: float = Field(100, gt=0, le=1000, description="project size; sets the generator step-up transformer MVA")
    need_by_weeks: float = Field(104, ge=0, le=520, description="weeks from today until construction must start")
    market: Literal["current", "tight", "crisis"] = "current"
    include_terminals: bool = Field(True, description="include breakers, switches, relays and the GSU at the two ends")


@app.post("/inventory")
async def inventory_assess(req: InventoryRequest):
    """Re-uses /route (and its cache), so asking for inventory right after a route costs almost nothing extra."""
    status, body, _ = await _route_cached(req.start, req.end)
    if status != 200:
        return Response(content=body, status_code=status, media_type="application/json")
    params = req.model_dump(exclude={"start", "end"})
    return await run_in_threadpool(inventory.assess, json.loads(body), **params)


@app.get("/inventory/catalog")
def inventory_catalog():
    return inventory.load_catalog()


@app.get("/inventory/bids")
def inventory_bids(item: str, qty: Annotated[float, Field(gt=0)], voltage_kv: int = 69,
                   capacity_mw: Annotated[float, Field(gt=0, le=1000)] = 100,
                   market: Literal["current", "tight", "crisis"] = "current",
                   need_by_week: float | None = None, site_lon: float | None = None, site_lat: float | None = None):
    """Mock supplier bids for one BOM line (the 'View suppliers' page)."""
    if voltage_kv not in (69, 138):          # an int Literal would not accept the query string "69"
        raise HTTPException(422, "voltage_kv must be 69 or 138")
    site = [site_lon, site_lat] if site_lon is not None and site_lat is not None else None
    try:
        return inventory.bids(item, qty, voltage_kv, capacity_mw, market, need_by_week, site)
    except KeyError:
        raise HTTPException(404, f"unknown item '{item}'")


@app.get("/stats")
def stats():
    return {"executor": "process" if state["pool"] else "inline", "workers": len(state["pids"]) or 1,
            "worker_pids": state["pids"], "cache": state["cache"].stats()}


@app.get("/overlays/{name}")
def overlay(name: str):
    if name not in OVERLAYS or not (C.OVERLAY_DIR / OVERLAYS[name]).exists():
        raise HTTPException(status_code=404, detail=f"unknown overlay '{name}'")
    path = C.OVERLAY_DIR / OVERLAYS[name]
    return FileResponse(path, media_type="image/png" if path.suffix == ".png" else "application/geo+json")


# Static frontend last so API routes take precedence.
app.mount("/", StaticFiles(directory=C.FRONTEND_DIR, html=True), name="frontend")
