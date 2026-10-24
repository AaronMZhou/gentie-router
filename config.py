"""
Central configuration: region, grid, data paths and every cost-model weight.

ALL DOLLAR FIGURES AND MULTIPLIERS BELOW ARE ROUGH PLANNING ASSUMPTIONS for a
hackathon demo, not engineering estimates. Tweak them freely and re-run the
backend (no need to re-run data prep - cost grids are built at server start).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# --------------------------------------------------------------------------
# Region
# --------------------------------------------------------------------------
# San Diego County backcountry: Ramona -> Santa Ysabel -> Julian.
# Mountainous (≈400-1,800 m), almost entirely CPUC Tier 2/3 High Fire-Threat
# District (Cedar 2003 / Witch 2007 fire footprints), mixed Cleveland NF,
# state/county preserves and tribal land, plus SDG&E 69 kV lines.
REGION_NAME = "San Diego backcountry (Ramona - Santa Ysabel - Julian)"
BBOX = (-116.89, 32.94, -116.56, 33.21)  # (west, south, east, north) EPSG:4326

# Projected working CRS (UTM zone 11N, metres) and raster cell size.
WORK_CRS = "EPSG:32611"
CELL_SIZE_M = 30.0

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"              # cached downloads
PROCESSED_DIR = DATA_DIR / "processed"  # aligned grids + overlays
OVERLAY_DIR = PROCESSED_DIR / "overlays"
META_PATH = PROCESSED_DIR / "meta.json"
FRONTEND_DIR = ROOT / "frontend"
DEMO_ROUTE_PATH = FRONTEND_DIR / "demo_route.json"

# --------------------------------------------------------------------------
# Data sources (public REST endpoints, verified Oct 2026)
# --------------------------------------------------------------------------
DEM_3DEP_URL = ("https://elevation.nationalmap.gov/arcgis/rest/services/"
                "3DEPElevation/ImageServer/exportImage")
PADUS_URL = ("https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services/"
             "Fee_Manager/FeatureServer/0")
HFTD_URL = ("https://services.arcgis.com/BLN4oKB0N1YSgvY8/ArcGIS/rest/services/"
            "CPUC_Fire_Threat_Areas/FeatureServer")
HFTD_LAYERS = {0: 3, 1: 2}  # layer id -> fire level (Tier 3 extreme, Tier 2 elevated)
FHSZ_URL = ("https://services.gis.ca.gov/arcgis/rest/services/Environment/"
            "Fire_Severity_Zones/MapServer")
FHSZ_LAYERS = [0, 1]        # SRA 2007 + LRA 2011; HAZ_CODE 1=Moderate 2=High 3=Very High
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# PAD-US classification
PROTECTED_STRICT_GAP = {"1", "2"}                 # managed for biodiversity
PROTECTED_STRICT_DESIGNATIONS = {"SP", "WA", "WSA", "NP", "NM", "SW"}  # state park, wilderness, ...
PROTECTED_MANAGED_GAP = {"3"}                     # multiple-use (e.g. National Forest, BLM)

# Existing-corridor buffers (half-widths of the right-of-way we'd share)
POWERLINE_BUFFER_M = 60.0
ROAD_BUFFER_M = 30.0

# --------------------------------------------------------------------------
# Cost model  (ASSUMPTIONS - order-of-magnitude, 69-138 kV gen-tie)
# --------------------------------------------------------------------------
METERS_PER_MILE = 1609.344

# Base construction cost on flat, unconstrained, low-fire-risk land.
OVERHEAD_COST_PER_MILE = 2_000_000      # ASSUMPTION: single-circuit 69-138 kV, steel poles
UNDERGROUND_COST_PER_MILE = 10_000_000  # ASSUMPTION: ~5x overhead (duct bank + vaults + trenching)

# Slope multipliers.
# Overhead: grows linearly (access roads, pads, longer spans): 1 + k * slope/10deg
OH_SLOPE_K = 0.15            # 10deg -> 1.15x, 30deg -> 1.45x
# Underground: grows quadratically (trenching on grade, rock, erosion control)
UG_SLOPE_K = 0.6             # 10deg -> 1.6x, 20deg -> 3.4x
UG_MAX_SLOPE_DEG = 25.0      # above this, trenching is impractical...
UG_STEEP_PENALTY = 4.0       # ...so apply an extra multiplier

# Wildfire multipliers by fire level
# (0 = none, 1 = moderate, 2 = high / HFTD Tier 2, 3 = very high / HFTD Tier 3).
# Overhead captures hardening (covered conductor, fast-trip relays), PSPS
# de-energisation risk, vegetation management and ignition liability.
OH_FIRE_MULT = {0: 1.0, 1: 1.25, 2: 2.0, 3: 6.0}
# Underground lines can't start wildfires; small factor for access/permitting.
UG_FIRE_MULT = {0: 1.0, 1: 1.0, 2: 1.05, 3: 1.1}
HIGH_FIRE_LEVEL = 2          # fire level >= this counts toward "high_fire_km"

# Existing corridors (multipliers < 1 = discount).  Overhead lines like to
# parallel existing transmission; underground cables like road right-of-way.
OH_POWERLINE_MULT = 0.6
OH_ROAD_MULT = 0.85
UG_POWERLINE_MULT = 0.9
UG_ROAD_MULT = 0.6

# Land status
MANAGED_LAND_MULT = 1.25     # GAP 3 multiple-use federal/state land (extra permitting, NEPA)
PROTECTED_PENALTY = 1000.0   # GAP 1-2 / parks / wilderness: routing penalty (near-impassable)
OUTSIDE_REGION_PENALTY = 1e4 # cells in the projected grid but outside the lon/lat bbox

# Post-processing of the route
MIN_SEGMENT_M = 450.0        # overhead/underground runs shorter than this are merged into neighbours
TRANSITION_COST_USD = 750_000  # ASSUMPTION: riser pole / transition structure per OH<->UG switch
TOWER_SPACING_M = 300.0      # visual tower markers along overhead segments
SIMPLIFY_TOLERANCE_M = 20.0
