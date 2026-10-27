# Engine results

Measured on one Windows 11 laptop (Python 3.13, zig 0.16 / clang `-O3`), on the real 1,001 × 1,030 cell
(30 m) study grid. Raw numbers: [compare_results.json](compare_results.json), [bench_results.json](bench_results.json).
Design: [DESIGN.md](DESIGN.md).

## 1. Route quality: production pipeline vs exact optimum

`python -m engine.compare --n 300`: 300 random sites in the region, each routed to one of the three
substations, for each strategy preset. Both sides search the same preset weights, pay $750k per transition and
obey the 450 m minimum run. Production = free-switching search, then per-cell mode choice and smoothing
([DESIGN.md §1](DESIGN.md#1-the-problem-with-the-production-pipeline)). Engine = exact search over (cell, mode, run length).

**Like-for-like** compares the engine with production *before* string-pulling, so both are grid paths priced on
the same grid. String-pulling (production's straight spans, ≤1% costlier each) changes geometry, not mode
choice. "Real dollars" are construction cost on the balanced cost model, which is how production prices every
preset. "High-hazard km" is overhead line through USFS high wildfire-hazard cells.

| Preset | Production above the optimum, on the objective both minimise (mean over routes) | Real dollars, engine vs production (total / median route) | Transitions per route | Overhead km in high hazard (300 routes) |
|---|---|---|---|---|
| Prefer underground | +15.9% | **−10.0%** / −8.4% | 7.19 → **1.40** | 116 → 36 |
| Avoid fire | +21.1% | −3.6% / +1.2% | 7.08 → **3.70** | 86 → **6** |
| Balanced | +0.2% | −0.2% / 0.0% | 0.06 → 0.02 | 121 → 116 |

Against the *shipped* production routes (after string-pulling) the gaps are larger: +20.2% / +13.0% median
(underground), +24.6% / +18.4% (avoid fire), +3.1% / +2.7% (balanced) on the objective. The extra few points
there come from straightened geometry, not mode choice, so the like-for-like numbers are the ones to quote.

How to read it:
- **Where both modes compete** (the fire-aware presets), the old pipeline's routes are 16–21% worse than optimal
  on their own objective. Its path was chosen assuming free switching, and fixing the modes afterwards costs it
  ~5–6 extra transitions per route.
- **"Avoid fire" doesn't save money on a typical route** (median +1.2%). It spends the objective gain where the
  preset asks: overhead line in high-hazard areas falls from 86 km to 6 km across the 300 routes.
- **Balanced routes almost never switch mode**, so mode-aware search changes almost nothing there (0.2%).
- Every engine route obeys the run rule: the shortest run in all 900 routes is 456 m.
- Speed: on the fire-aware presets the engine takes 221–319 ms median per route (p95 2.6–3.2 s, ~0.4–0.5M states),
  vs ~53 ms for production's tree traceback. On balanced weights it takes ~1 ms, because the free-switching bound
  is nearly exact there.

## 2. Search speed

`python -m engine.bench_engine`, balanced weights, $750k transitions. Median over 12 pairs (4 for
constrained Dijkstra, ~25 s each). "To substation" has a landmark at the goal; "arbitrary" doesn't.

| Search | No run rule: to substation | No run rule: arbitrary | 450 m rule: to substation | 450 m rule: arbitrary |
|---|---:|---:|---:|---:|
| Dijkstra, binary heap | 315 ms / 568k states | 396 ms / 725k | 53.2 s / 42.5M | 47.0 s / 37.5M |
| Dijkstra, radix heap | 212 ms | 279 ms | 27.2 s | 24.4 s |
| A*, octile bound | 116 ms / 295k | 171 ms / 420k | 5.9 s / 10.4M | 10.2 s / 15.4M |
| **A*, ALT landmarks** | **0.5 ms / 596** | **12.5 ms / 27k** | **1.1 ms / 1,234** | **67 ms / 106k** |

- **ALT vs Dijkstra with the 450 m rule:** 27 s → 1.1 ms to a substation (42.5M → 1,234 states expanded);
  24 s → 67 ms between arbitrary cells. Most of this comes from the landmark at the goal, which turns the bound
  into the exact free-switching distance (the tree production already precomputes). Without it, the gain is 366×.
- **Radix vs binary heap:** 1.5–2× on the same expansions.

**On the fire-aware presets** (where mode-aware search matters; [bench_fire_presets.json](bench_fire_presets.json)), with
the 450 m rule, 5 routes to substations each, same engine, ALT cost == Dijkstra cost on every route:

| Preset | Dijkstra, median | A* + ALT, median | Speedup (median; per-route range) |
|---|---:|---:|---:|
| Avoid fire | 4.7 s / 25.8M states | 72 ms / 0.43M states | 66× (18–170×) |
| Prefer underground | 4.8 s / 28.1M states | 81 ms / 0.56M states | 60× (21–200×) |

The landmark bound is looser here than on balanced weights (the free-switching relaxation is ~16–21% below the
real optimum), so A* expands more states than the 1,234 above. These are also the cases where the engine is
**slower than the production pipeline** (~53 ms, which solves the easier free-switching problem): it trades
~4–6× more search time for routes that are 16–21% better on the preset's objective.

**Single-source tree** on the free-switching grid (1.03M cells), the same computation as scikit-image's
`MCP_Geometric.find_costs`, verified equal:

| | Median of 9 | Range |
|---|---:|---:|
| Engine, radix heap | 85 ms | 84–89 ms |
| Engine, binary heap | 139 ms | 137–143 ms |
| scikit-image MCP_Geometric | 890 ms | 207–920 ms |

scikit-image's first call in a process takes ~207 ms and later calls ~800–900 ms (the cause wasn't investigated),
so the conservative claim is **2.4× faster** than its best run.

## 3. Correctness

`python -m engine.test_engine`: all checks pass ([DESIGN.md §5](DESIGN.md#5-how-its-verified-test_enginepy)).
- Tree == scikit-image on 1.03M cells, max relative error 1.5e-6 (float32 vs float64).
- 240 two-layer searches == the SciPy reference.
- 480 constrained searches == the pure-Python label-setting search on random grids.
- 25 real-grid routes with the 450 m rule (22 switch mode): A* == Dijkstra, shortest run 450 m, float64 re-pricing matches.

## Caveats

- The routes are generated (random sites), not real projects.
- The engine isn't wired into the live `/route` API yet (memory: ~570 MB per engine; see DESIGN.md §6), so the
  app still ships the production pipeline's routes.
- One laptop, one run; absolute times will differ elsewhere.
