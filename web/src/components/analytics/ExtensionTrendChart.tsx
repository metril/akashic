import { memo, useCallback, useMemo } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { ExtensionTrendPoint } from "../../hooks/useAnalyticsTimeseries";
import { formatBytes } from "../../lib/format";
import { useChartTooltipStyle } from "./chartTooltipStyle";

const SERIES_COLORS = ["#6366f1", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"];
const LEGEND_STYLE = { fontSize: 11, paddingTop: 8 };

interface Props {
  data: Record<string, ExtensionTrendPoint[]>;
}

export const ExtensionTrendChart = memo(function ExtensionTrendChart({ data }: Props) {
  const { contentStyle, labelStyle, itemStyle, colors: c } =
    useChartTooltipStyle();
  const extensions = useMemo(() => Object.keys(data), [data]);

  // Pivot wide: one row per timestamp, one column per extension's bytes.
  // Recharts' multi-line layout wants a single `data` array with named
  // series rather than separate datasets, so this re-shape is necessary.
  const points = useMemo(() => {
    type Row = { label: string; [series: string]: string | number };
    const byDate = new Map<string, Row>();
    for (const ext of extensions) {
      for (const p of data[ext]) {
        const key = p.taken_at;
        const row =
          byDate.get(key) ??
          ({ label: new Date(key).toLocaleDateString() } as Row);
        row[ext] = p.bytes;
        byDate.set(key, row);
      }
    }
    return [...byDate.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([, row]) => row);
  }, [data, extensions]);

  const tickFormatter = useCallback((v: number) => formatBytes(v), []);
  const formatter = useCallback(
    (v: number, n: string) => [formatBytes(v), n] as [string, string],
    [],
  );
  const labelFormatter = useCallback((l: string) => l, []);

  return (
    <div className="h-64 -mx-2">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 8, right: 24, bottom: 8, left: 8 }}>
          <CartesianGrid stroke={c.tooltipBorder} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" stroke={c.axis} fontSize={11} tickLine={false} axisLine={false} />
          <YAxis
            stroke={c.axis}
            fontSize={11}
            tickFormatter={tickFormatter}
            tickLine={false}
            axisLine={false}
            width={70}
          />
          <Tooltip
            contentStyle={contentStyle}
            labelStyle={labelStyle}
            itemStyle={itemStyle}
            formatter={formatter}
            labelFormatter={labelFormatter}
          />
          <Legend wrapperStyle={LEGEND_STYLE} />
          {extensions.map((ext, i) => (
            <Line
              key={ext}
              type="monotone"
              dataKey={ext}
              stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4 }}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
});
