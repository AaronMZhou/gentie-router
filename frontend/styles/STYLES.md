# Gen-tie Router style guide

Every page is built from `styles/tokens.css` (values), `styles/components.css` (classes) and
`js/theme.js` (the theme toggle). Open `/styles/preview.html` to see every component in both themes
side by side. When you add or change a component, add it to the preview too.

Look and feel: variant 1 of the approved mockups. Night Grid = `dark`, Solar Paper = `light`.

## 1. Design principles

- **Hairlines, not boxes.** Separate things with 1 px `--line` borders. Use `--line2` for borders
  that must read as controls (buttons, pills, inputs). No heavy shadows except on modal and toast.
- **Grid background.** Large empty surfaces (hero, map frames, thumbnails) get `.grid-bg`, a 64 px
  hairline grid.
- **Serif italic for the emphasis word.** Headings are Instrument Serif (`.display`). Exactly one
  word or phrase is italic (`<span class="it">`), often in accent colour (`it acc`):
  "Ridge <span class="it acc">route</span>".
- **Mono uppercase labels.** Field names, captions, counts and nav crumbs use `.mono-label`
  (Geist Mono 11 px, tracked .14em, `--muted`). Values sit under them in body or serif type.
- **Glow only on live or selected things.** `--glow` goes on the selected route card, the logo dot,
  progress fills and the live status `.dot`. Nothing static glows.
- **Generous whitespace.** Section padding is 24–48 px. Don't cram: put content in another bento cell.
- **Body is light.** Geist 300 at 14 px. Use 400 for emphasis, not bold.

## 2. Tokens

Theme is `data-theme="dark|light"` on `<html>`. Token blocks are scoped by `[data-theme]`, so any
element can pin a theme for its subtree (`<section data-theme="light">`). Read tokens from JS with
`cssVar("--acc")`.

### Colour

| Token | Dark | Light | Use for |
|---|---|---|---|
| `--bg` | `#02030a` | `#f3f1ea` | page background |
| `--bg2` | `#05070f` | `#ebe8de` | HUDs and map overlays (sits on maps) |
| `--surf` | `#0a0e1c` | `#fbfaf6` | cards, modal, active side-nav item |
| `--line` | `#dee4f521` | `#1a1d1a1f` | hairline dividers, grid lines, empty bar track |
| `--line2` | `#dee4f540` | `#1a1d1a3a` | control borders (btn, pill, input), dashed empty-state |
| `--text` | `#edeff6` | `#121512` | primary text, filled button background |
| `--muted` | `#9aa0b6` | `#6a6f67` * | secondary text, labels |
| `--acc` | `#8fb4ff` | `#0f9d6b` | primary accent: selected route, links, emphasis word, focus ring |
| `--acc2` | `#ff8a5b` | `#e0682b` | warnings and risk: permit risk, fire, `.risk` lines, dates to watch |
| `--ok` | `#7ef2c4` | `#0f9d6b` | good / not triggered / ready / live |
| `--glow` | `0 0 18px #8fb4ff66` | `0 0 14px #0f9d6b44` | `box-shadow` for live or selected things |
| `--mapbg` | `#060914` | `#e6e3d7` | behind maps and thumbnails |
| `--contour` | `#8fb4ff1c` | `#0f9d6b26` | decorative contour lines in illustrations |
| `--route-1` | = `--acc` | = `--acc` | selected / first route |
| `--route-2` | `#ff8a5b` | `#e0682b` | second route |
| `--route-3` | `#9aa0b6` | `#6a6f67` | third route |
| `--route-4` | `#7ef2c4` | `#2f6fd0` | fourth route (light differs because `--ok` = `--acc` there) |
| `--series-1` | `#8fb4ff` | `#0f9d6b` | chart series 1: donut slice 1, plain Gantt bar |
| `--series-2` | `#ff8a5b` | `#e0682b` | chart series 2: donut slice 2, Gantt `.o` (regulatory review) |
| `--series-3` | `#7ef2c4` | `#2f6fd0` | chart series 3: donut slice 3, Gantt `.g` (construction, energize) |
| `--series-4` | `#9aa0b6` | `#6a6f67` | chart series 4: donut slice 4 ("other") |

