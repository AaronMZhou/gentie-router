"""
FastAPI backend.

    python -m uvicorn backend.app:app --port 8000

POST /route            {start: [lon, lat], end: [lon, lat]} -> GeoJSON FeatureCollection
GET  /bounds           study-region bbox + data provenance
GET  /overlays/{name}  firerisk (PNG over the bbox) | fire | protected | tribal | powerlines | roads | substations (GeoJSON)
GET  /assumptions      cost-model constants
GET  /                 the Cesium frontend (static files in frontend/)
"""
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config as C
from backend.router import Router, RouteError

LonLat = Annotated[list[float], Field(min_length=2, max_length=2, examples=[[-116.69, 33.10]])]
OVERLAYS = {"fire": "fire.geojson", "firerisk": "firerisk.png", "protected": "protected.geojson",
            "tribal": "tribal.geojson", "powerlines": "powerlines.geojson", "roads": "roads.geojson",
            "substations": "substations.geojson"}

_router: Router | None = None


def get_router() -> Router:
    global _router
    if _router is None:
        _router = Router()
    return _router


@asynccontextmanager
async def lifespan(_app):
    r = get_router()  # build cost grids once at startup
    print(f"[gentie] cost grids ready {r.g.routing.shape}; synthetic layers: {r.g.meta['synthetic_layers'] or 'none'}")
    yield


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
    meta = get_router().g.meta
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


@app.post("/route")
def route(req: RouteRequest):
    try:
        return get_router().route(req.start, req.end)
    except RouteError as ex:
        raise HTTPException(status_code=400, detail=str(ex))


@app.get("/overlays/{name}")
def overlay(name: str):
    if name not in OVERLAYS or not (C.OVERLAY_DIR / OVERLAYS[name]).exists():
        raise HTTPException(status_code=404, detail=f"unknown overlay '{name}'")
    path = C.OVERLAY_DIR / OVERLAYS[name]
    return FileResponse(path, media_type="image/png" if path.suffix == ".png" else "application/geo+json")


# Static frontend last so API routes take precedence.
app.mount("/", StaticFiles(directory=C.FRONTEND_DIR, html=True), name="frontend")
