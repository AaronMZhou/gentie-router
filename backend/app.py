"""
FastAPI backend.

    python -m uvicorn backend.app:app --port 8000

POST /route            {start: [lon, lat], end: [lon, lat], preset?: balanced|avoid_fire|cheapest|underground}
                       -> GeoJSON FeatureCollection
GET  /stats            executor, worker pids, cache hit/miss counters
GET  /bounds           study-region bbox + data provenance
GET  /overlays/{name}  firerisk (PNG over the bbox) | fire | protected | tribal | powerlines | roads | substations (GeoJSON)
GET  /assumptions      cost-model constants
GET  /                 the Cesium frontend (static files in frontend/)

Concurrency: /route is async; routing (CPU-bound) runs in a ProcessPoolExecutor whose
workers each load the cost grids once at startup and return pre-serialized JSON.
Finished responses go in an in-memory LRU, and identical requests already in flight
share one computation (single-flight).  Environment variables:

    ROUTE_WORKERS     worker processes (default 8; 0 = inline in a thread pool)
    ROUTE_CACHE_SIZE  cached responses (default 1024; 0 disables)
"""
import asyncio
import json
import os
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config as C
from backend import worker

LonLat = Annotated[list[float], Field(min_length=2, max_length=2, examples=[[-116.69, 33.10]])]
OVERLAYS = {"fire": "fire.geojson", "firerisk": "firerisk.png", "protected": "protected.geojson",
            "tribal": "tribal.geojson", "powerlines": "powerlines.geojson", "roads": "roads.geojson",
            "substations": "substations.geojson"}

WORKERS = int(os.getenv("ROUTE_WORKERS", "8"))
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
    if WORKERS > 0:
        pool = ProcessPoolExecutor(max_workers=WORKERS, initializer=worker.init_worker)
        loop = asyncio.get_running_loop()
        # spawn every worker (each loads the cost grids) before taking traffic
        state["pids"] = sorted(set(await asyncio.gather(*[loop.run_in_executor(pool, worker.warm) for _ in range(WORKERS)])))
        state["pool"] = pool
        mode = f"process pool, {len(state['pids'])} workers"
    else:
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
    preset: str = "balanced"  # validated by the router (unknown -> 400)


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
    presets = {name: {"label": p["label"], "blurb": p["blurb"]} for name, p in C.PRESETS.items()}
    return {"PRESETS": presets, **{k: getattr(C, k) for k in [
        "OVERHEAD_COST_PER_MILE", "UNDERGROUND_COST_PER_MILE", "OH_SLOPE_K", "UG_SLOPE_K",
        "UG_MAX_SLOPE_DEG", "UG_STEEP_PENALTY", "WHP_REF", "WHP_EXPONENT", "OH_FIRE_RISK_MAX_MULT",
        "OH_FIRE_TIER_MULT", "UG_FIRE_RISK_K", "HIGH_RISK_THRESHOLD", "OH_POWERLINE_MULT",
        "OH_ROAD_MULT", "UG_POWERLINE_MULT", "UG_ROAD_MULT", "MANAGED_LAND_MULT", "PROTECTED_PENALTY", "TRIBAL_PENALTY",
        "TRANSITION_COST_USD", "MIN_SEGMENT_M"]}}


async def _compute(start, end, preset):
    if state["pool"]:
        return await asyncio.get_running_loop().run_in_executor(state["pool"], worker.route_json, start, end, preset)
    return await run_in_threadpool(worker.route_json, start, end, preset)


@app.post("/route")
async def route(req: RouteRequest):
    key = (*(round(v, CACHE_KEY_DECIMALS) for v in (*req.start, *req.end)), req.preset)
    status, body, cache_state = await state["cache"].get_or_compute(key, lambda: _compute(req.start, req.end, req.preset))
    return Response(content=body, status_code=status, media_type="application/json", headers={"X-Cache": cache_state})


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