Charts use `--series-*`, never `--acc`/`--ok` directly. In light, `--ok` equals `--acc`, so they
would collide. All four series are mutually distinct in each theme.

\* The spec gives light `--muted` as `#6b7068`, which is 4.49:1 on `--bg` (just under AA). It is
darkened one step to `#6a6f67` (4.55:1). Everything else is verbatim from the spec/mockup.

### Type

| Token | Value |
|---|---|
| `--font-display` | "Instrument Serif" (400, italic for accents) |
| `--font-body` | Geist (300 body, 400 emphasis) |
| `--font-mono` | "Geist Mono" (400) |

Type scale (sizes are tokens; classes in brackets):

| Token | Size | Where |
|---|---|---|
| `--fs-hero` | clamp(54px, 8vw, 112px) | landing headline (`.display.display--hero`) |
| `--fs-1` | 60px | page title: route header, dashboard greeting (`.display--1`) |
| `--fs-2` | 44px | stat numbers, project-page badge (`.display--2`, `.stats .n`) |
| `--fs-3` | 28px | bento cell and modal headings (`.display--3`, `.cell h6`) |
| `--fs-4` | 26px | card titles (`.pcard h4`, `.rcard h5`) |
| `--fs-lead` | 17px | hero subtitle (`.lead`) |
| `--fs-body` | 14px | body |
| `--fs-small` | 12px | HUD text, card metric captions |
| `--fs-mono` | 11px | `.mono-label`, buttons, tabs |
| `--fs-micro` | 10px | pills |

Special numbers: KPI values 36px, scores 40px, route header figures 38px, `.big` 52px. All serif.

### Spacing and radii

Spacing, 4 px base: `--s-1` 4 · `--s-2` 8 · `--s-3` 12 · `--s-4` 16 · `--s-5` 24 · `--s-6` 32 ·
`--s-7` 48 · `--s-8` 64. Component paddings ported from the mockup (18, 22, 26, 28 px) stay as
written; use the scale for new layout gaps.

Radii: `--r-sm` 10px (side-nav items, HUDs, inputs, toast) · `--r-md` 16px (route cards, KPI strip)
· `--r-lg` 18px (project cards, modal, empty state) · `--r-pill` 99px (buttons, pills, toggle).

## 3. Page skeleton

### Head snippet (paste into every page, before any stylesheet)

It sets the theme before first paint (no flash) and installs the error half of the boot contract.

```html
<script>
  (function () {
    var d = document.documentElement, t;
    try { t = localStorage.getItem("gentie.theme"); } catch (e) {}
    if (t !== "dark" && t !== "light") t = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    d.dataset.theme = t;
    window.addEventListener("error", function (e) { d.dataset.errors = (d.dataset.errors || "") + e.message + "|"; });
    window.addEventListener("unhandledrejection", function (e) { d.dataset.errors = (d.dataset.errors || "") + (e.reason && e.reason.message || String(e.reason)) + "|"; });
  })();
</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Geist+Mono:wght@400;500&family=Geist:wght@300;400;500&display=swap">
<link rel="stylesheet" href="styles/tokens.css">
<link rel="stylesheet" href="styles/components.css">
```

### Boot contract

- When boot finishes (data loaded or an error/empty state rendered), the page module runs
  `document.documentElement.dataset.ready = "1"`.
- Uncaught errors **and unhandled Promise rejections** are appended to `<html data-errors>` by the
  head snippet. A page that shows an inline error state for a failed fetch has handled it: that's
  not an uncaught error. Every `async` path must `catch` (or `await` inside `try`). A forgotten
  rejection fails the smoke test.
