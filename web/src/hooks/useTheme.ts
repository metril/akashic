import { useCallback, useEffect, useState } from "react";

export type ThemeMode = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "theme";

// Read the stored mode without crashing on SSR or storage-disabled browsers.
function readStoredMode(): ThemeMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {
    // Storage may be blocked (private mode, embed contexts) — fall through.
  }
  return "system";
}

function systemPrefersDark(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );
}

function resolve(mode: ThemeMode): ResolvedTheme {
  if (mode === "system") return systemPrefersDark() ? "dark" : "light";
  return mode;
}

// Toggle the .dark class on <html>. Kept side-effect-free so the
// pre-mount inline script in index.html can run the same logic without
// importing this module.
function applyClass(theme: ResolvedTheme) {
  const root = document.documentElement;
  if (theme === "dark") root.classList.add("dark");
  else root.classList.remove("dark");
}

export function useTheme() {
  const [mode, setModeState] = useState<ThemeMode>(() => readStoredMode());
  const [resolved, setResolved] = useState<ResolvedTheme>(() => resolve(readStoredMode()));

  // Sync `<html class="dark">` whenever mode changes, and persist.
  useEffect(() => {
    const r = resolve(mode);
    setResolved(r);
    applyClass(r);
    try {
      localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      // Ignore — user kept their preference for this session only.
    }
  }, [mode]);

  // Track OS-level light/dark when the user is in "system" mode.
  // Without this, switching the OS theme leaves Akashic stuck on whatever
  // it picked at first paint.
  useEffect(() => {
    if (mode !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => {
      const r: ResolvedTheme = mq.matches ? "dark" : "light";
      setResolved(r);
      applyClass(r);
    };
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [mode]);

  const setMode = useCallback((next: ThemeMode) => setModeState(next), []);

  return { mode, resolved, setMode };
}
