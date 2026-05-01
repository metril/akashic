import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useChartColors } from "../../hooks/useChartColors";
import type { ForecastResponse } from "../../hooks/useAnalyticsTimeseries";
import { formatBytes } from "../../lib/format";

interface Props {
  data: ForecastResponse;
}

export function ForecastChart({ data }: Props) {
  const c = useChartColors();

  // Combine history + forecast into a single chart series. History has
  // a `value` only; forecast points carry low/high too. The ComposedChart
  // overlays a line (value) on a shaded area (low → high) so the
  // confidence band reads visually as widening into the future.
  const history = data.history.map((p) => ({
    label: new Date(p.taken_at).toLocaleDateString(),
    history: p.value,
    value: p.value,
  }));
  const forecast = (data.forecast?.points ?? []).map((p) => ({
    label: new Date(p.taken_at).toLocaleDateString(),
    forecast: p.value,
    low: p.low,
    high: p.high,
  }));

  const combined = [...history, ...forecast];

  return (
    <div className="h-64 -mx-2">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart
          data={combined}
          margin={{ top: 8, right: 24, bottom: 8, left: 8 }}
        >
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
          {/* Solid line for the historical points */}
          <Line
            type="monotone"
            dataKey="history"
            stroke="#6366f1"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
          {/* Dashed line for the forecast */}
          <Line
            type="monotone"
            dataKey="forecast"
            stroke="#6366f1"
            strokeWidth={2}
            strokeDasharray="6 3"
            dot={false}
            isAnimationActive={false}
          />
          {/* Shaded confidence band — high–low fan */}
          <Area
            type="monotone"
            dataKey="high"
            stroke="none"
            fill="#6366f1"
            fillOpacity={0.08}
            isAnimationActive={false}
          />
          <Area
            type="monotone"
            dataKey="low"
            stroke="none"
            fill={c.tooltipBg}
            fillOpacity={1}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
