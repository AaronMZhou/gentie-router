import { initThemeToggle } from "../theme.js";
import { apiGet } from "../api.js";

initThemeToggle(document.querySelector(".theme-toggle"));

// Live layer count from /bounds; any failure leaves the static "12" in place.
// /stats has no solve-time field, so "~9 ms" stays static copy.
try {
  const { sources } = await apiGet("/bounds");
  const n = Array.isArray(sources) ? sources.length : Object.keys(sources || {}).length;
  if (n > 0) document.getElementById("stat-layers").textContent = n;
} catch { /* offline or backend down: static copy */ }

document.documentElement.dataset.ready = "1";
