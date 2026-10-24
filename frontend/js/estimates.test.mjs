import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { estimate, rankRoutes } from "./estimates.js";

const p = JSON.parse(readFileSync(new URL("../demo_route.json", import.meta.url))).properties;
const e = estimate(p);

assert.equal(e.is_placeholder, true);
assert.equal(e.upfront_total, p.total_cost_usd);
const sum = Object.values(e.upfront_breakdown).reduce((a, b) => a + b, 0);
assert.ok(Math.abs(sum - e.upfront_total) < 1, `breakdown sum ${sum}`);
assert.ok(e.lifetime_cost_40yr > e.upfront_total);
assert.ok(e.maintenance_per_year > 0);
assert.ok(e.calendar.length >= 3);
for (let i = 0; i < e.calendar.length; i++) {
  const c = e.calendar[i];
  assert.ok(c.end > c.start, "phase has length");
  if (i) assert.ok(c.start >= e.calendar[i - 1].end, "phases ordered");
}
assert.equal(e.energize_date, e.calendar.at(-1).end);
assert.equal(e.permits.length, p.permitting_triggers.length);
assert.ok(e.permits.every((x) => x.months_max >= x.months_min && x.code && x.reason));
assert.equal(estimate({ permitting_triggers: [{ code: "ZZZ", reason: "r" }] }).permits[0].months_min, 3);
assert.ok(e.lead_times.length && e.lead_times.every((l) => l.weeks > 0));
assert.deepEqual(e.labels, { cheapest_build: false, fastest: false });

const m = (kv) => estimate(p, { voltage_kv: kv }).maintenance_per_year;
assert.ok(m(230) > m(138) && m(138) > m(69));

assert.equal(estimate(p, { filing_target: "2027-03-01" }).calendar[0].start, "2027-03-01");

const X = { custom: 1 };
const o = estimate({ ...p, estimates: X });
assert.equal(o, X);
assert.equal(o.is_placeholder, false);

const a = estimate(p);
const b = estimate({ ...p, total_cost_usd: p.total_cost_usd * 1.5 });
const ranked = rankRoutes([b, a]);
assert.equal(ranked[1].labels.cheapest_build, true);
assert.equal(ranked[0].labels.cheapest_build, false);
assert.equal(ranked.filter((r) => r.recommended).length, 1);
for (const r of ranked) assert.ok(r.score >= 0 && r.score <= 100);
const wild = estimate({ total_cost_usd: 1e12, total_km: 1e4, high_fire_km: 1e4, permitting_triggers: [] });
assert.ok(wild.score >= 0 && wild.score <= 100);
assert.ok(estimate({}).score >= 0);

console.log("estimates: all checks passed");