- `tools/smoke_pages.py` passes a page only with `data-ready="1"` and empty `data-errors`.

### Full page template

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Page name · Gen-tie Router</title>
  <link rel="icon" href="data:,">
  <!-- head snippet here -->
</head>
<body>
  <nav class="nav-top">
    <a class="logo" href="index.html"><i></i>Gen-tie Router <span class="mono-label crumb">/ Projects / Ramona Solar</span></a>
    <div class="actions">
      <button class="btn" type="button">+ Add route</button>
      <button class="theme-toggle" type="button"></button>
    </div>
  </nav>
  <main id="app"></main>
  <script type="module" src="js/pages/mypage.js"></script>
</body>
</html>
```

```js
// js/pages/mypage.js
import { initThemeToggle } from "../theme.js";
initThemeToggle(document.querySelector(".theme-toggle"));
// ... render ...
document.documentElement.dataset.ready = "1";
```

The dashboard skeleton swaps the top nav for the side nav:

```html
<div class="dash">
  <aside class="side">
    <a class="logo" href="index.html" style="margin-bottom:26px"><i></i>Gen-tie Router</a>
    <a class="is-active" href="dashboard.html" aria-current="page">Projects <span class="mono-label">4</span></a>
    <div style="flex:1"></div>
    <button class="theme-toggle" type="button"></button>
  </aside>
  <main class="main">
    <div class="h-row">
      <div><div class="mono-label">Portfolio · Oct 2026</div><h1 class="display display--1">Good evening, <span class="it acc">Hari.</span></h1></div>
      <button class="btn btn--fill" type="button">+ New project</button>
    </div>
    <!-- .kpis, .pgrid -->
  </main>
</div>
```

The project page body is `<div class="proj"><div class="mapwrap">…Cesium…</div><div class="rail">…</div></div>`.
The route page body is `.rhead`, `.tabs`, `.three` (Cesium hero, 520 px) then `.bento`.

## 4. How to add a page

1. Copy the full page template above to `frontend/<name>.html`. Fix the stylesheet paths if the page
   is not at the frontend root.
2. Create `frontend/js/pages/<name>.js`. Call `initThemeToggle` and set `data-ready="1"` at the end of
   boot, on every path, including error and not-found states.
3. Build only from classes in this file. If you need a new component, add it to `components.css`,
   `preview.html` and this guide in the same change.
4. Anything themed in JS (canvas, Cesium colours, SVG fills set from code) reads `cssVar(...)` and
   re-applies inside `onThemeChange(...)`.
5. Wrap every `localStorage` access in try/catch.
6. Add the page to the smoke run: `python tools/smoke_pages.py /<name>.html`.

## 5. Components

Every snippet is copy-paste ready. Live versions are in `preview.html`.

### Logo, top nav, breadcrumb

```html
<nav class="nav-top">
  <a class="logo" href="index.html"><i></i>Gen-tie Router <span class="mono-label crumb">/ Projects / Ramona Solar</span></a>
  <div class="navl mono-label"><a href="#how">How it works</a><a href="#data">Data</a></div>
  <div class="actions"><a class="btn" href="dashboard.html">Sign in →</a><button class="theme-toggle" type="button"></button></div>
</nav>
```

### Side nav (`.side`)

```html
<aside class="side">
  <a class="is-active" href="#" aria-current="page">Projects <span class="mono-label">4</span></a>
  <a href="#">Routes <span class="mono-label">11</span></a>
