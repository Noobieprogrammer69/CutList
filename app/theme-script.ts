export const THEME_STORAGE_KEY = "cutlist-theme";

// Runs before paint so a saved dark theme never flashes a light background.
export const themeScript = `(() => {
  let saved;
  try { saved = localStorage.getItem("${THEME_STORAGE_KEY}"); } catch {}
  const theme = saved === "light" || saved === "dark" ? saved : window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.dataset.theme = theme;
})();`;
