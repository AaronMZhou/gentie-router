"""
MongoDB project/route store: same interface as backend.store.Store, used when MONGO_URL is set.

    docker compose up -d mongo
    MONGO_URL=mongodb://localhost:27017 python -m uvicorn backend.app:app --port 8000

Collections
  projects      {_id: int, name, voltage_kv, phase, start_lonlat, end_lonlat, filing_target, created_at, updated_at}
  routes        {_id: int, project_id, name, preset, created_at,
                 result:   the full /route FeatureCollection, stored as a document (not a JSON string),
                 geometry: the route as one 2D GeoJSON LineString  -- 2dsphere index,
                 summary:  route_summary() (estimate fields + thumbnail), computed once at write time}
  tribal_lands  {_id: name, geometry}         -- 2dsphere, loaded from data/processed/overlays/tribal.geojson
  substations   {_id: name, ..., geometry}    -- 2dsphere, from overlays/substations.geojson
  counters      {_id: collection, seq}        -- integer ids, which the API and frontend use

A FeatureCollection isn't a geometry 2dsphere can index, hence the separate `geometry` field.
Single-document updates are atomic, so set_route_weather can't clobber a concurrent recompute.
A standalone mongod has no multi-document transactions: add_route checks the project, inserts,
and re-checks, deleting its route if the project vanished in between.
"""
from pymongo import ASCENDING, GEOSPHERE, MongoClient, ReturnDocument

from backend import spatial
from backend.store import PROJECT_FIELDS, ProjectNotFound, _now, route_summary

ROUTE_LIST_FIELDS = {"name": 1, "preset": 1, "project_id": 1, "summary": 1}


def _summary(id, name, preset, result):
    s = route_summary({"id": id, "name": name, "preset": preset, "result_json": result})
    return {k: v for k, v in s.items() if k not in ("id", "name", "preset")}


def _route_out(doc):
    """routes doc -> the same summary dict the SQLite store returns."""
    return {"id": doc["_id"], "name": doc["name"], "preset": doc["preset"], **doc["summary"]}


