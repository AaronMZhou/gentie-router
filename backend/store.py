"""
SQLite project/route store (stdlib sqlite3, one connection per call, WAL).

Projects hold the endpoints; each route stores its full /route FeatureCollection as JSON.
Project dicts carry `routes`: a list of route summaries (no full GeoJSON).

open_store() picks this or backend.mongo_store.MongoStore (when MONGO_URL is set); both have the
same interface, including the spatial queries, which this store answers by brute force with shapely.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from backend import spatial

THUMB_POINTS = 40
SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    voltage_kv INTEGER NOT NULL,
    phase TEXT NOT NULL,
    start_lonlat TEXT,
    end_lonlat TEXT,
    filing_target TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    preset TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS routes_project ON routes(project_id);
"""
JSON_FIELDS = ("start_lonlat", "end_lonlat")
EST_KEYS = ("total_km", "total_cost_usd", "tower_count", "overhead_km", "underground_km", "n_transitions",
            "high_fire_km", "overhead_high_fire_km", "protected_km", "tribal_km", "estimates", "weather_maint")
PROJECT_FIELDS = ("name", "voltage_kv", "phase", "filing_target", *JSON_FIELDS)


class ProjectNotFound(LookupError):
    """add_route on a project that doesn't exist (or was deleted while the route was computing)."""


def open_store():
    if url := os.getenv("MONGO_URL"):
        from backend.mongo_store import MongoStore
        return MongoStore(url, os.getenv("MONGO_DB", "gentie"))
    import config as C
    return Store(os.getenv("GENTIE_DB", C.PROJECTS_DB))


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def route_summary(row):
    fc = json.loads(row["result_json"]) if isinstance(row["result_json"], str) else row["result_json"]
    pts = [c[:2] for f in fc["features"] if f["properties"].get("kind") == "segment" for c in f["geometry"]["coordinates"]]
    if len(pts) > THUMB_POINTS:  # even subsample, keeping both ends
        pts = [pts[round(i * (len(pts) - 1) / (THUMB_POINTS - 1))] for i in range(THUMB_POINTS)]
    p = fc["properties"]
    # Everything frontend/js/estimates.js estimate() reads, so the dashboard ranks routes like the project page.
    est = {k: p[k] for k in EST_KEYS if k in p}
    est["permitting_triggers"] = [{"code": t["code"], "reason": t["reason"]} for t in p["permitting_triggers"]]
    est["row"] = {"private_parcels": (p.get("row") or {}).get("private_parcels", 0)}
    est["inventory"] = {"conductor_km_est": (p.get("inventory") or {}).get("conductor_km_est", 0)}
    return {"id": row["id"], "name": row["name"], "preset": row["preset"], **est,
            "n_triggers": len(p["permitting_triggers"]), "thumb": pts}


