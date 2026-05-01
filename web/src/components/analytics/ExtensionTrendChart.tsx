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

import { useChartColors } from "../../hooks/useChartColors";
import type { ExtensionTrendPoint } from "../../hooks/useAnalyticsTimeseries";
import { formatBytes } from "../../lib/format";

const SERIES_COLORS = ["#6366f1", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"];

interface Props {
  data: Record<string, ExtensionTrendPoint[]>;
}

export function ExtensionTrendChart({ data }: Props) {
  const c = useChartColors();
  const extensions = Object.keys(data);

  // Pivot wide: one row per timestamp, one column per extension's bytes.
  // Recharts' multi-line layout wants a single `data` array with named
  // series rather than separate datasets, so this re-shape is necessary.
  type Row = { label: string; [series: string]: string | number };
  const byDate = new Map<string, Row>();
  for (const ext of extensions) {
    for (const p of data[ext]) {
      const key = p.taken_at;
      const row =
        byDate.get(key) ?? ({ label: new Date(key).toLocaleDateString() } as Row);
      row[ext] = p.bytes;
      byDate.set(key, row);
    }
  }
  const points = [...byDate.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([, row]) => row);

  return (
    <div className="h-64 -mx-2">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 8, right: 24, bottom: 8, left: 8 }}>
          <CartesianGrid stroke={c.tooltipBorder} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" stroke={c.axis} fontSize={11} tickLine={false} axisLine={false} />
          <YAxis
            stroke={c.axis}
            fontSize={11}
            tickFormatter={(v) => formatBytes(v)}
            tickLine={false}
            axisLine={false}
            width={70}
          />
          <Tooltip
            contentStyle={{
              background: c.tooltipBg,
              border: `1px solid ${c.tooltipBorder}`,
              borderRadius: 8,
              fontSize: 13,
              boxShadow: "0 4px 12px rgba(0,0,0,0.18)",
            }}
            formatter={(v: number, n: string) => [formatBytes(v), n]}
            labelFormatter={(l) => l}
          />
          <Legend wrapperStyle={{ fontSize: 11, paddingTop: 8 }} />
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
}