class MongoStore:
    kind = "mongodb"

    def __init__(self, url, db_name="gentie", client=None):
        self.client = client or MongoClient(url, serverSelectionTimeoutMS=5000, tz_aware=False)
        self.db = self.client[db_name]
        self.db.command("ping")  # fail at startup, not on the first request
        self.db.routes.create_index([("project_id", ASCENDING)])
        self.db.routes.create_index([("geometry", GEOSPHERE)])
        self.db.tribal_lands.create_index([("geometry", GEOSPHERE)])
        self.db.substations.create_index([("geometry", GEOSPHERE)])
        # reference layers are small and derived from the overlays: refresh them on every start
        for name, geom in spatial.tribal_lands():
            self.db.tribal_lands.replace_one({"_id": name}, {"geometry": geom}, upsert=True)
        for s in spatial.substations():
            self.db.substations.replace_one({"_id": s["name"]}, {k: v for k, v in s.items() if k != "name"}, upsert=True)

    def _next_id(self, coll):
        return self.db.counters.find_one_and_update({"_id": coll}, {"$inc": {"seq": 1}}, upsert=True,
                                                    return_document=ReturnDocument.AFTER)["seq"]

    # ---- projects
    def _project_out(self, doc, routes):
        p = {"id": doc["_id"], **{k: doc.get(k) for k in PROJECT_FIELDS}, "created_at": doc["created_at"],
             "updated_at": doc["updated_at"]}
        p["routes"] = [_route_out(r) for r in routes]
        return p

    def list_projects(self):
        projects = list(self.db.projects.find().sort("_id", ASCENDING))
        # one query for every project's route summaries (the SQLite store re-parses each route's JSON per read)
        by_project = {}
        for r in self.db.routes.find({"project_id": {"$in": [p["_id"] for p in projects]}}, ROUTE_LIST_FIELDS).sort("_id", ASCENDING):
            by_project.setdefault(r["project_id"], []).append(r)
        return [self._project_out(p, by_project.get(p["_id"], [])) for p in projects]

    def get_project(self, id):
        doc = self.db.projects.find_one({"_id": id})
        if doc is None:
            return None
        return self._project_out(doc, self.db.routes.find({"project_id": id}, ROUTE_LIST_FIELDS).sort("_id", ASCENDING))

    def create_project(self, name, voltage_kv, phase, filing_target=None):
        now = _now()
        id = self._next_id("projects")
        self.db.projects.insert_one({"_id": id, "name": name, "voltage_kv": voltage_kv, "phase": phase,
                                     "start_lonlat": None, "end_lonlat": None, "filing_target": filing_target,
                                     "created_at": now, "updated_at": now})
        return self.get_project(id)

    def update_project(self, id, **fields):
        unknown = set(fields) - set(PROJECT_FIELDS)
        if unknown:
            raise ValueError(f"unknown project fields {unknown}")
        if not self.db.projects.update_one({"_id": id}, {"$set": {**fields, "updated_at": _now()}}).matched_count:
            return None
        return self.get_project(id)

    def delete_project(self, id):
        if not self.db.projects.delete_one({"_id": id}).deleted_count:
            return False
        self.db.routes.delete_many({"project_id": id})
        return True

    # ---- routes
    def add_route(self, project_id, name, preset, result):
        if not self.db.projects.count_documents({"_id": project_id}, limit=1):
            raise ProjectNotFound(project_id)
        id, now = self._next_id("routes"), _now()
        self.db.routes.insert_one({"_id": id, "project_id": project_id, "name": name, "preset": preset, "created_at": now,
                                   "result": result, "geometry": spatial.route_line(result),
                                   "summary": _summary(id, name, preset, result)})
        if not self.db.projects.update_one({"_id": project_id}, {"$set": {"updated_at": now}}).matched_count:
            self.db.routes.delete_one({"_id": id})  # project deleted while we inserted
            raise ProjectNotFound(project_id)
        return {"id": id, "name": name, "preset": preset, **_summary(id, name, preset, result)}

    def get_route(self, id):
        doc = self.db.routes.find_one({"_id": id}, {"geometry": 0, "summary": 0})
        if doc is None:
            return None
        return {"id": doc.pop("_id"), **doc}

    def update_route(self, id, name):
        doc = self.db.routes.find_one_and_update({"_id": id}, {"$set": {"name": name}},
                                                 {"project_id": 1, "name": 1, "preset": 1, "created_at": 1},
                                                 return_document=ReturnDocument.AFTER)
        return {"id": doc.pop("_id"), **doc} if doc else None

    def delete_route(self, id):
        return self.db.routes.delete_one({"_id": id}).deleted_count > 0

    def routes_without_weather(self):
        return [(d["_id"], d["result"]) for d in
                self.db.routes.find({"result.properties.weather_maint": {"$exists": False}}, {"result": 1})]

    def set_route_weather(self, id, wm):
        self.db.routes.update_one({"_id": id}, {"$set": {"result.properties.weather_maint": wm, "summary.weather_maint": wm}})

    def replace_route_result(self, id, result):
        doc = self.db.routes.find_one({"_id": id}, {"name": 1, "preset": 1})
        if doc:
            self.db.routes.update_one({"_id": id}, {"$set": {"result": result, "geometry": spatial.route_line(result),
                                                             "summary": _summary(id, doc["name"], doc["preset"], result)}})

    # ---- spatial (2dsphere indexes)
    def _with_project_names(self, pipeline):
        return pipeline + [
            {"$lookup": {"from": "projects", "localField": "project_id", "foreignField": "_id", "as": "project",
                         "pipeline": [{"$project": {"name": 1}}]}},
            {"$unwind": "$project"},
        ]

    def routes_near(self, lon, lat, max_km, limit=50):
        """Saved routes passing within max_km of a point, nearest first ($geoNear on routes.geometry)."""
        docs = self.db.routes.aggregate(self._with_project_names([
            {"$geoNear": {"near": {"type": "Point", "coordinates": [lon, lat]}, "key": "geometry",
                          "distanceField": "distance_m", "maxDistance": max_km * 1000, "spherical": True}},
            {"$limit": limit},
            {"$project": {**ROUTE_LIST_FIELDS, "distance_m": 1}},
        ]))
        return [{**_route_out(d), "project_id": d["project_id"], "project_name": d["project"]["name"],
                 "distance_km": round(d["distance_m"] / 1000, 3)} for d in docs]

    def tribal_crossings(self):
        """Saved routes whose line intersects tribal land, with the lands crossed ($geoIntersects per land)."""
        lands = {}
        for land in self.db.tribal_lands.find():
            for r in self.db.routes.find({"geometry": {"$geoIntersects": {"$geometry": land["geometry"]}}}, {"_id": 1}):
                lands.setdefault(r["_id"], []).append(land["_id"])
        docs = self.db.routes.aggregate(self._with_project_names([
            {"$match": {"_id": {"$in": list(lands)}}}, {"$sort": {"_id": 1}}, {"$project": ROUTE_LIST_FIELDS}]))
        return [{**_route_out(d), "project_id": d["project_id"], "project_name": d["project"]["name"],
                 "tribal_lands": lands[d["_id"]]} for d in docs]
