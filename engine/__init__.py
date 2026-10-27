"""
Python side of the C++ mode-aware search (engine/gentie_search.cpp), via ctypes.

    from engine import Engine
    eng = Engine(w_overhead, w_underground, cell_m=30)          # float32 $/m grids, routing penalties included
    eng.set_landmarks([(r, c), ...])                            # optional: ALT lower bounds
    r = eng.route((r0, c0), (r1, c1), transition_cost=750_000, min_run_m=450)
    r.cost, r.path (n x 2 cells), r.modes (0 overhead / 1 underground), r.expanded

The library is built on first use if missing or older than the source (python -m engine.build).
"""
import ctypes
from dataclasses import dataclass

import numpy as np

HEURISTICS = {"dijkstra": 0, "octile": 1, "alt": 2}
HEAPS = {"radix": 0, "binary": 1}
STATUS = {1: "no path", 2: "path buffer too small", 3: "bad arguments", 4: "out of memory"}


class _Query(ctypes.Structure):
    _fields_ = [("src_r", ctypes.c_int32), ("src_c", ctypes.c_int32), ("dst_r", ctypes.c_int32), ("dst_c", ctypes.c_int32),
                ("transition_cost", ctypes.c_double), ("min_run_m", ctypes.c_double), ("unit_m", ctypes.c_double),
                ("heuristic", ctypes.c_int32), ("heap", ctypes.c_int32)]


class _Result(ctypes.Structure):
    _fields_ = [("cost", ctypes.c_double), ("path_len", ctypes.c_int32), ("status", ctypes.c_int32),
                ("expanded", ctypes.c_int64), ("pushed", ctypes.c_int64)]


def _load():
    from engine import build as _build
    if _build.stale():
        _build.build(verbose=False)
    lib = ctypes.CDLL(str(_build.LIB))
    f32p = np.ctypeslib.ndpointer(np.float32, flags="C_CONTIGUOUS")
    lib.gt_create.restype = ctypes.c_void_p
    lib.gt_create.argtypes = [ctypes.c_int32, ctypes.c_int32, f32p, ctypes.c_void_p, ctypes.c_double]
    lib.gt_destroy.argtypes = [ctypes.c_void_p]
    lib.gt_tree.restype = ctypes.c_int32
    lib.gt_tree.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, f32p,
                            ctypes.POINTER(ctypes.c_int64)]
    lib.gt_set_landmarks.restype = ctypes.c_int32
    lib.gt_set_landmarks.argtypes = [ctypes.c_void_p, ctypes.c_int32, f32p]
    lib.gt_route.restype = ctypes.c_int32
    lib.gt_route.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Query), ctypes.POINTER(_Result),
                             np.ctypeslib.ndpointer(np.int32, flags="C_CONTIGUOUS"),
                             np.ctypeslib.ndpointer(np.int8, flags="C_CONTIGUOUS"), ctypes.c_int32]
    return lib


_lib = None


def lib():
    """The loaded library, built first if needed (lazy, so engine.reference etc. don't need a compiler)."""
    global _lib
    if _lib is None:
        _lib = _load()
    return _lib


class EngineError(RuntimeError):
    pass


@dataclass
class Route:
    cost: float
    path: np.ndarray    # (n, 2) row, col
    modes: np.ndarray   # (n,) int8, the mode each cell was reached in
    expanded: int
    pushed: int


class Engine:
    def __init__(self, w_overhead, w_underground=None, cell_m=30.0):
        self._w0 = np.ascontiguousarray(w_overhead, dtype=np.float32)
        self._w1 = None if w_underground is None else np.ascontiguousarray(w_underground, dtype=np.float32)
        self.shape = self._w0.shape
        self._h = lib().gt_create(self.shape[0], self.shape[1], self._w0,
                                 None if self._w1 is None else self._w1.ctypes.data, cell_m)
        if not self._h:
            raise EngineError("out of memory creating the engine")
        self._buf = 1 << 16

    def __del__(self):
        if getattr(self, "_h", None):
            _lib.gt_destroy(self._h)
            self._h = None

    def tree(self, root, heap="radix"):
        """Free-switching single-source costs (N floats, $; same quantity as MCP_Geometric x cell size)."""
        out = np.empty(self.shape, dtype=np.float32)
        n = ctypes.c_int64()
        if st := lib().gt_tree(self._h, root[0], root[1], HEAPS[heap], out, ctypes.byref(n)):
            raise EngineError(STATUS.get(st, st))
        return out

    def set_landmarks(self, cells):
        d = np.ascontiguousarray(np.stack([self.tree(c) for c in cells]), dtype=np.float32) if cells else \
            np.zeros((0,) + self.shape, np.float32)
        if st := lib().gt_set_landmarks(self._h, len(cells), d):
            raise EngineError(STATUS.get(st, st))

    def route(self, src, dst, transition_cost=0.0, min_run_m=0.0, unit_m=10.0, heuristic="alt", heap="radix"):
        q = _Query(int(src[0]), int(src[1]), int(dst[0]), int(dst[1]), float(transition_cost), float(min_run_m),
                   float(unit_m), HEURISTICS[heuristic], HEAPS[heap])
        res = _Result()
        while True:
            cells = np.empty(self._buf, np.int32)
            modes = np.empty(self._buf, np.int8)
            st = lib().gt_route(self._h, ctypes.byref(q), ctypes.byref(res), cells, modes, self._buf)
            if st != 2:
                break
            self._buf = max(self._buf * 2, res.path_len)
        if st:
            raise EngineError(STATUS.get(st, st))
        n = res.path_len
        c = cells[:n]
        return Route(res.cost, np.c_[c // self.shape[1], c % self.shape[1]], modes[:n].copy(), res.expanded, res.pushed)
