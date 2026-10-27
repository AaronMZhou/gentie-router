"""
Reference implementation of the mode-aware search, built on scipy's Dijkstra (C code, explicit graph).

The production router searches one grid whose cell cost is min(overhead, underground) - switching
mode is free during the search - then picks each cell's cheaper mode, flips runs shorter than
MIN_SEGMENT_M, and charges TRANSITION_COST_USD per switch afterwards.  Here the mode is part of the
search state instead:

    node (cell, mode)           mode 0 = overhead, 1 = underground
    move  (a, m) -> (b, m)      (w_m[a] + w_m[b]) / 2 * step_m     8-connected, like MCP_Geometric
    switch (a, m) -> (a, 1-m)   TRANSITION_COST_USD

w_m = mode-m $/m x the routing penalty (protected / tribal / outside region), the same weighting the
production search uses.  The graph is undirected, so one search rooted at a substation (both modes
as sources) answers every route ending there.  It has no minimum run length - transitions are only
discouraged by their cost; engine/ (C++) adds the run-length constraint.

This explicit graph has 2 x 1M nodes and ~18M edges (~0.4 GB), fine for a reference, too big to
grow another dimension - which is why the run-length search lives in the C++ engine.
"""
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

import config as C

OFFSETS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def layer_weights(g):
    """[overhead, underground] $/m x routing penalty (routing / dollar is exactly the penalty)."""
    penalty = g.routing.astype(np.float64) / g.dollar.astype(np.float64)
    return [g.overhead.astype(np.float64) * penalty, g.underground.astype(np.float64) * penalty]


def build_graph(weights, transition_cost, cs=C.CELL_SIZE_M):
    R, Cn = weights[0].shape
    N = R * Cn
    idx = np.arange(N).reshape(R, Cn)
    src, dst, w = [], [], []
    for m, wm in enumerate(weights):
        for dr, dc in OFFSETS:
            a = idx[max(0, -dr):R - max(0, dr), max(0, -dc):Cn - max(0, dc)]
            b = idx[max(0, dr):R - max(0, -dr), max(0, dc):Cn - max(0, -dc)]
            wa = wm.ravel()[a.ravel()]
            wb = wm.ravel()[b.ravel()]
            src.append(a.ravel() + m * N)
            dst.append(b.ravel() + m * N)
            w.append((wa + wb) / 2 * cs * np.hypot(dr, dc))
    for m in (0, 1):
        src.append(np.arange(N) + m * N)
        dst.append(np.arange(N) + (1 - m) * N)
        w.append(np.full(N, float(transition_cost)))
    src, dst, w = np.concatenate(src), np.concatenate(dst), np.concatenate(w)
    return csr_matrix((w, (src, dst)), shape=(2 * N, 2 * N))


class TwoLayerTree:
    """Exact two-layer least-cost tree rooted at one cell (either mode)."""

    def __init__(self, graph, shape, root_rc):
        self.shape = shape
        N = shape[0] * shape[1]
        root = root_rc[0] * shape[1] + root_rc[1]
        self.dist, self.pred, _ = dijkstra(graph, directed=True, indices=[root, root + N], min_only=True,
                                           return_predecessors=True)
        self.N = N

    def cost(self, rc):
        i = rc[0] * self.shape[1] + rc[1]
        return min(self.dist[i], self.dist[i + self.N])

    def path(self, rc):
        """-> (cells [(r, c)], modes) from rc to the root.  A switch edge stays on one cell; the cell keeps
        the mode it was reached in, so step a->b is in b's mode and a switch shows as modes[a] != modes[b],
        which is how evaluate() prices it."""
        i = rc[0] * self.shape[1] + rc[1]
        node = i if self.dist[i] <= self.dist[i + self.N] else i + self.N
        cells, modes = [], []
        while node >= 0:
            cell, mode = node % self.N, node // self.N
            if not cells or cells[-1] != cell:
                cells.append(cell)
                modes.append(mode)
            node = self.pred[node]
        cells = np.array(cells)
        return np.c_[cells // self.shape[1], cells % self.shape[1]], np.array(modes, dtype=np.int8)


def evaluate(path, modes, weights, transition_cost, cs=C.CELL_SIZE_M):
    """Objective of a (path, per-cell mode) pair under the two-layer model: a step a->b is priced in b's
    mode on both halves, plus a switch when a's mode differs.  -> (total, n_transitions)."""
    r, c = path[:, 0], path[:, 1]
    d = np.hypot(*np.diff(path, axis=0).T) * cs
    m_next = modes[1:]
    w_a = np.where(m_next == 1, weights[1][r[:-1], c[:-1]], weights[0][r[:-1], c[:-1]])
    w_b = np.where(m_next == 1, weights[1][r[1:], c[1:]], weights[0][r[1:], c[1:]])
    n_trans = int((np.diff(modes) != 0).sum())
    return float(((w_a + w_b) / 2 * d).sum() + n_trans * transition_cost), n_trans
