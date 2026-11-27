"use client";

import { useEffect, useState } from "react";
import { THEME_STORAGE_KEY as KEY } from "./theme-script";

type Theme = "light" | "dark";

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("light");
  useEffect(() => {
    const system = window.matchMedia("(prefers-color-scheme: dark)");
    function apply(value: Theme) {
      document.documentElement.dataset.theme = value;
      setTheme(value);
    }
    function sync() {
      let saved;
      try { saved = localStorage.getItem(KEY); } catch {}
      apply(saved === "light" || saved === "dark" ? saved : system.matches ? "dark" : "light");
    }
    sync();
    const storageChanged = (event: StorageEvent) => { if (event.key === KEY || event.key === null) sync(); };
    system.addEventListener("change", sync);
    window.addEventListener("storage", storageChanged);
    return () => { system.removeEventListener("change", sync); window.removeEventListener("storage", storageChanged); };
  }, []);

  function choose(value: Theme) {
    document.documentElement.dataset.theme = value;
    setTheme(value);
    try { localStorage.setItem(KEY, value); } catch { /* The current tab still works if storage is blocked. */ }
  }

  return <div className="theme-toggle" role="group" aria-label="Color theme">
    <button type="button" aria-pressed={theme === "light"} onClick={() => choose("light")}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/></svg>
      Light
    </button>
    <button type="button" aria-pressed={theme === "dark"} onClick={() => choose("dark")}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" aria-hidden="true"><path d="M20.5 14A9 9 0 0 1 10 3.5 9 9 0 1 0 20.5 14Z"/></svg>
      Dark
    </button>
  </div>;
}
