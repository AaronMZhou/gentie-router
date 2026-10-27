# Store benchmark: MongoDB (2dsphere) vs SQLite (JSON + shapely)

`python -m loadtest.bench_store --routes 5000` on 2026-10-27: 5000 saved routes in 1000 projects, MongoDB 8.0.34 (Docker), Python 3.13.12, Windows-11-10.0.26200-SP0.

The routes are **generated**, not real projects: random sites in the study region, each routed by the real router to one of the three substations with a random preset. Latency is in-process, store call to Python result, after one warm-up; up to 50 repetitions or 20 s per row.

| Query | Method | p50 ms | p95 ms | Results | Plan | Docs examined |
|---|---|---:|---:|---:|---|---:|
| routes within 2 km of Santa Ysabel Substation | MongoDB $geoIntersects, 2dsphere index | 417.92 | 426.32 | 3166 | IXSCAN | 3269 |
| routes within 2 km of Santa Ysabel Substation | MongoDB $geoIntersects, no index | 550.81 | 556.24 | 3166 | COLLSCAN | 5000 |
| routes within 2 km of Santa Ysabel Substation | MongoDB $geoNear endpoint (sorted + joined) | 354.98 | 397.45 | 3166 |  |  |
| routes within 2 km of Santa Ysabel Substation | SQLite + shapely brute force endpoint | 1,975.8 | 2,606.62 | 3166 |  |  |
| routes within 0.5 km of a spot (33.08, -116.78) | MongoDB $geoIntersects, 2dsphere index | 46.02 | 48.3 | 283 | IXSCAN | 359 |
| routes within 0.5 km of a spot (33.08, -116.78) | MongoDB $geoIntersects, no index | 469.01 | 479.1 | 283 | COLLSCAN | 5000 |
| routes within 0.5 km of a spot (33.08, -116.78) | MongoDB $geoNear endpoint (sorted + joined) | 31.84 | 43.79 | 283 |  |  |
| routes within 0.5 km of a spot (33.08, -116.78) | SQLite + shapely brute force endpoint | 2,323.6 | 2,583.69 | 283 |  |  |
| routes crossing Santa Ysabel Reservation | MongoDB $geoIntersects, 2dsphere index | 96.29 | 100.88 | 719 | IXSCAN | 1315 |
| routes crossing Santa Ysabel Reservation | MongoDB $geoIntersects, no index | 263.1 | 270.21 | 719 | COLLSCAN | 5000 |
| routes crossing any tribal land (6) | MongoDB endpoint (2dsphere, per land) | 302.85 | 330.96 | 1050 |  |  |
| routes crossing any tribal land (6) | SQLite + shapely brute force endpoint | 1,980.12 | 2,293.82 | 1050 |  |  |
| list all 1000 projects + route summaries | MongoDB (summaries stored at write time) | 239.99 | 309.97 | 1000 |  |  |
| list all 1000 projects + route summaries | SQLite (re-parses every route's JSON) | 901.58 | 1,225.73 | 1000 |  |  |

Agreement between methods:

- near mongo vs shapely: 3216 common, 0 only MongoDB, 0 only shapely
- near circle vs geoNear: 3449 common, 0 differ
- tribal mongo vs shapely: 1050/1050 shapely routes match; 0 only MongoDB

Storage: MongoDB routes collection 49.2 MB on disk (avg document 23.8 KB, compressed by WiredTiger); SQLite file 71.9 MB.
Bulk load (one insert per route through the store API): MongoDB 20.0 s, SQLite 27.7 s.