</aside>
```

### Theme toggle (`.theme-toggle`)

```html
<button class="theme-toggle" type="button"></button>
```
`initThemeToggle(button)` fills the label ("◐ Dark" / "◑ Light") and the `aria-label`.

### Buttons

```html
<button class="btn btn--fill" type="button">Start a project →</button>
<button class="btn" type="button">Watch a route get built</button>
<button class="btn btn--sm" type="button">▶ Fly along</button>
```
Use `<a class="btn">` for navigation. Use one `btn--fill` per view, for the primary action.

### Pills

```html
<span class="pill">Drafting</span>
<span class="pill pill--blue">Comparing</span>
<span class="pill pill--warn">Permit risk</span>
<span class="pill pill--ok">Ready to file</span>
```
Phase to pill: `drafting` → plain, `comparing` → `--blue`, `permit-risk` → `--warn`, `ready` → `--ok`.

### Labels, headings, accents

```html
<div class="mono-label"><span class="dot"></span>&nbsp; Pre-approval route intelligence</div>
<h1 class="display display--hero">Know the <span class="it acc">whole line</span><br>before you file.</h1>
<p class="lead">Draft transmission routes and see what each one really costs.</p>
```
Utilities: `.it`, `.acc`, `.acc2`, `.ok`, `.muted`, `.hair` (hairline box), `.wrap` (1240 px
container), `.sr-only`.

### Grid background

```html
<div class="hero grid-bg">…</div>
```

### Landing stats and steps

```html
<div class="stats">
  <div><div class="mono-label">Route solve time</div><div class="n it">~9 ms</div></div>
  <!-- 4 children -->
</div>
<div class="steps">
  <div><div class="mono-label">01 / Draw</div><h3>Pick the <span class="it">endpoints</span></h3><p>Drop a site and a substation.</p></div>
  <!-- 3 children -->
</div>
```

### KPI strip (`.kpis`)

```html
<div class="kpis">
  <div><div class="mono-label">Active projects</div><div class="n">4</div></div>
  <div><div class="mono-label">Routes in study</div><div class="n">11</div></div>
  <div><div class="mono-label">Portfolio capex</div><div class="n">$1.42B</div></div>
  <div><div class="mono-label">Next filing</div><div class="n it acc2">Dec 12</div></div>
</div>
```

### Project card (`.pcard`) and new-project card

```html
<div class="pgrid">
  <a class="pcard" href="project.html?id=1">
    <div class="thumb grid-bg"><svg viewBox="0 0 300 150" width="100%" height="100%" aria-hidden="true">
      <path d="M30 120 C90 90 140 70 270 30" stroke="var(--route-1)" stroke-width="2" fill="none"/>
      <path d="M30 120 C90 90 140 70 270 30" stroke="var(--text)" stroke-width="1.5" fill="none" class="flow"/>
    </svg></div>
    <div class="body">
      <div class="top"><span class="pill pill--blue">Comparing</span><span class="mono-label">3 routes</span></div>
      <h4>Ramona Solar <span class="it">Gen-tie</span></h4>
      <div class="meta"><div class="mono-label">Capex<b>$386M</b></div><div class="mono-label">Voltage<b>230 kV</b></div><div class="mono-label">Length<b>21 mi</b></div></div>
      <div class="bar"><i style="width:50%"></i></div>
    </div>
  </a>
  <button class="pcard pcard--new" type="button"><span><span class="display it" style="font-size:40px">+</span><span class="mono-label" style="display:block;margin-top:8px">Start a project</span></span></button>
</div>
```
Progress fill by phase: drafting 15 %, comparing 50 %, permit-risk 35 %, ready 90 % (ready uses
`style="background:var(--ok)"` on the `<i>`).

### Route card (`.rcard`, `.is-selected`)

```html
<div class="rail">
  <div class="rcard is-selected" tabindex="0" role="button" aria-pressed="true">
    <div class="top"><span class="pill pill--blue">Recommended</span><div class="score acc">82 <span class="est" title="Placeholder score">est.</span></div></div>
    <h5>Route 1 · <span class="it">Ridge</span></h5>
    <div class="mm">
      <div>Upfront<b>$386M</b></div><div>Upkeep/yr<b>$5.4M <span class="est" title="Placeholder estimate">est.</span></b></div>
      <div>Energize<b>Q3 2031 <span class="est" title="Placeholder estimate">est.</span></b></div>
      <div>Length<b>21.4 mi</b></div><div>Towers<b>112</b></div><div>Permits<b>6</b></div>
    </div>
    <div class="risk">▲ 4.2 mi through fire tier 3</div>
  </div>
