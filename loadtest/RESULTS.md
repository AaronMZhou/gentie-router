# /route load test

2026-10-24T17:06:40+00:00 · Intel64 Family 6 Model 183 Stepping 1, GenuineIntel (24 cores / 32 threads) · Python 3.13.12 · 32 concurrent users, 40s per run. Locust and the server share the machine; closed-loop users with zero think time.

| Configuration | req/s | p50 ms | p95 ms | p99 ms | failures | server RSS MB |
|---|---|---|---|---|---|---|
| before: sync endpoint — unique routes to substations | 7.4 | 4000 | 6900 | 8600 | 0/291 | 169 |
| before: sync endpoint — unique arbitrary routes | 8.5 | 3600 | 5700 | 6700 | 0/333 | 167 |
| async + 1 workers — unique routes to substations | 100.7 | 300 | 450 | 550 | 0/3948 | 280 |
| async + 4 workers — unique routes to substations | 388.4 | 80 | 110 | 130 | 0/15169 | 840 |
| async + 8 workers — unique routes to substations | 646.2 | 48 | 63 | 100 | 0/25227 | 1585 |
| async + 16 workers — unique routes to substations | 589.4 | 41 | 170 | 240 | 0/23044 | 3063 |
| async + 8 workers — unique arbitrary routes | 80.7 | 370 | 540 | 1300 | 0/3161 | 1768 |
| async + 16 workers — unique arbitrary routes | 80.4 | 320 | 1000 | 1400 | 0/3153 | 3086 |
| async + 8 workers + cache — 80% repeated routes | 2746.2 | 7 | 35 | 43 | 0/107240 | 1601 |

"before" is the synchronous endpoint at commit 3166cb6 (single process, full-grid search per request). The async rows add the process pool *and* the routing speedups (precomputed substation trees, windowed search), so the arbitrary-endpoint rows, which can't use the trees, are the cleaner measure of the concurrency gain.
