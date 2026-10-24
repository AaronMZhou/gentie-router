"""
Route computation for worker processes.

Each process builds its own Router once (cost grids live in that process's
memory) and returns responses already serialized to JSON bytes, so the API's
event loop never does CPU-heavy work.  Functions are module-level so they can
be pickled by ProcessPoolExecutor (Windows uses spawn).
"""
import json
import os
import time

from backend.router import Router, RouteError

_router: Router | None = None


def init_worker():
    global _router
    _router = Router()


def warm(_=None):
    """Touch a worker so the pool spawns it (and loads grids) before traffic arrives."""
    time.sleep(0.2)  # hold the worker so concurrent warm() calls land on different processes
    return os.getpid()


def route_json(start, end, preset="balanced"):
    """-> (status_code, json_bytes)."""
    try:
        return 200, json.dumps(_router.route(start, end, preset), separators=(",", ":")).encode()
    except RouteError as ex:
        return 400, json.dumps({"detail": str(ex)}).encode()