</div>
```
Only one card is `.is-selected`; it matches the route drawn in `--route-1` with the flowing line.

### HUD, map badge, legend (over a map)

```html
<div class="mapwrap grid-bg">
  <!-- Cesium container fills this -->
  <div class="hud mapbadge"><div class="mono-label">Ramona → Creelman · 230 kV</div><div class="display" style="font-size:28px;margin-top:4px">3 candidate <span class="it">routes</span></div></div>
  <div class="hud" style="right:20px;top:20px"><div class="mono-label">3D terrain · Cesium</div><div style="margin-top:6px">Elevation 420–1,180 m</div></div>
  <div class="hud hud--alert" style="left:54%;top:40%"><div class="mono-label acc2">Segment 7</div><div>Fire tier 3 · 1.8 mi</div></div>
  <div class="hud maplegend"><span><i style="background:var(--route-1)"></i>Route 1 · Ridge</span><span><i style="background:var(--route-2)"></i>Route 2</span></div>
</div>
```
`.hud` is absolutely positioned; place it with inline `left/right/top/bottom`. `.hud--glass` blurs
what's behind it.

### Tabs (`.tabs`)

```html
<nav class="tabs" aria-label="Route sections">
  <a href="#overview" class="is-active" aria-current="true">Overview</a>
  <a href="#inventory">Inventory</a>
  <a href="#costs">Costs</a>
</nav>
```
Anchors scroll to sections. For real tab switching use `<button>` children with `role="tab"`.

### Route header

```html
<div class="rhead">
  <div><div class="mono-label">Route 01 · 230 kV overhead · 21.4 mi</div><h1 class="display display--1">Ridge <span class="it acc">route</span></h1></div>
  <div class="figs">
    <div><div class="mono-label">Upfront</div><div class="display">$386M</div></div>
    <div><div class="mono-label">40-yr total</div><div class="display">$602M <span class="est" title="Placeholder estimate">est.</span></div></div>
  </div>
</div>
```

### Bento grid (`.bento` / `.cell`)

A 12-column grid. Each cell sets its width with `grid-column: span N` (default 4). Rows should add
up to 12. Below 900 px everything stacks.

```html
<div class="bento">
  <div class="cell" style="grid-column:span 4"><div class="mono-label">Upfront cost</div><h6>Where the <span class="it">$386M</span> goes</h6>…</div>
  <div class="cell" style="grid-column:span 8">…</div>
</div>
```

### Donut (`.donut`)

Four slices in `--series-1..4`. Set the cumulative split points.

```html
<div class="donut" style="--a:44%;--b:63%;--c:81%" role="img" aria-label="Towers 44%, ROW 19%, conductor 18%, other 19%"></div>
<div class="lg">
  <span><span><i style="background:var(--series-1)"></i>Towers &amp; foundations</span>$170M</span>
  <span><span><i style="background:var(--series-2)"></i>Right-of-way</span>$73M</span>
  <span><span><i style="background:var(--series-3)"></i>Conductor</span>$69M</span>
  <span><span><i style="background:var(--series-4)"></i>Permits, eng.</span>$74M</span>
</div>
```

### Inventory table (`.inv`)

```html
<table class="inv">
  <tr><td>Lattice towers</td><td>112</td></tr>
  <tr><td>Conductor (ACSR Drake)</td><td>207 km</td></tr>
