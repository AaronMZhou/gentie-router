"""
FastAPI backend.

    python -m uvicorn backend.app:app --port 8000

POST /route            {start: [lon, lat], end: [lon, lat], preset?: balanced|avoid_fire|cheapest|underground}
                       -> GeoJSON FeatureCollection
GET  /health           liveness
GET  /stats            executor, worker pids, cache hit/miss counters
POST /inventory        {start, end, voltage_kv?, capacity_mw?, need_by_weeks?, market?, include_terminals?}
                       -> BOM, supplier fill, long-lead order plan, on-time probability (see backend/inventory.py)
GET  /inventory/catalog  the simulated supplier catalog (suppliers, stock, lead-time bands, market scenarios)
GET  /inventory/bids     ?item=&qty=&voltage_kv=&capacity_mw=&market=&need_by_week=&site_lon=&site_lat=
                         -> mock supplier bids for one material, scored for comparison
GET  /bounds           study-region bbox + data provenance
GET  /overlays/{name}  firerisk (PNG over the bbox) | fire | protected | tribal | powerlines | roads | substations (GeoJSON)
GET  /assumptions      cost-model constants + PRESETS {key: {label, blurb}}
GET  /projects         project summaries (route summaries + thumbnail geometry, no full GeoJSON)
POST /projects         {name, voltage_kv, phase, filing_target?}
GET/PATCH/DELETE /projects/{id}       PATCH with new start_lonlat/end_lonlat recomputes every route first
POST /projects/{id}/routes            {preset, start_lonlat?, end_lonlat?} -> computes + stores a route
GET/PATCH/DELETE /routes/{id}         PATCH {name}
                       store: SQLite at GENTIE_DB (data/projects.db), seeded with samples on first start
GET  /                 the multi-page frontend (static files in frontend/: index, dashboard, project, route)

Concurrency: /route is async; routing (CPU-bound) runs in a ProcessPoolExecutor whose
workers each load the cost grids once at startup and return pre-serialized JSON.
Finished responses go in an in-memory LRU, and identical requests already in flight
share one computation (single-flight).  Environment variables:

    ROUTE_WORKERS     worker processes (default: auto, up to 8 as CPUs and free memory allow;
                      0 = inline in a thread pool)
    ROUTE_CACHE_SIZE  cached responses (default 1024; 0 disables)
    GENTIE_DB         project store path (default data/projects.db)
    GENTIE_NO_SEED    1 = don't create sample projects in an empty store
"""
import asyncio
import json
import os
import sqlite3
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from backend.weather.weather_service import fetch_weather
from backend.weather.history_service import fetch_outlook

import config as C
from backend import inventory, seed, worker
from backend.store import Store

Phase = Literal["drafting", "comparing", "permit-risk", "ready"]
Preset = Literal[tuple(C.PRESETS)]
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


state = {"pool": None, "pids": [], "meta": None, "cache": RouteCache(CACHE_SIZE), "store": None}


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
    state["store"] = Store(os.getenv("GENTIE_DB", C.PROJECTS_DB))
    await seed.seed_if_empty(state["store"], cached_route)
    yield
    if state["pool"]:
        state["pool"].shutdown(cancel_futures=True)


