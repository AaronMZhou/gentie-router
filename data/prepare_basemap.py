"""
Basemap context for the map: country/state borders and city labels.

    frontend/basemap/borders.geojson   LineStrings, properties.kind = "country" | "state"
    frontend/basemap/cities.geojson    Points, properties: name, population, tier = "metro" | "city" | "town"

Sources: Natural Earth 10m (public domain) for borders and metros, OpenStreetMap
place=city/town via Overpass for the cities and towns around the study region
(falls back to Natural Earth only if every Overpass mirror fails).  Written to
frontend/ so the map shows them even when only the static files are served.

Usage:  python data/prepare_basemap.py [--refresh]
"""
import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import box, mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

HEADERS = {"User-Agent": "gentie-router-hackathon/0.1"}
RAW = C.RAW_DIR / "basemap"
OUT = C.FRONTEND_DIR / "basemap"
SIMPLIFY_DEG = 0.003  # ~300 m; borders are context, not survey lines


def natural_earth(name, refresh):
    path = RAW / f"{name}.zip"
    if refresh or not path.exists():
        r = requests.get(C.NATURAL_EARTH_URL.format(name=name), headers=HEADERS, timeout=180)
        r.raise_for_status()
        zipfile.ZipFile(io.BytesIO(r.content))  # fail early on a truncated download
        path.write_bytes(r.content)
    return gpd.read_file(path)


def osm_places(refresh):
    path = RAW / "osm_places.json"
    if refresh or not path.exists():
        w, s, e, n = C.CITY_EXTENT
        q = f'[out:json][timeout:90];node["place"~"^(city|town)$"]({s},{w},{n},{e});out body;'
        for url in C.OVERPASS_URLS:
            try:
                r = requests.post(url, data={"data": q}, headers=HEADERS, timeout=150)
                r.raise_for_status()
                path.write_text(r.text)
                break
            except (requests.RequestException, ValueError) as ex:
                print(f"  overpass {url} failed: {ex}")
        else:
            return None
    return json.loads(path.read_text())["elements"]


def population(tag):
    digits = "".join(ch for ch in str(tag or "").split(";")[0] if ch.isdigit())
    return int(digits) if digits else 0


def borders(refresh):
    clip = box(*C.BASEMAP_EXTENT)
    feats = []
    for name, kind in [("ne_10m_admin_0_boundary_lines_land", "country"),
                       ("ne_10m_admin_1_states_provinces_lines", "state")]:
        gdf = natural_earth(name, refresh)
        gdf = gdf[gdf.intersects(clip)]
        for _, row in gdf.iterrows():
            geom = row.geometry.intersection(clip).simplify(SIMPLIFY_DEG)
            if geom.is_empty or geom.length < 0.01:  # drop slivers (< ~1 km)
                continue
            label = row.get("NAME") if isinstance(row.get("NAME"), str) else None
            feats.append({"type": "Feature", "properties": {"kind": kind, "name": label}, "geometry": mapping(geom)})
    return feats


def cities(refresh):
    out = {}
    ne = natural_earth("ne_10m_populated_places_simple", refresh)
    ne = ne[ne.intersects(box(*C.METRO_EXTENT)) & (ne.pop_max >= C.METRO_MIN_POP)
            & (ne.scalerank <= C.METRO_MAX_SCALERANK)]
    for _, row in ne.iterrows():
        out[row["name"]] = {"name": row["name"], "population": int(row.pop_max), "tier": "metro",
                            "lonlat": [round(row.geometry.x, 5), round(row.geometry.y, 5)]}

    elements = osm_places(refresh)
    if elements is None:
        print("  *** Overpass unavailable: cities.geojson has Natural Earth metros only ***")
        elements = []
    w, s, e, n = C.BBOX
    m = C.TOWN_MARGIN_DEG
    for el in elements:
        tags, name = el.get("tags", {}), el.get("tags", {}).get("name")
        if not name or name in out:  # Natural Earth metros win (San Diego, Tijuana)
            continue
        pop = population(tags.get("population"))
        near = w - m <= el["lon"] <= e + m and s - m <= el["lat"] <= n + m
        if tags.get("place") == "city" and pop >= C.CITY_MIN_POP:
            tier = "city"
        elif near:
            tier = "town"
        else:
            continue
        out[name] = {"name": name, "population": pop, "tier": tier, "lonlat": [round(el["lon"], 5), round(el["lat"], 5)]}

    return [{"type": "Feature", "properties": {k: v for k, v in c.items() if k != "lonlat"},
             "geometry": {"type": "Point", "coordinates": c["lonlat"]}}
            for c in sorted(out.values(), key=lambda c: -c["population"])]


def write(name, feats):
    (OUT / f"{name}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="ignore cached downloads in data/raw/basemap")
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    b = borders(args.refresh)
    write("borders", b)
    print(f"borders: {sum(f['properties']['kind'] == 'country' for f in b)} country, "
          f"{sum(f['properties']['kind'] == 'state' for f in b)} state lines")
    c = cities(args.refresh)
    write("cities", c)
    for tier in ("metro", "city", "town"):
        names = [f["properties"]["name"] for f in c if f["properties"]["tier"] == tier]
        print(f"{tier:5s} {len(names):3d}: {', '.join(names)}")


if __name__ == "__main__":
    main()
