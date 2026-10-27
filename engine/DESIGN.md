# Mode-aware route search engine

A C++ search engine that chooses a gen-tie's path **and** where it runs overhead or underground in one
optimisation, under the rule that every overhead/underground run is at least 450 m long. Measured results are
in [RESULTS.md](RESULTS.md); this file explains the design.

## 1. The problem with the production pipeline

The production router ([backend/router.py](../backend/router.py)) does this:

1. **Search** a single grid whose cell cost is `min(overhead $/m, underground $/m) × penalty`. In effect it
   assumes the line can switch mode in every cell for free.
2. **Assign modes** after the fact: each cell takes its cheaper mode.
3. **Smooth**: repeatedly flip the shortest run under 450 m to the other mode.
4. **Charge** $750k per remaining overhead↔underground transition.

Steps 2–4 never feed back into step 1, so the path was chosen for a cost model the final route doesn't follow.
When the two modes are close in cost (the *Avoid fire* and *Prefer underground* strategies), the free-switching
path weaves between cells where each mode is marginally cheaper. Smoothing then forces long stretches into the
wrong mode, or leaves many $750k transitions. The result is a valid route, but often far from the cheapest one
that obeys the same rules.

## 2. Formulation: search over (cell, mode, run length)

Make the mode and the run-length rule part of the search state:

```
state  (cell, m, k)       m ∈ {overhead, underground}, k = length of the current run, in units, capped at K
move   (a, m, k) → (b, m, min(k + inc, K))    cost (w_m[a] + w_m[b]) / 2 × step length      (8 neighbours)
switch (a, m, K) → (a, 1−m, 0)               cost T = $750k, only once the current run is ≥ the minimum
start  (src, m, 0) for both m, cost 0
goal   (dst, m, K) for either m              the last run must be long enough too
```

`w_m` is mode *m*'s $/m multiplied by the routing penalty (protected ×1000, tribal ×10, outside the region
×10⁴), the same weighting production uses. The move cost is the trapezoid rule that scikit-image's
`MCP_Geometric` uses, so on one layer the engine reproduces scikit-image exactly (test 1).

