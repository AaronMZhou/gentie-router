"""
SQLite project/route store (stdlib sqlite3, one connection per call, WAL).

Projects hold the endpoints; each route stores its full /route FeatureCollection as JSON.
Project dicts carry `routes`: a list of route summaries (no full GeoJSON).
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

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
            "high_fire_km", "overhead_high_fire_km", "protected_km", "tribal_km", "estimates")
PROJECT_FIELDS = ("name", "voltage_kv", "phase", "filing_target", *JSON_FIELDS)


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
        with self._db() as con:
            id = con.execute("INSERT INTO routes (project_id, name, preset, result_json, created_at) VALUES (?, ?, ?, ?, ?)",
                             (project_id, name, preset, json.dumps(result, separators=(",", ":")), now)).lastrowid
            con.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))
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

    def replace_route_result(self, id, result):
        with self._db() as con:
            con.execute("UPDATE routes SET result_json = ? WHERE id = ?", (json.dumps(result, separators=(",", ":")), id))
