"""
Builds per-cell cost grids from the aligned rasters written by data/prepare_data.py.

All costs are in USD per metre of line through that cell.

  overhead    = OH base   x slope_mult_oh x fire_mult_oh x corridor_oh x land
  underground = UG base   x slope_mult_ug x fire_mult_ug x corridor_ug x land

  fire_mult_oh = (1 + (OH_FIRE_RISK_MAX_MULT - 1) * risk) x OH_FIRE_TIER_MULT[level]
  fire_mult_ug = 1 + UG_FIRE_RISK_K * risk
  dollar      = min(overhead, underground)       (est. construction cost)
  routing     = dollar x penalty                  (penalty = protected / tribal / outside-bbox)
  mode        = 0 overhead | 1 underground       (which one won per cell)
"""
import json
from dataclasses import dataclass

import numpy as np
import rasterio

import config as C

OVERHEAD, UNDERGROUND = 0, 1
MODE_NAMES = {OVERHEAD: "overhead", UNDERGROUND: "underground"}


@dataclass
class CostGrids:
    elevation: np.ndarray
    slope: np.ndarray
    fire: np.ndarray          # 0-3 regulatory tier (HFTD / FHSZ)
    fire_risk: np.ndarray     # 0-1 continuous hazard (WHP)
    protected: np.ndarray
    owner: np.ndarray         # config.OWNER_NAMES code
    landunit: np.ndarray      # PAD-US unit id -> meta["land_units"][id - 1]
    corridor: np.ndarray
    region: np.ndarray
    parcel_density: np.ndarray       # est. parcels crossed per km of line
    parcel_id: np.ndarray | None     # real parcel ids, only if a parcel file was provided
    overhead: np.ndarray      # $/m
    underground: np.ndarray   # $/m
    dollar: np.ndarray        # $/m, cheaper of the two
    mode: np.ndarray          # 0 overhead / 1 underground
    routing: np.ndarray       # $/m incl. penalties - what the router minimises
    transform: rasterio.Affine
    meta: dict


def _read(name):
    with rasterio.open(C.PROCESSED_DIR / f"{name}.tif") as ds:
        return ds.read(1), ds.transform


def _lut(levels, table):
    lut = np.array([table[k] for k in sorted(table)], dtype=np.float32)
    return lut[np.clip(levels, 0, len(lut) - 1)]


def build_cost_grids(corridor_override=None, overrides=None):
    """corridor_override: replacement corridor raster (used by leave-one-out validation).
    overrides: {CONFIG_NAME: value} replacing cost-model constants (strategy presets, config.PRESETS)."""
    overrides = overrides or {}
    _c = lambda name: overrides.get(name, getattr(C, name))
    if not C.META_PATH.exists():
        raise FileNotFoundError("Run `python data/prepare_data.py` first (data/processed is missing).")
    meta = json.loads(C.META_PATH.read_text())
    elevation, transform = _read("elevation")
    slope, _ = _read("slope")
    fire, _ = _read("fire")
    fire_risk, _ = _read("firerisk")
    protected, _ = _read("protected")
    owner, _ = _read("owner")
    landunit, _ = _read("landunit")
    corridor = corridor_override if corridor_override is not None else _read("corridor")[0]
    region, _ = _read("region")
    parcel_density, _ = _read("parceldens")
    parcel_id = _read("parcelid")[0] if (C.PROCESSED_DIR / "parcelid.tif").exists() else None

    oh_base = _c("OVERHEAD_COST_PER_MILE") / _c("METERS_PER_MILE")
    ug_base = _c("UNDERGROUND_COST_PER_MILE") / _c("METERS_PER_MILE")

    s10 = slope / 10.0
    oh_slope = 1.0 + _c("OH_SLOPE_K") * s10
    ug_slope = 1.0 + _c("UG_SLOPE_K") * s10 ** 2
    ug_slope = np.where(slope > _c("UG_MAX_SLOPE_DEG"), ug_slope * _c("UG_STEEP_PENALTY"), ug_slope)

    road = (corridor & 1) > 0
    power = (corridor & 2) > 0
    oh_corr = np.minimum(np.where(power, _c("OH_POWERLINE_MULT"), 1.0), np.where(road, _c("OH_ROAD_MULT"), 1.0))
    ug_corr = np.minimum(np.where(power, _c("UG_POWERLINE_MULT"), 1.0), np.where(road, _c("UG_ROAD_MULT"), 1.0))

    land = np.where(protected == 1, _c("MANAGED_LAND_MULT"), 1.0)

    oh_fire = (1.0 + (_c("OH_FIRE_RISK_MAX_MULT") - 1.0) * fire_risk) * _lut(fire, _c("OH_FIRE_TIER_MULT"))
    ug_fire = 1.0 + _c("UG_FIRE_RISK_K") * fire_risk

    overhead = (oh_base * oh_slope * oh_fire * oh_corr * land).astype(np.float32)
    underground = (ug_base * ug_slope * ug_fire * ug_corr * land).astype(np.float32)

    mode = (underground < overhead).astype(np.uint8)
    dollar = np.minimum(overhead, underground)

    penalty = np.ones_like(dollar)
    penalty[owner == _c("TRIBAL")] = _c("TRIBAL_PENALTY")
    penalty[protected == 2] = _c("PROTECTED_PENALTY")
    penalty[(protected == 2) & power] = _c("PROTECTED_IN_CORRIDOR_PENALTY")
    penalty[region == 0] = _c("OUTSIDE_REGION_PENALTY")
    routing = (dollar * penalty).astype(np.float32)

    return CostGrids(elevation=elevation, slope=slope, fire=fire, fire_risk=fire_risk, protected=protected, owner=owner,
                     landunit=landunit, corridor=corridor,
                     region=region, parcel_density=parcel_density, parcel_id=parcel_id, overhead=overhead, underground=underground, dollar=dollar, mode=mode,
                     routing=routing, transform=transform, meta=meta)
