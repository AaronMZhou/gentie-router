"""
Builds per-cell cost grids from the aligned rasters written by data/prepare_data.py.

All costs are in USD per metre of line through that cell.

  overhead    = OH base   x slope_mult_oh x fire_mult_oh x corridor_oh x land
  underground = UG base   x slope_mult_ug x fire_mult_ug x corridor_ug x land
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
    fire: np.ndarray
    protected: np.ndarray
    owner: np.ndarray         # config.OWNER_NAMES code
    landunit: np.ndarray      # PAD-US unit id -> meta["land_units"][id - 1]
    corridor: np.ndarray
    region: np.ndarray
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


def build_cost_grids():
    if not C.META_PATH.exists():
        raise FileNotFoundError("Run `python data/prepare_data.py` first (data/processed is missing).")
    meta = json.loads(C.META_PATH.read_text())
    elevation, transform = _read("elevation")
    slope, _ = _read("slope")
    fire, _ = _read("fire")
    protected, _ = _read("protected")
    owner, _ = _read("owner")
    landunit, _ = _read("landunit")
    corridor, _ = _read("corridor")
    region, _ = _read("region")

    oh_base = C.OVERHEAD_COST_PER_MILE / C.METERS_PER_MILE
    ug_base = C.UNDERGROUND_COST_PER_MILE / C.METERS_PER_MILE

    s10 = slope / 10.0
    oh_slope = 1.0 + C.OH_SLOPE_K * s10
    ug_slope = 1.0 + C.UG_SLOPE_K * s10 ** 2
    ug_slope = np.where(slope > C.UG_MAX_SLOPE_DEG, ug_slope * C.UG_STEEP_PENALTY, ug_slope)

    road = (corridor & 1) > 0
    power = (corridor & 2) > 0
    oh_corr = np.minimum(np.where(power, C.OH_POWERLINE_MULT, 1.0), np.where(road, C.OH_ROAD_MULT, 1.0))
    ug_corr = np.minimum(np.where(power, C.UG_POWERLINE_MULT, 1.0), np.where(road, C.UG_ROAD_MULT, 1.0))

    land = np.where(protected == 1, C.MANAGED_LAND_MULT, 1.0)

    overhead = (oh_base * oh_slope * _lut(fire, C.OH_FIRE_MULT) * oh_corr * land).astype(np.float32)
    underground = (ug_base * ug_slope * _lut(fire, C.UG_FIRE_MULT) * ug_corr * land).astype(np.float32)

    mode = (underground < overhead).astype(np.uint8)
    dollar = np.minimum(overhead, underground)

    penalty = np.ones_like(dollar)
    penalty[owner == C.TRIBAL] = C.TRIBAL_PENALTY
    penalty[protected == 2] = C.PROTECTED_PENALTY
    penalty[region == 0] = C.OUTSIDE_REGION_PENALTY
    routing = (dollar * penalty).astype(np.float32)

    return CostGrids(elevation=elevation, slope=slope, fire=fire, protected=protected, owner=owner,
                     landunit=landunit, corridor=corridor,
                     region=region, overhead=overhead, underground=underground, dollar=dollar, mode=mode,
                     routing=routing, transform=transform, meta=meta)
