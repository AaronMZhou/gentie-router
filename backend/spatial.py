"""
Spatial helpers shared by the two project stores.

MongoStore answers the spatial queries with 2dsphere indexes; the SQLite Store (and the
benchmark's baseline) answers the same questions by brute force with shapely in UTM metres.
"""
import json
from functools import lru_cache

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, Point, mapping, shape
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely.validation import make_valid

import config as C

_TO_UTM = Transformer.from_crs("EPSG:4326", C.WORK_CRS, always_xy=True)
_FROM_UTM = Transformer.from_crs(C.WORK_CRS, "EPSG:4326", always_xy=True)


def reproject(geom, transformer):
    return shapely.transform(geom, lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1])))


def route_line(result):
    """The route as one 2D GeoJSON LineString: segments chained, consecutive duplicate vertices dropped
    (2dsphere rejects them).  The FeatureCollection itself can't be indexed; this field is."""
    coords = []
    for f in result["features"]:
        if f["properties"].get("kind") == "segment":
            for c in f["geometry"]["coordinates"]:
                pt = [round(c[0], 7), round(c[1], 7)]
                if not coords or pt != coords[-1]:
                    coords.append(pt)
    return {"type": "LineString", "coordinates": coords}


def _polygonal(geom):
    """make_valid can return a GeometryCollection with stray lines/points; keep the area, rings CCW."""
    geom = make_valid(geom)
    if geom.geom_type == "GeometryCollection":
        geom = unary_union([g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")])
    if geom.geom_type == "Polygon":
        return orient(geom)
    return type(geom)([orient(p) for p in geom.geoms])


@lru_cache(maxsize=1)
def tribal_lands():
    """[(name, GeoJSON geometry)] from the tribal overlay, cleaned for 2dsphere (valid, CCW shells)."""
    fc = json.loads((C.OVERLAY_DIR / "tribal.geojson").read_text())
    return [(f["properties"]["name"], mapping(_polygonal(shape(f["geometry"])))) for f in fc["features"]]


@lru_cache(maxsize=1)
def substations():
    fc = json.loads((C.OVERLAY_DIR / "substations.geojson").read_text())
    return [{**f["properties"], "geometry": f["geometry"]} for f in fc["features"]]


def to_utm(geojson):
    return reproject(shape(geojson), _TO_UTM)


def circle(lon, lat, km):
    """km-radius circle (128-gon built in UTM) as a GeoJSON Polygon, e.g. for $geoIntersects."""
    ring = reproject(Point(lon, lat), _TO_UTM).buffer(km * 1000, quad_segs=32)
    return mapping(orient(reproject(ring, _FROM_UTM)))


def near(lines_utm, lon, lat, max_km):
    """lines_utm: {id: shapely line in UTM}.  -> [(id, distance_km)] within max_km, nearest first."""
    p = reproject(Point(lon, lat), _TO_UTM)
    hits = [(i, line.distance(p) / 1000) for i, line in lines_utm.items()]
    return sorted([(i, d) for i, d in hits if d <= max_km], key=lambda x: x[1])


@lru_cache(maxsize=1)
def tribal_lands_utm():
    return [(name, to_utm(g)) for name, g in tribal_lands()]


def crossings(lines_utm):
    """{id: shapely line in UTM} -> {id: [tribal land names the line intersects]} (only ids that cross)."""
    out = {}
    for name, poly in tribal_lands_utm():
        for i, line in lines_utm.items():
            if line.intersects(poly):
                out.setdefault(i, []).append(name)
    return out
