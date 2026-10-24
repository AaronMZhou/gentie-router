// Theme toggle. The initial theme is set by the inline <head> snippet (see styles/STYLES.md);
// this module only switches it, persists it and announces it as the window event "gentie:theme".
const KEY = "gentie.theme";
const root = document.documentElement;

export function currentTheme() {
  return root.dataset.theme === "light" ? "light" : "dark";
}

// Resolved value of a CSS custom property on <html>, e.g. cssVar("--acc") -> "#8fb4ff".
export function cssVar(name) {
  return getComputedStyle(root).getPropertyValue(name).trim();
}

export function onThemeChange(cb) {
  window.addEventListener("gentie:theme", (e) => cb(e.detail?.theme ?? currentTheme()));
}

function setTheme(theme) {
  root.dataset.theme = theme;
  try { localStorage.setItem(KEY, theme); } catch { /* storage blocked: theme still applies for this page */ }
  window.dispatchEvent(new CustomEvent("gentie:theme", { detail: { theme } }));
}

export function initThemeToggle(button) {
  const sync = () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    button.textContent = currentTheme() === "dark" ? "◐ Dark" : "◑ Light";
    button.setAttribute("aria-label", `Switch to ${next} theme`);
    button.title = `Switch to ${next} theme`;
  };
  button.addEventListener("click", () => setTheme(currentTheme() === "dark" ? "light" : "dark"));
  onThemeChange(sync);
  sync();
}