app = FastAPI(title="Gen-tie Router", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class RouteRequest(BaseModel):
    start: LonLat
    end: LonLat
    preset: str = "balanced"  # validated by the router (unknown -> 400)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1)
    voltage_kv: int = Field(gt=0)
    phase: Phase
    filing_target: date | None = None


class ProjectPatch(BaseModel):
    # defaults aren't validated, so these may be omitted but an explicit null -> 422 (only filing_target is nullable)
    name: str = Field(None, min_length=1)
    voltage_kv: int = Field(None, gt=0)
    phase: Phase = None
    filing_target: date | None = None
    start_lonlat: LonLat = None
    end_lonlat: LonLat = None


class RouteCreate(BaseModel):
    preset: Preset
    start_lonlat: LonLat | None = None
    end_lonlat: LonLat | None = None


class RouteRename(BaseModel):
    name: str = Field(min_length=1)


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


async def cached_route(start, end, preset):
    """-> (status, json_bytes, cache_state); identical requests share one computation."""
    key = (*(round(v, CACHE_KEY_DECIMALS) for v in (*start, *end)), preset)
    return await state["cache"].get_or_compute(key, lambda: _compute(start, end, preset))


@app.post("/route")
async def route(req: RouteRequest):
    status, body, cache_state = await cached_route(req.start, req.end, req.preset)
    return Response(content=body, status_code=status, media_type="application/json", headers={"X-Cache": cache_state})


async def _route_or_400(start, end, preset):
    status, body, _ = await cached_route(start, end, preset)
    if status != 200:
        raise HTTPException(status_code=status, detail=json.loads(body)["detail"])
    return json.loads(body)


def _project_or_404(id):
    p = state["store"].get_project(id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"project {id} not found")
    return p


@app.get("/projects")
def list_projects():
    return state["store"].list_projects()


@app.post("/projects", status_code=201)
def create_project(req: ProjectCreate):
    return state["store"].create_project(**req.model_dump(mode="json"))


@app.get("/projects/{id}")
def get_project(id: int):
    return _project_or_404(id)


@app.patch("/projects/{id}")
async def update_project(id: int, req: ProjectPatch):
    p = _project_or_404(id)
    fields = req.model_dump(mode="json", exclude_unset=True)
    start, end = fields.get("start_lonlat", p["start_lonlat"]), fields.get("end_lonlat", p["end_lonlat"])
    results = []
    if (start, end) != (p["start_lonlat"], p["end_lonlat"]) and p["routes"]:
        # recompute every route first, so a bad endpoint (400) leaves the project untouched
        results = await asyncio.gather(*[_route_or_400(start, end, r["preset"]) for r in p["routes"]])
    for r, result in zip(p["routes"], results):
        state["store"].replace_route_result(r["id"], result)
    return state["store"].update_project(id, **fields) or _project_or_404(id)


@app.delete("/projects/{id}", status_code=204)
def delete_project(id: int):
    if not state["store"].delete_project(id):
        raise HTTPException(status_code=404, detail=f"project {id} not found")
    return Response(status_code=204)


@app.post("/projects/{id}/routes", status_code=201)
async def add_route(id: int, req: RouteCreate):
    p = _project_or_404(id)
    start, end = req.start_lonlat or p["start_lonlat"], req.end_lonlat or p["end_lonlat"]
    if not (start and end):
        raise HTTPException(status_code=400, detail="project has no endpoints yet: pass start_lonlat and end_lonlat")
    result = await _route_or_400(start, end, req.preset)
    try:
        if (start, end) != (p["start_lonlat"], p["end_lonlat"]):
            state["store"].update_project(id, start_lonlat=start, end_lonlat=end)
        summary = state["store"].add_route(id, C.PRESETS[req.preset]["label"], req.preset, result)
    except sqlite3.IntegrityError:  # project deleted while the route was computing
        raise HTTPException(status_code=404, detail=f"project {id} not found")
    return {"route_summary": summary, "result": result}


@app.get("/routes/{id}")
def get_route(id: int):
    r = state["store"].get_route(id)
    if r is None:
        raise HTTPException(status_code=404, detail=f"route {id} not found")
    return r


@app.patch("/routes/{id}")
def rename_route(id: int, req: RouteRename):
    r = state["store"].update_route(id, req.name)
    if r is None:
        raise HTTPException(status_code=404, detail=f"route {id} not found")
    return r


@app.delete("/routes/{id}", status_code=204)
def delete_route(id: int):
    if not state["store"].delete_route(id):
        raise HTTPException(status_code=404, detail=f"route {id} not found")
    return Response(status_code=204)


class InventoryRequest(BaseModel):
    start: LonLat
    end: LonLat
    voltage_kv: Literal[69, 138] = 69
    capacity_mw: float = Field(100, gt=0, le=1000, description="project size; sets the generator step-up transformer MVA")
    need_by_weeks: float = Field(104, ge=0, le=520, description="weeks from today until construction must start")
    market: Literal["current", "tight", "crisis"] = "current"
    preset: str = "balanced"
    include_terminals: bool = Field(True, description="include breakers, switches, relays and the GSU at the two ends")


@app.post("/inventory")
async def inventory_assess(req: InventoryRequest):
    """Re-uses /route (and its cache), so asking for inventory right after a route costs almost nothing extra."""
    route_fc = await _route_or_400(req.start, req.end, req.preset)
    params = req.model_dump(exclude={"start", "end", "preset"})
    return await run_in_threadpool(inventory.assess, route_fc, **params)


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

class WeatherPoint(BaseModel):
    id: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class WeatherRequest(BaseModel):
    locations: list[WeatherPoint] = Field(min_length=1, max_length=50)
    forecast_days: int = Field(3, ge=1, le=7)


@app.post("/weather/route")
def weather_route(req: WeatherRequest):
    # plain `def`: FastAPI runs it in a thread pool, so the blocking client doesn't stall the event loop
    try:
        locations = fetch_weather([p.model_dump() for p in req.locations], req.forecast_days)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    except Exception:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=502, detail="Weather provider unavailable.")
    return {"locations": locations}

class OutlookLimits(BaseModel):
    wind: float = 50
    gust: float = 70
    heat: float = 35
    cold: float = -10


class OutlookRequest(BaseModel):
    locations: list[WeatherPoint] = Field(min_length=1, max_length=20)
    limits: OutlookLimits = OutlookLimits()
    past_days: int = Field(92, ge=14, le=92)
    horizon_days: int = Field(14, ge=1, le=16)

@app.post("/weather/outlook")
def weather_outlook(req: OutlookRequest):
    try:
        return fetch_outlook(
            [p.model_dump() for p in req.locations],
            req.limits.model_dump(),
            req.past_days,
            req.horizon_days,
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    except Exception:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=502, detail="Weather provider unavailable.")

    

# Static frontend last so API routes take precedence.
app.mount("/", StaticFiles(directory=C.FRONTEND_DIR, html=True), name="frontend")
