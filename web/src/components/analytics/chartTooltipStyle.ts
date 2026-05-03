import { useMemo } from "react";
import { useChartColors } from "../../hooks/useChartColors";

/**
 * Stable, memoised tooltip styling derived from the current
 * `useChartColors()` output. recharts re-renders its tooltip subtree
 * whenever any of these prop *references* change, so creating the
 * objects inline (`contentStyle={{...}}`) on each parent render was a
 * dominant source of tooltip lag on the Analytics page even though the
 * shape of the values was unchanged.
 *
 * Returned objects keep stable identity across renders unless the
 * resolved theme actually changes (light↔dark toggle).
 */
export function useChartTooltipStyle() {
  const c = useChartColors();
  return useMemo(
    () => ({
      contentStyle: {
        background: c.tooltipBg,
        border: `1px solid ${c.tooltipBorder}`,
        borderRadius: 8,
        fontSize: 13,
        boxShadow: "0 4px 12px rgba(0,0,0,0.18)",
        color: c.tooltipFg,
      },
      labelStyle: { color: c.tooltipFg },
      itemStyle: { color: c.tooltipFg },
      cursor: { fill: c.cursorFill },
      colors: c,
    }),
    [c],
  );
}
