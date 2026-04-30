import { useEffect, useState } from "react";
import { useTheme } from "./useTheme";

interface ChartColors {
  axis: string;
  axisLabel: string;
  tooltipBg: string;
  tooltipBorder: string;
  cursorFill: string;
}

// Read computed CSS-var values once per theme change. recharts wants
// concrete strings (not "var(--…)") because it serializes them into SVG
// attributes which CSS vars don't resolve in. Re-read whenever
// `resolved` flips so a theme toggle re-paints charts.
export function useChartColors(): ChartColors {
  const { resolved } = useTheme();
  const [colors, setColors] = useState<ChartColors>(() => fallback(resolved));

  useEffect(() => {
    const root = getComputedStyle(document.documentElement);
    setColors({
      axis: root.getPropertyValue("--color-fg-subtle").trim() || fallback(resolved).axis,
      axisLabel: root.getPropertyValue("--color-fg-muted").trim() || fallback(resolved).axisLabel,
      tooltipBg: root.getPropertyValue("--color-surface").trim() || fallback(resolved).tooltipBg,
      tooltipBorder: root.getPropertyValue("--color-border").trim() || fallback(resolved).tooltipBorder,
      cursorFill: resolved === "dark" ? "rgba(99,102,241,0.16)" : "rgba(99,102,241,0.06)",
    });
  }, [resolved]);

  return colors;
}

function fallback(resolved: "light" | "dark"): ChartColors {
  if (resolved === "dark") {
    return {
      axis: "#94a3b8",
      axisLabel: "#cbd5e1",
      tooltipBg: "#1f2937",
      tooltipBorder: "#334155",
      cursorFill: "rgba(99,102,241,0.16)",
    };
  }
  return {
    axis: "#9ca3af",
    axisLabel: "#6b7280",
    tooltipBg: "#ffffff",
    tooltipBorder: "#e5e7eb",
    cursorFill: "rgba(99,102,241,0.06)",
  };
}
