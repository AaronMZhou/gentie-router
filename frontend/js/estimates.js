// Placeholder estimates derived from real route fields. Every number here is an
// "est." (is_placeholder: true). A backend `properties.estimates` replaces them all.

export const RATES = {
  tower_usd: 90000,           // per structure
  conductor_usd_per_km: 28000, // per conductor-km (inventory.conductor_km_est)
  cable_usd_per_km: 900000,   // per underground cable-km
  row_usd_per_parcel: 18000,  // easements / negotiation per parcel
  fire_mitigation_usd_per_km: 22000, // per overhead high-fire km
  transition_usd: 450000,     // per overhead/underground transition
  maint_oh_km: 1800,          // $/yr per overhead km
  maint_ug_km: 600,           // $/yr per underground km
  maint_tower: 120,           // $/yr per tower
  maint_fire_km: 900,         // $/yr per overhead high-fire km (vegetation, inspections)
  years: 40,                  // lifetime horizon, undiscounted
  studies_weeks: 12,
  row_weeks_per_100_parcels: 10,
  construction_weeks_base: 8,
  construction_weeks_per_km: 2.5,
  construction_weeks_per_ug_km: 4,
  energize_weeks: 3,
};
export const VOLTAGE_SCALE = { 69: 0.8, 138: 1.0, 230: 1.35 };
// [min, max] months by trigger code; unknown code -> DEFAULT_PERMIT_MONTHS
export const PERMIT_MONTHS = {
  CEQA: [8, 14], NEPA: [12, 24], HFTD: [3, 6], CPUC: [12, 18],
  PROTECTED: [12, 24], TRIBAL: [9, 18], FEDERAL: [10, 20], STATE: [6, 12],
};
export const DEFAULT_PERMIT_MONTHS = [3, 6];

const DAY = 86400000;
const iso = (ms) => new Date(ms).toISOString().slice(0, 10);
const addDays = (s, d) => iso(Date.parse(s) + Math.round(d) * DAY);
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
const sumVals = (o) => Object.values(o).reduce((a, b) => a + b, 0);

export function estimate(props = {}, project = {}) {
  // Backend hook: same object back (identity kept), flagged as real.
  if (props.estimates) return Object.assign(props.estimates, { is_placeholder: false });

  const inv = props.inventory || {};
  const vScale = VOLTAGE_SCALE[project?.voltage_kv] ?? 1;
  const total = props.total_cost_usd || 0;
  const ohKm = props.overhead_km ?? props.total_km ?? 0;
  const ugKm = props.underground_km || 0;
  const towers = props.tower_count ?? inv.tower_count ?? 0;
  const fireKm = props.overhead_high_fire_km ?? props.high_fire_km ?? 0;
  const parcels = props.row?.private_parcels || 0;

  // Upfront: weight components by rates, then scale to the real model total.
  const raw = {
    structures: towers * RATES.tower_usd * vScale,
    conductor: (inv.conductor_km_est || ohKm * 3) * RATES.conductor_usd_per_km * vScale,
    underground_cable: ugKm * RATES.cable_usd_per_km,
    right_of_way: parcels * RATES.row_usd_per_parcel,
    fire_mitigation: fireKm * RATES.fire_mitigation_usd_per_km,
    transitions: (props.n_transitions || 0) * RATES.transition_usd,
  };
  const rawSum = sumVals(raw);
  const upfront_breakdown = {};
  for (const k in raw) upfront_breakdown[k] = rawSum ? (raw[k] / rawSum) * total : 0;
  if (!rawSum) upfront_breakdown.structures = total;

  const maintenance_breakdown = {
    overhead_lines: ohKm * RATES.maint_oh_km * vScale,
    underground_cable: ugKm * RATES.maint_ug_km * vScale,
    structures: towers * RATES.maint_tower * vScale,
    fire_vegetation: fireKm * RATES.maint_fire_km * vScale,
  };
  const maintenance_per_year = sumVals(maintenance_breakdown);

  const permits = (props.permitting_triggers || []).map(({ code, reason }) => {
    const [months_min, months_max] = PERMIT_MONTHS[code] || DEFAULT_PERMIT_MONTHS;
    return { code, reason, months_min, months_max };
  });
  const permitMonths = Math.max(3, ...permits.map((x) => x.months_max));

  const lead_times = [
    { item: "Steel structures", weeks: Math.round(20 + towers * 0.15) },
    { item: "Conductor & hardware", weeks: 18 },
    { item: "Substation terminal equipment", weeks: Math.round(40 * vScale) },
  ];
  if (ugKm > 0) lead_times.push({ item: "Underground cable", weeks: 52 });
  const procureWeeks = Math.max(...lead_times.map((l) => l.weeks));

  const durations = [
    ["Studies & siting", RATES.studies_weeks * 7],
    ["Permitting", permitMonths * 30.4],
    ["Land & right-of-way", (parcels / 100) * RATES.row_weeks_per_100_parcels * 7 + 14],
    ["Procurement", procureWeeks * 7],
    ["Construction", (RATES.construction_weeks_base + ohKm * RATES.construction_weeks_per_km + ugKm * RATES.construction_weeks_per_ug_km) * 7],
    ["Energization", RATES.energize_weeks * 7],
  ];
  const today = iso(Date.now());
  let cursor = project?.filing_target || today;
  const calendar = durations.map(([phase, days]) => {
    const start = cursor;
    cursor = addDays(start, Math.max(1, days));
    return { phase, start, end: cursor };
  });

  // Score: start at 100, subtract cost, fire exposure, permitting time and protected/tribal crossings.
  const fireShare = (props.total_km || 0) > 0 ? fireKm / props.total_km : 0;
  const score = Math.round(clamp(
    100 - Math.min(35, total / 2e6) - fireShare * 20 - Math.min(25, permitMonths) -
      Math.min(10, (props.protected_km || 0) + (props.tribal_km || 0)), 0, 100));

  const risks = [
    [permitMonths / 24, permits.length ? `Permitting: ${[...permits].sort((a, b) => b.months_max - a.months_max)[0].code} review (${permitMonths} mo max)` : null],
    [fireShare, `Wildfire exposure: ${fireKm.toFixed(1)} km of overhead in high fire-hazard terrain`],
    [((props.protected_km || 0) + (props.tribal_km || 0)) / 5, "Protected or tribal land crossing"],
  ].filter((r) => r[1]).sort((a, b) => b[0] - a[0]);

  return {
    upfront_total: total,
    upfront_breakdown,
    maintenance_per_year,
    maintenance_breakdown,
    lifetime_cost_40yr: total + maintenance_per_year * RATES.years,
    calendar,
    energize_date: calendar[calendar.length - 1].end,
    lead_times,
    permits,
    score,
    top_risk: risks[0]?.[1] || "No major risk flagged",
    labels: { cheapest_build: false, fastest: false },
    is_placeholder: true,
  };
}

// Mutates and returns the list: sets labels and `recommended` (highest score).
export function rankRoutes(estimates) {
  if (!estimates.length) return estimates;
  const min = (key) => estimates.reduce((a, b) => (b[key] < a[key] ? b : a));
  const cheapest = min("upfront_total");
  const fastest = min("energize_date");
  const top = estimates.reduce((a, b) => (b.score > a.score ? b : a));
  for (const e of estimates) {
    e.labels = { cheapest_build: e === cheapest, fastest: e === fastest };
    e.recommended = e === top;
  }
  return estimates;
}