class Store:
    kind = "sqlite"

    def __init__(self, path):
        self.path = str(path)
        with self._db() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)

    @contextmanager
    def _db(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            with con:  # commit on success, roll back on error
                yield con
        finally:
            con.close()

    # ---- projects
    def _project(self, con, row):
        p = dict(row)
        for k in JSON_FIELDS:
            p[k] = json.loads(p[k]) if p[k] else None
        # shortcut: summaries re-parse each route's full JSON per read; fine for tens of routes, store summary columns if it grows
        p["routes"] = [route_summary(r) for r in con.execute(
            "SELECT id, name, preset, result_json FROM routes WHERE project_id = ? ORDER BY id", (p["id"],))]
        return p

    def list_projects(self):
        with self._db() as con:
            return [self._project(con, r) for r in con.execute("SELECT * FROM projects ORDER BY id")]

    def get_project(self, id):
        with self._db() as con:
            row = con.execute("SELECT * FROM projects WHERE id = ?", (id,)).fetchone()
            return self._project(con, row) if row else None

    def create_project(self, name, voltage_kv, phase, filing_target=None):
        now = _now()
        with self._db() as con:
            id = con.execute("INSERT INTO projects (name, voltage_kv, phase, filing_target, created_at, updated_at) "
                             "VALUES (?, ?, ?, ?, ?, ?)", (name, voltage_kv, phase, filing_target, now, now)).lastrowid
        return self.get_project(id)

    def update_project(self, id, **fields):
        unknown = set(fields) - set(PROJECT_FIELDS)
        if unknown:
            raise ValueError(f"unknown project fields {unknown}")
        vals = {k: json.dumps(v) if k in JSON_FIELDS and v is not None else v for k, v in fields.items()}
        vals["updated_at"] = _now()
        with self._db() as con:
            cur = con.execute(f"UPDATE projects SET {', '.join(f'{k} = ?' for k in vals)} WHERE id = ?", (*vals.values(), id))
            if not cur.rowcount:
                return None
        return self.get_project(id)

    def delete_project(self, id):
        with self._db() as con:
            return con.execute("DELETE FROM projects WHERE id = ?", (id,)).rowcount > 0

    # ---- routes
    def add_route(self, project_id, name, preset, result):
        now = _now()
        try:
            with self._db() as con:
                id = con.execute("INSERT INTO routes (project_id, name, preset, result_json, created_at) VALUES (?, ?, ?, ?, ?)",
                                 (project_id, name, preset, json.dumps(result, separators=(",", ":")), now)).lastrowid
                con.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))
        except sqlite3.IntegrityError:
            raise ProjectNotFound(project_id) from None
        return route_summary({"id": id, "name": name, "preset": preset, "result_json": result})

    def get_route(self, id):
        with self._db() as con:
            row = con.execute("SELECT * FROM routes WHERE id = ?", (id,)).fetchone()
        if not row:
            return None
        r = dict(row)
        r["result"] = json.loads(r.pop("result_json"))
        return r

    def update_route(self, id, name):
        with self._db() as con:
            con.execute("UPDATE routes SET name = ? WHERE id = ?", (name, id))
            row = con.execute("SELECT id, project_id, name, preset, created_at FROM routes WHERE id = ?", (id,)).fetchone()
        return dict(row) if row else None

    def delete_route(self, id):
        with self._db() as con:
            return con.execute("DELETE FROM routes WHERE id = ?", (id,)).rowcount > 0

    def routes_without_weather(self):
        with self._db() as con:
            rows = con.execute("SELECT id, result_json FROM routes WHERE result_json NOT LIKE '%\"weather_maint\"%'").fetchall()
        return [(r["id"], json.loads(r["result_json"])) for r in rows]

    def set_route_weather(self, id, wm):
        # one statement, so a route re-computed meanwhile is not overwritten with a stale copy
        with self._db() as con:
            con.execute("UPDATE routes SET result_json = json_set(result_json, '$.properties.weather_maint', json(?)) WHERE id = ?",
                        (json.dumps(wm), id))

    def replace_route_result(self, id, result):
        with self._db() as con:
            con.execute("UPDATE routes SET result_json = ? WHERE id = ?", (json.dumps(result, separators=(",", ":")), id))

    # ---- spatial (brute force: parse every stored route, measure in UTM metres)
    def _lines(self):
        with self._db() as con:
            rows = con.execute("SELECT r.id, r.project_id, r.name, r.preset, r.result_json, p.name AS project_name "
                               "FROM routes r JOIN projects p ON p.id = r.project_id").fetchall()
        meta = {r["id"]: (r["project_id"], r["project_name"], route_summary(r)) for r in rows}
        lines = {r["id"]: spatial.to_utm(spatial.route_line(json.loads(r["result_json"]))) for r in rows}
        return meta, lines

    def routes_near(self, lon, lat, max_km, limit=50):
        meta, lines = self._lines()
        return [{**meta[i][2], "project_id": meta[i][0], "project_name": meta[i][1], "distance_km": round(d, 3)}
                for i, d in spatial.near(lines, lon, lat, max_km)[:limit]]

    def tribal_crossings(self):
        meta, lines = self._lines()
        return [{**meta[i][2], "project_id": meta[i][0], "project_name": meta[i][1], "tribal_lands": names}
                for i, names in sorted(spatial.crossings(lines).items())]