**Why it's exact.** A route that obeys the rules is a sequence of runs, each in one mode and each at least 450 m
(when there's more than one), with a transition between consecutive runs. Map it onto states: walk each run's
cells with *k* counting up from 0, and switch only where the route switches, which is allowed because that run
has *k = K*. Its cost equals the path's cost in the state graph. Conversely, every start-to-goal path in the
state graph is a valid route, since switches and the goal both require *k = K*. So the cheapest path in the
state graph is the cheapest valid route.

**The switch-free exception.** A route that never switches is valid at any length. In this graph a single run
shorter than 450 m can't reach a goal state (it has *k < K*). So when the rule is on, the engine also runs a
second, switch-free search (*K* = 0, no switch edges) and keeps the cheaper result. Adding a "has switched yet"
flag to the state would also work, but it doubles the state space. Two searches cost less.

**Discretisation.** *k* counts 10 m units. A straight step (30 m) adds 3; a diagonal step (42.4 m) adds
`floor(42.4/10) = 4`, i.e. 40 m. Diagonals are under-counted, so when the engine says a run has reached
450 m (K = 45) it is at least 450 m long. The rule is never violated, and at worst a run must be ~6% longer
than strictly necessary. Finer units would be more precise but grow the state space linearly.

State-space size: 1,001 × 1,030 cells × 2 modes × 46 run buckets ≈ **95 million states**.

## 3. Search: Dijkstra → A* with lower bounds

Plain Dijkstra on the constrained graph expands ~20M states for a typical route (~12 s). A* expands states in
order of `g + h`, where *h* is a lower bound on the remaining cost. It stays optimal if *h* is **admissible**
(never overestimates) and **consistent** (`h(u) ≤ cost(u,v) + h(v)` for every edge). Consistency means the first
time a state is expanded, its cost is final, so the engine can use a closed set.

**The relaxation that gives every lower bound.** Delete the run-length rule and make switching free. Every
route then costs at least its free-switching cost, `Σ (wmin[a] + wmin[b]) / 2 × step` with `wmin = min(w_0, w_1)`.
That's exactly the grid production searches. So any lower bound on the free-switching distance is a lower bound
for the full problem.

1. **Octile:** the cheapest possible $/m in the region times the octile distance (the shortest 8-connected
   path length). Valid but weak, because the cheapest cell (an overhead line along an existing power-line
   corridor) is far cheaper than typical terrain.
2. **ALT (A\*, Landmarks, Triangle inequality):** precompute free-switching distance trees `d_L` from a few
   landmark cells. Because the free-switching graph is undirected, the triangle inequality gives
   `d(u, goal) ≥ |d_L(goal) − d_L(u)|` for every landmark L. The engine uses the max over landmarks and octile.
   Each bound is consistent, and the max of consistent bounds is consistent.
3. **Landmark at the goal.** Every gen-tie ends at a substation, and the engine puts a landmark on each one, so
   for those routes `|d_goal(goal) − d_goal(u)| = d_goal(u)`: the *exact* free-switching distance. That's the
   tightest bound this relaxation can give, and it's the same tree production already precomputes. The search
   only has to pay for the gap between free switching and the real rules, which is a few percent.

All bounds are multiplied by 0.99999, so float32 rounding can't push them above the true cost.

## 4. Data structures

| Piece | Choice | Why |
|---|---|---|
| State index | `cell × S + mode × (K+1) + k`, with S = 2(K+1) | a cell's states are adjacent in memory; neighbouring cells are close too |
| Cost per state | `float32` (4 bytes) | 95M states: float64 would need 760 MB just for costs. Relative error ~1e-7 per step, so a route's cost agrees with float64 to ~1e-5 (tests check this) |
| Parent per state | `uint16`: move direction 0–7 or 8 = switch (4 bits), previous run bucket (11 bits), closed flag (1 bit) | 2 bytes instead of an 8-byte parent pointer. The predecessor is recomputed from the direction and the stored bucket (the bucket can't be inverted once *k* saturates at K, which is why it's stored) |
| Reset between searches | a "touched" list of every state given a cost | A* touches a few thousand of 95M states; re-filling 570 MB per query would cost ~100 ms |
| Heuristic cache | one float per cell, NaN = not computed | ALT reads 8 landmark arrays per cell; neighbouring states share a cell |
| Priority queue | **radix heap** over the float's bit pattern | see below |

**Radix heap.** Dijkstra's popped keys never decrease, and neither do A*'s when the heuristic is consistent. A
radix heap exploits that: it keeps 33 buckets keyed by the highest bit in which a key differs from the last
popped key. Push is O(1). A pop that finds bucket 0 empty redistributes one bucket, and each element moves
down at most 32 times over its lifetime. Non-negative IEEE-754 floats order exactly like their uint32 bit
patterns, so the float costs go in directly. Float rounding in `g + h` can produce a key one ulp below the last
popped key, so `push` clamps to it. The binary heap (`std::priority_queue`) is kept for comparison.

**Memory.** 95M states × 6 bytes ≈ 570 MB, allocated once per engine on the first constrained search. The
dense layout is the trade-off for speed: a hash map of touched states would use far less memory for A* but
cost a hash lookup per relaxation.

**Interface.** A C ABI (`gt_create`, `gt_route`, `gt_tree`, `gt_set_landmarks`) loaded with Python `ctypes`
([__init__.py](__init__.py)). No exceptions cross the boundary; allocation failures return status 4. Built with
zig's bundled clang (`pip install ziglang`, `python -m engine.build`), so it needs no system compiler or admin
rights on Windows, macOS or Linux.

## 5. How it's verified ([test_engine.py](test_engine.py))

1. **Single-layer tree == scikit-image** `MCP_Geometric` on the full 1.03M-cell grid (max relative error ~1e-6, float32).
2. **Two-layer search == an independent SciPy implementation** ([reference.py](reference.py): explicit 2M-node
   sparse graph, SciPy's C Dijkstra). Tested on 40 pairs × 3 heuristics × 2 heaps.
3. **Constrained search == a pure-Python search over (cell, mode, run) states**, written separately, on 60 small
   random grids with random transition costs and run limits.
4. **On the real grid with the 450 m rule**, using the *Avoid fire* weights so routes actually switch: A* (octile,
   ALT) returns Dijkstra's cost; every run of every route that switches is ≥ 450 m measured along the actual
   steps; and the path re-priced in float64 matches the engine's float32 cost.

## 6. Limitations

- **Memory:** 570 MB per engine with the run rule. It doesn't fit the API's 8 route-worker processes on a
  16 GB machine, so the engine isn't wired into `/route` yet. Options: one shared engine process behind a
  queue, a search window, or hashed state storage.
- **Arbitrary endpoints:** without a landmark at the goal, ALT's bound is looser and the search expands more
  states (see RESULTS.md).
- **Run-length precision:** at most 6% conservative on diagonal-heavy runs (§2).
- **Geometry:** engine routes are grid paths. Production's string-pulling (straight spans that cost ≤1% more)
  isn't applied to them yet, so the comparisons in RESULTS.md price both on the grid.