</table>
```
The last column is right-aligned mono. It works for any two-column key/value table.

### Gantt (`.gantt`)

Positions are percentages of the whole calendar span. Bar colours: plain = `--series-1`,
`.o` = `--series-2` (regulatory review), `.g` = `--series-3` (construction, energize).

```html
<div class="gantt">
  <span></span><div class="years mono-label"><span>2027</span><span>2029</span><span>2031</span></div>
  <span>Federal route filing</span><div class="track"><i style="left:0;width:12%"></i></div>
  <span>NEPA review</span><div class="track"><i class="o" style="left:8%;width:38%"></i></div>
  <span>Construction</span><div class="track"><i class="g" style="left:58%;width:34%"></i></div>
</div>
```

### Permit row (`.permit`)

```html
<div class="permit"><span>NEPA (federal nexus)</span><span class="pill pill--warn">14–20 mo</span></div>
<div class="permit"><span>Tribal consultation</span><span class="pill pill--ok">Not triggered</span></div>
```

### Risk line, score, progress bar, sparkline

```html
<div class="risk">▲ 47 parcels, 3 Caltrans encroachments</div>
<div class="score acc">82</div>
<div class="bar"><i style="width:62%"></i></div>
<svg viewBox="0 0 300 110" class="spark"><path d="M0 80 L150 60 L300 40" fill="none" stroke="var(--acc)" stroke-width="2"/></svg>
```

### Empty / error state (`.empty-state`)

Use for "no projects yet", "backend unreachable" (with Retry) and "not found" (with a link back).

```html
<div class="empty-state">
  <div class="display display--3">Backend <span class="it">unreachable</span></div>
  <p class="muted">Couldn't load your projects. Is the server running?</p>
  <button class="btn" type="button">Retry</button>
</div>
```

### Modal (`.modal`)

A native `<dialog>`: `showModal()` gives focus trapping, Esc to close and a `::backdrop`.

```html
<dialog class="modal" id="newProject">
  <form method="dialog">
    <div class="mono-label">New project</div>
    <h3 class="display display--3">Name your <span class="it">line</span></h3>
    <label class="field"><span class="mono-label">Name</span><input name="name" required></label>
    <label class="field"><span class="mono-label">Voltage</span><select name="voltage_kv"><option>69</option><option selected>138</option><option>230</option></select></label>
    <div class="actions"><button class="btn" value="cancel" formnovalidate>Cancel</button><button class="btn btn--fill" value="ok">Create</button></div>
  </form>
