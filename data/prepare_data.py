"""
One-off data pipeline.

Downloads (or loads from cache) every input layer, clips it to config.BBOX,
reprojects to config.WORK_CRS and burns it onto ONE aligned ~30 m grid:

    data/processed/elevation.tif   float32  metres (USGS 3DEP)
    data/processed/slope.tif       float32  degrees
    data/processed/protected.tif   uint8    0 none, 1 managed (GAP 3), 2 strict (GAP 1-2/park/wilderness)
    data/processed/owner.tif       uint8    config.OWNER_NAMES: 0 private .. 4 tribal, 5 ngo
    data/processed/landunit.tif    int32    PAD-US unit id (0 = none) -> meta.json "land_units"
    data/processed/fire.tif        uint8    0 none .. 3 very high / HFTD Tier 3
    data/processed/firerisk.tif    float32  0..1 continuous hazard (USFS WHP; fallback: from fire.tif)
    data/processed/corridor.tif    uint8    bit 1 = major road, bit 2 = power line
    data/processed/region.tif      uint8    1 inside the lon/lat bbox
    data/processed/parceldens.tif  float32  est. parcels crossed per km of line (from building density)
    data/processed/parcelid.tif    int32    real parcel ids - only if data/raw/parcels.* was provided
    data/processed/overlays/*.geojson       lon/lat polygons/lines for the map
    data/processed/meta.json                grid + provenance (incl. synthetic layers)

Any layer whose download fails is replaced by clearly-labelled SYNTHETIC data
so development is never blocked.

Usage:  python data/prepare_data.py [--refresh] [--synthetic-all]
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
import rasterio
from rasterio.features import rasterize, shapes
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds, from_origin
from rasterio.warp import Resampling, reproject
import geopandas as gpd
from pyproj import Transformer
from scipy.ndimage import gaussian_filter
from shapely.geometry import box, shape, LineString, mapping
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

HEADERS = {"User-Agent": "gentie-router-hackathon/0.1"}
RNG = np.random.default_rng(42)


# ---------------------------------------------------------------------------
# Grid definition
# ---------------------------------------------------------------------------
def build_grid():
    """Projected grid covering the lon/lat bbox, snapped to CELL_SIZE_M."""
    w, s, e, n = C.BBOX
    to_utm = Transformer.from_crs("EPSG:4326", C.WORK_CRS, always_xy=True)
    xs, ys = to_utm.transform([w, e, e, w], [s, s, n, n])
    cs = C.CELL_SIZE_M
    xmin, xmax = np.floor(min(xs) / cs) * cs, np.ceil(max(xs) / cs) * cs
    ymin, ymax = np.floor(min(ys) / cs) * cs, np.ceil(max(ys) / cs) * cs
    ncols, nrows = int(round((xmax - xmin) / cs)), int(round((ymax - ymin) / cs))
    transform = from_origin(xmin, ymax, cs, cs)
    return dict(xmin=xmin, ymin=ymin, xmax=xmax, ymax=ymax,
                ncols=ncols, nrows=nrows, transform=transform)


def region_mask(grid):
    """1 where the cell centre lies inside the lon/lat bbox."""
    t = grid["transform"]
    cols, rows = np.meshgrid(np.arange(grid["ncols"]), np.arange(grid["nrows"]))
    x = t.c + (cols + 0.5) * t.a
    y = t.f + (rows + 0.5) * t.e
    to_ll = Transformer.from_crs(C.WORK_CRS, "EPSG:4326", always_xy=True)
    lon, lat = to_ll.transform(x, y)
    w, s, e, n = C.BBOX
    return ((lon >= w) & (lon <= e) & (lat >= s) & (lat <= n)).astype(np.uint8)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------
def cached_json(name, fetch_fn, refresh):
    path = C.RAW_DIR / name
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    data = fetch_fn()
    path.write_text(json.dumps(data))
    return data


def arcgis_query_geojson(layer_url, out_fields="*", where="1=1", max_offset_deg=0.0002):
    """Query an ArcGIS Feature/Map service layer for features intersecting the bbox."""
    w, s, e, n = C.BBOX
    feats, offset = [], 0
    while True:
        params = dict(
            where=where, geometry=f"{w},{s},{e},{n}", geometryType="esriGeometryEnvelope",
            inSR=4326, spatialRel="esriSpatialRelIntersects", outFields=out_fields,
            returnGeometry="true", outSR=4326, maxAllowableOffset=max_offset_deg,
            resultOffset=offset, f="geojson",
        )
        r = requests.get(f"{layer_url}/query", params=params, headers=HEADERS, timeout=120)
        r.raise_for_status()
        js = r.json()
        if "error" in js:
            raise RuntimeError(js["error"])
        batch = js.get("features", [])
        feats += batch
        exceeded = js.get("exceededTransferLimit") or js.get("properties", {}).get("exceededTransferLimit")
        if not exceeded or not batch:
            break
        offset += len(batch)
    return {"type": "FeatureCollection", "features": feats}


def to_work_gdf(fc):
    """GeoJSON (lon/lat) -> GeoDataFrame in WORK_CRS clipped to the bbox."""
    feats = [f for f in fc["features"] if f.get("geometry")]
    if not feats:
        return gpd.GeoDataFrame(geometry=[], crs=C.WORK_CRS)
    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
    gdf["geometry"] = gdf.geometry.make_valid()
    gdf = gdf.clip(box(*C.BBOX).buffer(0.01))
    gdf = gdf[~gdf.geometry.is_empty]
    return gdf.to_crs(C.WORK_CRS)


def burn(geoms, grid, value=1, all_touched=False):
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return np.zeros((grid["nrows"], grid["ncols"]), np.uint8)
    return rasterize(((g, value) for g in geoms), out_shape=(grid["nrows"], grid["ncols"]),
                     transform=grid["transform"], fill=0, dtype="uint8", all_touched=all_touched)


def smooth_noise(grid, sigma_cells):
    z = gaussian_filter(RNG.standard_normal((grid["nrows"], grid["ncols"])), sigma_cells)
    return (z - z.mean()) / (z.std() + 1e-9)


# ---------------------------------------------------------------------------
# Layer 1: elevation (USGS 3DEP) -> slope
# ---------------------------------------------------------------------------
def export_image(url, grid, cache_name, refresh, interpolation="RSP_BilinearInterpolation"):
    """ArcGIS ImageServer exportImage straight onto our grid (same extent, size and CRS)."""
    path = C.RAW_DIR / cache_name
    if not path.exists() or refresh:
        epsg = int(C.WORK_CRS.split(":")[1])
        params = dict(
            bbox=f"{grid['xmin']},{grid['ymin']},{grid['xmax']},{grid['ymax']}",
            bboxSR=epsg, imageSR=epsg, size=f"{grid['ncols']},{grid['nrows']}",
            format="tiff", pixelType="F32", noData=-9999, interpolation=interpolation, f="image",
        )
        r = requests.get(url, params=params, headers=HEADERS, timeout=180)
        r.raise_for_status()
        if "tiff" not in r.headers.get("Content-Type", ""):
            raise RuntimeError(f"{url} returned {r.headers.get('Content-Type')}: {r.text[:200]}")
        path.write_bytes(r.content)
    with MemoryFile(path.read_bytes()) as mf, mf.open() as ds:
        arr = ds.read(1).astype(np.float32)
    if arr.shape != (grid["nrows"], grid["ncols"]):
        raise RuntimeError(f"{cache_name} shape {arr.shape} != grid")
    return arr


def fetch_dem(grid, refresh):
    dem = export_image(C.DEM_3DEP_URL, grid, "dem_3dep.tif", refresh)
    bad = (dem < -500) | ~np.isfinite(dem)
    if bad.mean() > 0.05:
        raise RuntimeError(f"DEM has {bad.mean():.0%} nodata")
    if bad.any():
        dem[bad] = np.nanmedian(dem[~bad])
    return dem


def synthetic_dem(grid):
    """Fractal hills 400-1800 m with a NE-trending ridge (labelled SYNTHETIC)."""
    rows, cols = np.mgrid[0:grid["nrows"], 0:grid["ncols"]]
    u = (cols - rows) / max(grid["nrows"], grid["ncols"])
    ridge = np.exp(-((u - 0.1) ** 2) / 0.05)
    z = sum(smooth_noise(grid, s) * a for s, a in [(80, 1.0), (30, 0.5), (10, 0.2), (3, 0.05)])
    z = 0.6 * z / np.abs(z).max() + ridge
    z = (z - z.min()) / (z.max() - z.min())
    return (400 + 1400 * z).astype(np.float32)


def slope_deg(dem):
    gy, gx = np.gradient(dem, C.CELL_SIZE_M)
    return np.degrees(np.arctan(np.hypot(gx, gy))).astype(np.float32)


# ---------------------------------------------------------------------------
# Layer 2: land status (PAD-US): protection class, owner category, named units
# ---------------------------------------------------------------------------
def land_from_units(gdf, grid):
    """Burn one id per land unit (smallest last, so inholdings win) and derive per-cell layers.

    gdf columns: name, owner (code), protected (0/1/2), designation, gap; geometry in WORK_CRS.
    Returns dict(protected, owner, unit, units, tribal_fc).
    """
    gdf = gdf.assign(area=gdf.geometry.area).sort_values("area", ascending=False).reset_index(drop=True)
    unit = rasterize(((g, i + 1) for i, g in enumerate(gdf.geometry)), out_shape=(grid["nrows"], grid["ncols"]),
                     transform=grid["transform"], fill=0, dtype="int32")
    owner_lut = np.r_[0, gdf["owner"].to_numpy()].astype(np.uint8)
    prot_lut = np.r_[0, gdf["protected"].to_numpy()].astype(np.uint8)
    units = [dict(id=i + 1, name=r["name"], owner=C.OWNER_NAMES[int(r["owner"])], protected=int(r["protected"]),
                  designation=r["designation"], gap=r["gap"]) for i, r in gdf.iterrows()]
    tribal = gdf[gdf["owner"] == C.TRIBAL].to_crs("EPSG:4326")
    tribal_fc = json.loads(tribal[["name", "geometry"]].to_json()) if len(tribal) else {"type": "FeatureCollection", "features": []}
    return dict(protected=prot_lut[unit], owner=owner_lut[unit], unit=unit, units=units, tribal_fc=tribal_fc)


def fetch_land(grid, refresh):
    fc = cached_json("padus_v2.geojson", lambda: arcgis_query_geojson(
        C.PADUS_URL, out_fields="Unit_Nm,GAP_Sts,Des_Tp,Mang_Name,Mang_Type,Own_Type,Own_Name"), refresh)
    gdf = to_work_gdf(fc)
    if gdf.empty:
        raise RuntimeError("PAD-US returned no features")
    gap = gdf["GAP_Sts"].astype(str)
    strict = gap.isin(C.PROTECTED_STRICT_GAP) | gdf["Des_Tp"].isin(C.PROTECTED_STRICT_DESIGNATIONS)
    managed = gap.isin(C.PROTECTED_MANAGED_GAP) & ~strict
    tribal = (gdf["Mang_Type"] == "TRIB") | (gdf["Des_Tp"] == "TRIBL")
    owner = gdf["Own_Type"].map(C.OWNER_FROM_PADUS).fillna(0).astype(int)
    owner[tribal] = C.TRIBAL
    units = gpd.GeoDataFrame(dict(
        name=gdf["Unit_Nm"].fillna("Unnamed unit"), owner=owner,
        protected=np.where(strict, 2, np.where(managed, 1, 0)),
        designation=gdf["Des_Tp"].fillna(""), gap=gap), geometry=gdf.geometry, crs=C.WORK_CRS)
    print(f"    strict units: {', '.join(sorted(set(units.loc[strict, 'name'])))}")
    print(f"    tribal units: {', '.join(sorted(set(units.loc[tribal, 'name'])))}")
    return land_from_units(units, grid)


def synthetic_land(grid):
    """Noise blobs -> a managed forest, two preserves and a reservation (labelled SYNTHETIC)."""
    t = grid["transform"]
    shapes_ = []
    for name, owner, prot, sigma, thr in [("SYNTHETIC National Forest", 1, 1, 60, 0.3),
                                          ("SYNTHETIC Preserve", 3, 2, 25, 1.6),
                                          ("SYNTHETIC Reservation", C.TRIBAL, 0, 40, 1.4)]:
        mask = (smooth_noise(grid, sigma) > thr).astype(np.uint8)
        geoms = [shape(g) for g, v in shapes(mask, mask=mask > 0, transform=t)]
        if geoms:
            shapes_.append(dict(name=name, owner=owner, protected=prot, designation="SYN", gap="",
                                geometry=unary_union(geoms)))
    units = gpd.GeoDataFrame(shapes_, geometry="geometry", crs=C.WORK_CRS)
    return land_from_units(units, grid)


# ---------------------------------------------------------------------------
# Layer 3: wildfire hazard (CPUC HFTD tiers + CAL FIRE FHSZ)
# ---------------------------------------------------------------------------
def fetch_fire(grid, refresh):
    fire = np.zeros((grid["nrows"], grid["ncols"]), np.uint8)
    sources = []
    for lid, level in C.HFTD_LAYERS.items():
        try:
            fc = cached_json(f"hftd_{lid}.geojson",
                             lambda: arcgis_query_geojson(f"{C.HFTD_URL}/{lid}", out_fields="FID"), refresh)
            gdf = to_work_gdf(fc)
            fire = np.maximum(fire, burn(gdf.geometry, grid, level))
            sources.append(f"CPUC HFTD layer {lid}")
        except Exception as ex:  # keep going with other sources
            print(f"    HFTD layer {lid} failed: {ex}")
    for lid in C.FHSZ_LAYERS:
        try:
            fc = cached_json(f"fhsz_{lid}.geojson",
                             lambda: arcgis_query_geojson(f"{C.FHSZ_URL}/{lid}", out_fields="HAZ_CODE"), refresh)
            gdf = to_work_gdf(fc)
            for code in (1, 2, 3):
                fire = np.maximum(fire, burn(gdf[gdf["HAZ_CODE"] == code].geometry, grid, code))
            sources.append(f"CAL FIRE FHSZ layer {lid}")
        except Exception as ex:
            print(f"    FHSZ layer {lid} failed: {ex}")
    if not sources:
        raise RuntimeError("all fire sources failed")
    return fire, sources


def synthetic_fire(grid, slope):
    z = smooth_noise(grid, 40) + 0.04 * gaussian_filter(slope, 5)
    return np.digitize(z, [-0.8, 0.0, 0.7]).astype(np.uint8)


# ---------------------------------------------------------------------------
# Layer 3b: continuous wildfire hazard (USFS WRC Wildfire Hazard Potential)
# ---------------------------------------------------------------------------
def fetch_fire_risk(grid, refresh):
    whp = export_image(C.WHP_URL, grid, "whp_wrc_2024.tif", refresh, interpolation="RSP_NearestNeighbor")
    whp[(whp < 0) | (whp >= 65535) | ~np.isfinite(whp)] = 0  # nodata -> non-burnable
    if whp.max() <= 0:
        raise RuntimeError("WHP export is empty")
    return (np.clip(whp / C.WHP_REF, 0, 1) ** C.WHP_EXPONENT).astype(np.float32)


# ---------------------------------------------------------------------------
# Layer 5: parcels (estimated from building density, or real parcels if provided)
# ---------------------------------------------------------------------------
def fetch_parcel_density(grid, refresh):
    """Parcels crossed per km of line, estimated from USFS WRC building counts."""
    bld = export_image(C.BUILDINGS_URL, grid, "buildings_wrc.tif", refresh, interpolation="RSP_NearestNeighbor")
    bld[(bld < 0) | (bld > 1e4) | ~np.isfinite(bld)] = 0
    if bld.sum() == 0:
        raise RuntimeError("building count export is empty")
    density = gaussian_filter(bld, C.PARCEL_DENSITY_SMOOTH_M / C.CELL_SIZE_M) / (C.CELL_SIZE_M ** 2 / 1e6)  # per km2
    area = np.clip(C.BUILDINGS_PER_PARCEL / np.maximum(density, 1e-9), C.PARCEL_MIN_KM2, C.PARCEL_MAX_KM2)
    print(f"    {int(bld.sum())} buildings in grid")
    return (1.0 / np.sqrt(area)).astype(np.float32)


def load_real_parcels(grid):
    """Optional: rasterize a user-supplied parcel file (one id per de-duplicated polygon)."""
    for stem in C.PARCEL_FILE_STEMS:
        path = C.RAW_DIR / stem
        if path.exists():
            gdf = gpd.read_file(path, bbox=gpd.GeoSeries([box(*C.BBOX)], crs="EPSG:4326"))
            gdf = gdf[gdf.geometry.notna()].to_crs(C.WORK_CRS)
            gdf = gdf[~gdf.geometry.to_wkb().duplicated()]  # SanGIS parcels are "stacked"
            ids = rasterize(((g, i + 1) for i, g in enumerate(gdf.geometry)), out_shape=(grid["nrows"], grid["ncols"]),
                            transform=grid["transform"], fill=0, dtype="int32")
            print(f"    real parcels: {len(gdf)} polygons from {path.name}")
            return ids, path.name
    return None, None


def risk_from_levels(fire):
    lut = np.array([C.FIRE_RISK_FROM_LEVEL[k] for k in range(4)], dtype=np.float32)
    return lut[fire]


def risk_overlay_png(risk, grid):
    """Colour-ramped hazard image on a lon/lat grid matching BBOX, for draping in Cesium."""
    from PIL import Image
    w, s, e, n = C.BBOX
    width, height = int(round((e - w) / 0.0003)), int(round((n - s) / 0.00027))
    dst = np.zeros((height, width), np.float32)
    reproject(risk, dst, src_transform=grid["transform"], src_crs=C.WORK_CRS,
              dst_transform=from_bounds(w, s, e, n, width, height), dst_crs="EPSG:4326",
              resampling=Resampling.bilinear)
    stops = np.array([0.0, 0.35, 0.65, 1.0])
    colors = np.array([[255, 230, 120], [255, 170, 50], [235, 70, 40], [140, 0, 30]], dtype=np.float32)
    rgb = np.stack([np.interp(dst, stops, colors[:, i]) for i in range(3)], axis=-1)
    alpha = np.where(dst < 0.08, 0, 50 + 160 * dst)
    rgba = np.dstack([rgb, alpha]).clip(0, 255).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(C.OVERLAY_DIR / "firerisk.png", optimize=True)


# ---------------------------------------------------------------------------
# Layer 4: existing corridors (OSM power lines + major roads)
# ---------------------------------------------------------------------------
def overpass_query():
    w, s, e, n = C.BBOX
    bb = f"{s},{w},{n},{e}"
    q = (f'[out:json][timeout:90];('
         f'way["power"~"^(line|minor_line)$"]({bb});'
         f'way["highway"~"^(trunk|primary|secondary|motorway)$"]({bb});'
         f');out geom tags;'
         f'nwr["power"="substation"]({bb});out center tags;')
    last = None
    for attempt in range(2):
        for url in C.OVERPASS_URLS:
            try:
                r = requests.post(url, data={"data": q}, headers=HEADERS, timeout=150)
                if r.ok:
                    return r.json()
                last = f"{url} HTTP {r.status_code}"
            except Exception as ex:
                last = f"{url} {ex}"
            print(f"    overpass attempt failed: {last}")
        time.sleep(3)
    raise RuntimeError(last)


def fetch_corridors(grid, refresh):
    osm = cached_json("osm.json", overpass_query, refresh)
    lines = {"power": [], "road": []}
    subs = []
    for el in osm["elements"]:
        tags = el.get("tags", {})
        if tags.get("power") == "substation":
            c = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
            if c.get("lat") is not None:
                subs.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [c["lon"], c["lat"]]},
                             "properties": {"name": tags.get("name") or "Substation (unnamed)",
                                            "voltage": tags.get("voltage")}})
            continue
        if el["type"] != "way" or "geometry" not in el:
            continue
        coords = [(p["lon"], p["lat"]) for p in el["geometry"]]
        if len(coords) < 2:
            continue
        kind = "power" if "power" in tags else "road"
        lines[kind].append({"type": "Feature", "geometry": mapping(LineString(coords)),
                            "properties": {"kind": kind, "name": tags.get("name") or tags.get("ref"),
                                           "voltage": tags.get("voltage"), "highway": tags.get("highway")}})
    if not lines["power"] and not lines["road"]:
        raise RuntimeError("OSM returned no lines")
    return lines, subs


def synthetic_corridor_lines(grid):
    """A few wiggly roads and one power line, in lon/lat (labelled SYNTHETIC)."""
    w, s, e, n = C.BBOX
    feats = {"power": [], "road": []}

    def walk(p0, p1, wiggle, k=40):
        t = np.linspace(0, 1, k)
        lon = p0[0] + (p1[0] - p0[0]) * t + np.cumsum(RNG.normal(0, wiggle, k)) * np.sin(np.pi * t)
        lat = p0[1] + (p1[1] - p0[1]) * t + np.cumsum(RNG.normal(0, wiggle, k)) * np.sin(np.pi * t)
        return LineString(zip(lon, lat))

    for p0, p1 in [((w, s + 0.3 * (n - s)), (e, s + 0.55 * (n - s))),
                   ((w + 0.4 * (e - w), s), (w + 0.6 * (e - w), n))]:
        feats["road"].append({"type": "Feature", "geometry": mapping(walk(p0, p1, 0.002)),
                              "properties": {"kind": "road", "name": "SYNTHETIC road"}})
    feats["power"].append({"type": "Feature", "geometry": mapping(walk((w, s + 0.2 * (n - s)), (e, n - 0.1 * (n - s)), 0.001)),
                           "properties": {"kind": "power", "name": "SYNTHETIC 69 kV line"}})
    subs = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [w + 0.08 * (e - w), s + 0.3 * (n - s)]},
             "properties": {"name": "SYNTHETIC Substation", "voltage": "69000"}}]
    return feats, subs


def fetch_cec_grid(refresh):
    """CEC transmission lines + substations as lon/lat features (operational assets only)."""
    lines_fc = cached_json("cec_lines.geojson", lambda: arcgis_query_geojson(
        C.CEC_LINES_URL, out_fields="Name,kV_Sort,Owner,Status,Circuit,Type"), refresh)
    lines = []
    for f in lines_fc["features"]:
        p = f.get("properties") or {}
        if f.get("geometry") and (p.get("Status") or "Operational") == "Operational":
            lines.append({"type": "Feature", "geometry": f["geometry"], "properties": {
                "kind": "power", "source": "CEC", "name": p.get("Name"), "kv": p.get("kV_Sort"),
                "owner": p.get("Owner"), "circuit": p.get("Circuit"), "line_type": p.get("Type")}})
    if not lines:
        raise RuntimeError("CEC returned no transmission lines")
    subs_fc = cached_json("cec_substations.geojson", lambda: arcgis_query_geojson(
        C.CEC_SUBSTATIONS_URL, out_fields="Substati_1,Status,Map_Owner," + ",".join(C.CEC_SUBSTATION_KV)), refresh)
    subs = []
    for f in subs_fc["features"]:
        p = f.get("properties") or {}
        if not f.get("geometry") or (p.get("Status") or "Operational") != "Operational":
            continue
        kvs = sorted(kv for flag, kv in C.CEC_SUBSTATION_KV.items() if str(p.get(flag, "")).strip().upper() == "YES")
        subs.append({"type": "Feature", "geometry": f["geometry"], "properties": {
            "name": f"{p.get('Substati_1', '').strip() or 'Substation'} Substation", "owner": p.get("Map_Owner"),
            "voltage_label": "/".join(map(str, kvs)) + " kV" if kvs else None, "source": "CEC"}})
    print(f"    CEC: {len(lines)} transmission lines, {len(subs)} substations")
    return lines, subs


def merge_grid(osm_lines, osm_subs, cec_lines, cec_subs):
    """Overlay lines = all CEC lines + the parts of OSM lines not already within 150 m of a CEC line.
    Substations = CEC + OSM ones more than 300 m from any CEC substation."""
    cec = to_work_gdf({"type": "FeatureCollection", "features": cec_lines})
    osm = to_work_gdf({"type": "FeatureCollection", "features": osm_lines})
    covered = unary_union(list(cec.geometry.buffer(150))) if len(cec) else None
    extra = []
    if len(osm):
        osm = osm.assign(geometry=osm.geometry.difference(covered) if covered is not None else osm.geometry)
        osm = osm[~osm.geometry.is_empty & (osm.geometry.length > 100)]
        osm["source"] = "OSM"
        extra = json.loads(osm.to_crs("EPSG:4326").to_json())["features"]
    cec_ll = json.loads(cec.to_crs("EPSG:4326").to_json())["features"] if len(cec) else []
    to_utm = Transformer.from_crs("EPSG:4326", C.WORK_CRS, always_xy=True)
    cec_pts = [to_utm.transform(*s["geometry"]["coordinates"]) for s in cec_subs]
    keep_osm = [s for s in osm_subs if all(np.hypot(*(np.subtract(to_utm.transform(*s["geometry"]["coordinates"]), q))) > 300
                                           for q in cec_pts)]
    for s in keep_osm:
        v = s["properties"].get("voltage")
        s["properties"].update(source="OSM", voltage_label=f"{round(int(v) / 1000)} kV" if v and str(v).isdigit() else None)
    return cec_ll + extra, cec_subs + keep_osm


def rasterize_corridors(lines, grid):
    out = np.zeros((grid["nrows"], grid["ncols"]), np.uint8)
    for kind, bit, buf in [("road", 1, C.ROAD_BUFFER_M), ("power", 2, C.POWERLINE_BUFFER_M)]:
        fc = {"type": "FeatureCollection", "features": lines[kind]}
        gdf = to_work_gdf(fc)
        if gdf.empty:
            continue
        out |= burn(gdf.geometry.buffer(buf), grid, bit, all_touched=True)
    return out


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
def write_tif(name, arr, grid):
    profile = dict(driver="GTiff", height=grid["nrows"], width=grid["ncols"], count=1,
                   dtype=arr.dtype, crs=C.WORK_CRS, transform=grid["transform"], compress="deflate")
    with rasterio.open(C.PROCESSED_DIR / f"{name}.tif", "w", **profile) as ds:
        ds.write(arr, 1)


def raster_overlay(arr, grid, levels, name, labels):
    """Polygonise level >= 1 cells of a small-int raster to a lon/lat GeoJSON overlay."""
    geoms, vals = [], []
    for geom, val in shapes(arr.astype(np.uint8), mask=arr > 0, transform=grid["transform"]):
        if int(val) in levels:
            geoms.append(shape(geom))
            vals.append(int(val))
    if not geoms:
        gdf = gpd.GeoDataFrame({"level": []}, geometry=[], crs=C.WORK_CRS)
    else:
        gdf = gpd.GeoDataFrame({"level": vals}, geometry=geoms, crs=C.WORK_CRS)
        gdf = gdf.dissolve(by="level", as_index=False)
        gdf["geometry"] = gdf.geometry.simplify(C.CELL_SIZE_M * 1.5).buffer(0)
        gdf = gdf.explode(index_parts=False)
        gdf = gdf[gdf.geometry.area > 4 * C.CELL_SIZE_M ** 2]
    gdf["label"] = gdf["level"].map(labels)
    gdf.to_crs("EPSG:4326").to_file(C.OVERLAY_DIR / f"{name}.geojson", driver="GeoJSON")


def write_fc(name, feats):
    (C.OVERLAY_DIR / f"{name}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="ignore cached downloads in data/raw")
    ap.add_argument("--synthetic-all", action="store_true", help="skip downloads, use synthetic data for every layer")
    args = ap.parse_args()

    for d in (C.RAW_DIR, C.PROCESSED_DIR, C.OVERLAY_DIR):
        d.mkdir(parents=True, exist_ok=True)

    grid = build_grid()
    print(f"Region: {C.REGION_NAME}  bbox={C.BBOX}")
    print(f"Grid: {grid['ncols']} x {grid['nrows']} cells @ {C.CELL_SIZE_M:.0f} m in {C.WORK_CRS}")
    synthetic, sources = [], {}

    def attempt(label, real_fn, fake_fn):
        if args.synthetic_all:
            synthetic.append(label)
            return fake_fn()
        try:
            t = time.time()
            out = real_fn()
            print(f"  [ok]  {label} ({time.time() - t:.1f}s)")
            return out
        except Exception as ex:
            print(f"  [!!]  {label} download failed -> SYNTHETIC placeholder ({ex})")
            synthetic.append(label)
            return fake_fn()

    region = region_mask(grid)

    print("Layer 1/4  elevation (USGS 3DEP)")
    dem = attempt("elevation", lambda: fetch_dem(grid, args.refresh), lambda: synthetic_dem(grid))
    sources["elevation"] = "SYNTHETIC" if "elevation" in synthetic else "USGS 3DEP ImageServer"
    slope = slope_deg(dem)

    print("Layer 2/4  land status: protected / federal / tribal (PAD-US 3.0)")
    land = attempt("land", lambda: fetch_land(grid, args.refresh), lambda: synthetic_land(grid))
    sources["land"] = "SYNTHETIC" if "land" in synthetic else "PAD-US 3.0 Fee_Manager"
    protected, owner = land["protected"], land["owner"]

    print("Layer 3/4  wildfire hazard (CPUC HFTD + CAL FIRE FHSZ)")
    fire_res = attempt("fire", lambda: fetch_fire(grid, args.refresh), lambda: (synthetic_fire(grid, slope), ["SYNTHETIC"]))
    fire, sources["fire"] = fire_res

    print("Layer 3b    continuous wildfire hazard (USFS WRC Wildfire Hazard Potential 2024)")
    if args.synthetic_all:
        fire_risk = risk_from_levels(fire)
        synthetic.append("fire_risk")
        sources["fire_risk"] = "SYNTHETIC (derived from synthetic fire levels)"
    else:
        try:
            t = time.time()
            fire_risk = fetch_fire_risk(grid, args.refresh)
            sources["fire_risk"] = "USFS WRC Wildfire Hazard Potential 2024 (30 m)"
            print(f"  [ok]  fire_risk ({time.time() - t:.1f}s)")
        except Exception as ex:
            # fallback: the discrete HFTD/FHSZ layer above (itself possibly synthetic)
            fire_risk = risk_from_levels(fire)
            fallback = "SYNTHETIC fire levels" if "fire" in synthetic else "HFTD/FHSZ levels"
            sources["fire_risk"] = f"FALLBACK: derived from {fallback}"
            print(f"  [!!]  fire_risk download failed -> derived from {fallback} ({ex})")
            if "fire" in synthetic:
                synthetic.append("fire_risk")

    print("Layer 4/4  existing corridors (OSM power lines + major roads)")
    lines, subs = attempt("corridors", lambda: fetch_corridors(grid, args.refresh), lambda: synthetic_corridor_lines(grid))
    sources["corridors"] = "SYNTHETIC" if "corridors" in synthetic else "OpenStreetMap via Overpass"
    osm_power = list(lines["power"])

    print("Layer 4b    existing grid (California Energy Commission transmission lines + substations)")
    if args.synthetic_all:
        grid_lines, subs_merged = osm_power, subs
    else:
        try:
            t = time.time()
            cec_lines, cec_subs = fetch_cec_grid(args.refresh)
            grid_lines, subs_merged = merge_grid(osm_power, subs, cec_lines, cec_subs)
            sources["grid"] = "CEC transmission lines + substations, OSM supplement"
            print(f"  [ok]  grid ({time.time() - t:.1f}s): {len(grid_lines)} line features, {len(subs_merged)} substations")
        except Exception as ex:
            grid_lines, subs_merged = osm_power, subs
            sources["grid"] = "OSM only (CEC download failed)"
            print(f"  [!!]  CEC grid download failed -> OSM lines only ({ex})")
    # corridor raster: every known line (CEC and raw OSM; overlaps don't matter in a raster)
    corridor = rasterize_corridors({"road": lines["road"], "power": osm_power + [f for f in grid_lines if f["properties"].get("source") == "CEC"]}, grid)

    print("Layer 5    parcels (estimated from USFS WRC building counts; real parcels if data/raw/parcels.* exists)")
    parcel_density = attempt("parcels", lambda: fetch_parcel_density(grid, args.refresh),
                             lambda: np.full((grid["nrows"], grid["ncols"]), C.SYNTHETIC_PARCELS_PER_KM, np.float32))
    parcel_ids, parcel_file = (None, None) if args.synthetic_all else load_real_parcels(grid)
    sources["parcels"] = (f"real parcels: {parcel_file}" if parcel_file else
                          "SYNTHETIC constant density" if "parcels" in synthetic else
                          "ESTIMATED from USFS WRC building count")

    # ---- write aligned grids
    write_tif("elevation", dem, grid)
    write_tif("slope", slope, grid)
    write_tif("protected", protected.astype(np.uint8), grid)
    write_tif("owner", owner.astype(np.uint8), grid)
    write_tif("landunit", land["unit"].astype(np.int32), grid)
    write_tif("fire", fire.astype(np.uint8), grid)
    write_tif("firerisk", fire_risk.astype(np.float32), grid)
    write_tif("corridor", corridor.astype(np.uint8), grid)
    write_tif("region", region, grid)
    write_tif("parceldens", parcel_density.astype(np.float32), grid)
    parcel_tif = C.PROCESSED_DIR / "parcelid.tif"
    if parcel_ids is not None:
        write_tif("parcelid", parcel_ids, grid)
    elif parcel_tif.exists():
        parcel_tif.unlink()  # stale real-parcel layer from an earlier run

    # ---- overlays for the frontend
    raster_overlay(fire, grid, {1, 2, 3}, "fire",
                   {1: "Moderate", 2: "High / HFTD Tier 2", 3: "Very High / HFTD Tier 3"})
    raster_overlay(protected, grid, {1, 2}, "protected",
                   {1: "Managed public land (GAP 3)", 2: "Protected (GAP 1-2 / park / wilderness)"})
    risk_overlay_png(fire_risk, grid)
    (C.OVERLAY_DIR / "tribal.geojson").write_text(json.dumps(land["tribal_fc"]))
    write_fc("powerlines", grid_lines)       # display: CEC + OSM-only extras
    write_fc("powerlines_osm", osm_power)    # raw OSM ways (validation reconstructs lines by OSM ref)
    write_fc("roads", lines["road"])
    write_fc("substations", subs_merged)

    meta = dict(
        region_name=C.REGION_NAME, bbox=list(C.BBOX), crs=C.WORK_CRS, cell_size_m=C.CELL_SIZE_M,
        shape=[grid["nrows"], grid["ncols"]], transform=list(grid["transform"])[:6],
        synthetic_layers=synthetic, sources=sources, land_units=land["units"],
        created_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    C.META_PATH.write_text(json.dumps(meta, indent=2))

    # ---- summary
    inside = region == 1
    print("\nSummary (inside bbox):")
    print(f"  elevation  {dem[inside].min():.0f} - {dem[inside].max():.0f} m, "
          f"slope median {np.median(slope[inside]):.1f} deg, p90 {np.percentile(slope[inside], 90):.1f} deg")
    print(f"  protected  strict {np.mean(protected[inside] == 2):.1%}, managed {np.mean(protected[inside] == 1):.1%}")
    print("  owner      " + ", ".join(f"{n} {np.mean(owner[inside] == k):.1%}" for k, n in C.OWNER_NAMES.items()))
    print("  fire       " + ", ".join(f"L{k} {np.mean(fire[inside] == k):.1%}" for k in range(4)))
    fr = fire_risk[inside]
    print(f"  fire risk  p10 {np.percentile(fr, 10):.2f}, median {np.median(fr):.2f}, p90 {np.percentile(fr, 90):.2f}, "
          f">= {C.HIGH_RISK_THRESHOLD} on {np.mean(fr >= C.HIGH_RISK_THRESHOLD):.1%}  ({sources['fire_risk']})")
    pdn = parcel_density[inside]
    print(f"  parcels    est. parcels/km p10 {np.percentile(pdn, 10):.1f}, median {np.median(pdn):.1f}, "
          f"p90 {np.percentile(pdn, 90):.1f}  ({sources['parcels']})")
    print(f"  corridors  road {np.mean(corridor[inside] & 1 > 0):.1%}, power {np.mean(corridor[inside] & 2 > 0):.1%}, "
          f"{len(osm_power)} OSM power ways + {len(grid_lines)} merged grid lines, {len(lines['road'])} road ways, "
          f"{len(subs_merged)} substations ({', '.join(x['properties']['name'] for x in subs_merged)})")
    if synthetic:
        print(f"\n*** SYNTHETIC PLACEHOLDER LAYERS: {', '.join(synthetic)} ***")
    else:
        print("\nAll layers loaded from real data sources.")
    print(f"Wrote grids + overlays to {C.PROCESSED_DIR}")


if __name__ == "__main__":
    main()
