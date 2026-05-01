import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useChartColors } from "../../hooks/useChartColors";
import type { TimeseriesPoint } from "../../hooks/useAnalyticsTimeseries";
import { formatBytes, formatNumber } from "../../lib/format";

interface Props {
  data: TimeseriesPoint[];
  metric: "size" | "count";
}

export function GrowthChart({ data, metric }: Props) {
  const c = useChartColors();
  const points = data.map((p) => ({
    ...p,
    label: new Date(p.taken_at).toLocaleDateString(),
  }));
  const fmt = metric === "size" ? formatBytes : formatNumber;

  return (
    <div className="h-64 -mx-2">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={points}
          margin={{ top: 8, right: 24, bottom: 8, left: 8 }}
        >
          <CartesianGrid stroke={c.tooltipBorder} strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="label"
            stroke={c.axis}
            fontSize={11}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            stroke={c.axis}
            fontSize={11}
            tickFormatter={(v) => fmt(v)}
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
            formatter={(v: number) => [fmt(v), metric === "size" ? "Size" : "Files"]}
            labelFormatter={(l) => l}
          />
          <Line
            type="monotone"
            dataKey="value"
            stroke="#6366f1"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