</dialog>
```

### Toast (`.toast`)

Fixed, bottom centre. Show it by removing `hidden`; hide it again after a few seconds.

```html
<div class="toast" role="status" hidden>Please click inside the study region.</div>
<div class="toast toast--error" role="alert" hidden>Routing failed: backend offline.</div>
```

## 6. Map conventions

- **Route colours** come from `--route-1..4`, read with `cssVar()` and re-read on `onThemeChange`.
  The **selected** route is always `--route-1` (= `--acc`), full opacity, with the **flowing line**
  and tower dots. The others take `--route-2..4` in order at about 60–70 % opacity and thinner width.
  The `--acc2` route is drawn dashed (`7 6`), as in the mockup.
- **Flowing line:** a `--text`-coloured dash pattern (4 on, 14 off) moving along the selected route
  over a `--acc` base line. In SVG it's `class="flow"`. On Cesium it's a time-driven dash offset
  material, drawn on the ground under the 3D poles. It runs only while the page is visible and is
  off under `prefers-reduced-motion`.
- **Endpoints:** the generation site is an `--ok` dot with a `.pulse` ring; the substation is an
  `--acc` square outline.
- **Zones:** fire = `--acc2` at low alpha with a dashed edge; protected = `--ok` at low alpha.
- **Overlays on maps** are `.hud` panels: badge top left, legend bottom left, layers top right,
  fly-along bottom centre.
- Imagery and terrain don't change with theme. Only chrome, HUDs and route colours do.

## 7. Placeholder ("est.") conventions

- Every value that comes from `js/estimates.js` (maintenance, 40-yr cost, calendar, energize date,
  lead times, score, top risk) is followed by an `est.` marker:
  `$5.4M <span class="est" title="Placeholder estimate, not a real model">est.</span>`.
- Put the marker right after the value, inside the same element, so it moves with it.
- The `title` says what it is. Mention the basis when it's short, e.g. "Placeholder: $X per km".
- When a whole block is placeholder (the Gantt or the maintenance cell), one marker in the cell's
  `.mono-label` heading covers it: `Maintenance <span class="est" …>est.</span>`.
- Real backend values never get the marker. When `routeProps.estimates` exists the values are real,
  so render without markers (check `is_placeholder`).

## 8. Accessibility

Contrast, WCAG 2 ratios (AA needs 4.5:1 for body-size text, 3:1 for large text ≥ 24 px and UI parts):

| Pair | Dark | Light |
|---|---|---|
| `--text` on `--bg` | 17.92 | 16.27 |
| `--muted` on `--bg` | 7.92 | 4.55 |
| `--muted` on `--surf` | 7.39 | 4.93 |
| `--text` on `--surf` | 16.73 | 17.61 |
| `--text` on `--bg2` | 17.51 | 15.00 |
| `--muted` on `--bg2` | 7.74 | 4.20 |
| `--acc` on `--bg` | 9.94 | 3.07 |
| `--acc2` on `--bg` | 8.86 | 3.00 |
| `--ok` on `--bg` | 15.08 | 3.07 |
| `--bg` on `--text` (filled button) | 17.92 | 16.27 |

Rules that follow:
- In **light**, `--acc`, `--acc2` and `--ok` reach only 3:1. Use them for large serif text (≥ 24 px),
  borders, fills and icons. Don't put long small text in them. Short pill and `.risk` labels are a
  known compromise from the mockup.
- In **light**, `--muted` on `--bg2` (HUDs) is 4.20:1. Keep HUD body copy in `--text`; muted is for
  the mono caption only.
- **Focus:** every focusable element gets the global `:focus-visible` ring (2 px `--acc`, 2 px
  offset). Don't remove outlines. Clickable cards are `<a>`/`<button>`, or `tabindex="0"` with
  Enter/Space handlers.
- **Reduced motion:** under `prefers-reduced-motion: reduce`, all CSS animations and transitions are
  off (`components.css`). JS animations (the Cesium flowing line, fly-along easing) must check
  `matchMedia("(prefers-reduced-motion: reduce)")` themselves.
- Colour is never the only signal: pills have text, risks have ▲, legends have labels. Chart
  series use `--series-*`, which stay distinct in both themes.
- Decorative SVG gets `aria-hidden="true"`; the donut gets `role="img"` and an `aria-label`.

## 9. JS: `js/theme.js`

```js
import { initThemeToggle, currentTheme, onThemeChange, cssVar } from "./theme.js";
initThemeToggle(button);           // wires a .theme-toggle; click flips, stores gentie.theme, fires gentie:theme
currentTheme();                    // "dark" | "light"
onThemeChange((theme) => recolor());   // listens to window "gentie:theme"
cssVar("--route-1");               // resolved token value on <html>, e.g. "#8fb4ff"
```
Code that sets `data-theme` itself must then fire
`window.dispatchEvent(new CustomEvent("gentie:theme", { detail: { theme } }))`.

## 10. Smoke test

```
python tools/smoke_pages.py /styles/preview.html /dashboard.html      # uvicorn on :8767, temp DB, seeded (--no-seed: empty)
python tools/smoke_pages.py "/route.html?demo=1" --no-server          # static files only, no API
python tools/smoke_pages.py /styles/preview.html --eval 'document.querySelectorAll(".pcard").length > 0'
```
`--eval` expressions run in page global scope after `data-ready="1"`. They may `await`. Expose
what a check needs on `window` (module internals aren't visible). With `--eval` the pages are
fetched through a rewriting proxy on :8769.
