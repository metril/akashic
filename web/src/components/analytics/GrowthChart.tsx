import { memo, useCallback, useMemo } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { TimeseriesPoint } from "../../hooks/useAnalyticsTimeseries";
import { formatBytes, formatNumber } from "../../lib/format";
import { useChartTooltipStyle } from "./chartTooltipStyle";

interface Props {
  data: TimeseriesPoint[];
  metric: "size" | "count";
}

export const GrowthChart = memo(function GrowthChart({ data, metric }: Props) {
  const { contentStyle, labelStyle, itemStyle, colors: c } =
    useChartTooltipStyle();

  // Memo the per-point label transform so a parent re-render that
  // doesn't actually mutate `data` doesn't reshape the array (which
  // would defeat recharts' diffing further down).
  const points = useMemo(
    () =>
      data.map((p) => ({
        ...p,
        label: new Date(p.taken_at).toLocaleDateString(),
      })),
    [data],
  );

  const fmt = metric === "size" ? formatBytes : formatNumber;
  const seriesName = metric === "size" ? "Size" : "Files";
  const tickFormatter = useCallback((v: number) => fmt(v), [fmt]);
  const formatter = useCallback(
    (v: number) => [fmt(v), seriesName] as [string, string],
    [fmt, seriesName],
  );
  const labelFormatter = useCallback((l: string) => l, []);

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
          <Line
            type="monotone"
            dataKey="value"
            stroke="#6366f1"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
});
