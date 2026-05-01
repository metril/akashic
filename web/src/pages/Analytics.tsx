import { useQuery } from "@tanstack/react-query";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Cell,
} from "recharts";
import { api } from "../api/client";
import type { StorageByType, StorageBySource, LargestFile, Source } from "../types";
import {
  Card,
  CardHeader,
  Select,
  Table,
  Skeleton,
  EmptyState,
  Page,
} from "../components/ui";
import type { Column } from "../components/ui";
import { formatBytes, formatNumber } from "../lib/format";
import { useMemo, useState } from "react";
import { useChartColors } from "../hooks/useChartColors";
import {
  useStorageTimeseries,
  useStorageForecast,
  useExtensionTrend,
} from "../hooks/useAnalyticsTimeseries";
import { GrowthChart } from "../components/analytics/GrowthChart";
import { ForecastChart } from "../components/analytics/ForecastChart";
import { ExtensionTrendChart } from "../components/analytics/ExtensionTrendChart";

const CHART_COLORS = [
  "#6366f1",
  "#8b5cf6",
  "#a78bfa",
  "#c4b5fd",
  "#d8b4fe",
  "#ddd6fe",
  "#e9d5ff",
];

function ChartSkeleton() {
  return <Skeleton className="h-64 w-full" />;
}

export default function Analytics() {
  const typeQuery = useQuery<StorageByType[]>({
    queryKey: ["analytics", "storage-by-type"],
    queryFn: () => api.get<StorageByType[]>("/analytics/storage-by-type"),
  });

  const sourceQuery = useQuery<StorageBySource[]>({
    queryKey: ["analytics", "storage-by-source"],
    queryFn: () =>
      api.get<StorageBySource[]>("/analytics/storage-by-source"),
  });

  const largestQuery = useQuery<LargestFile[]>({
    queryKey: ["analytics", "largest-files"],
    queryFn: () => api.get<LargestFile[]>("/analytics/largest-files"),
  });

  const sourcesQuery = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: () => api.get<Source[]>("/sources"),
  });

  const sourceMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of sourcesQuery.data ?? []) m.set(s.id, s.name);
    return m;
  }, [sourcesQuery.data]);

  // Source picker for the time-series row. Defaults to the first
  // source we know about so charts render without ceremony — admins
  // open Analytics and immediately see *something*. The dropdown is
  // hidden until at least one source exists.
  const [selectedSource, setSelectedSource] = useState<string>("");
  const effectiveSource =
    selectedSource || sourcesQuery.data?.[0]?.id || "";

  const timeseriesQuery = useStorageTimeseries(effectiveSource, "size", 90);
  const forecastQuery = useStorageForecast(effectiveSource, 30, 90);

  // Top-5 extensions globally drive the trend chart's series. Using the
  // global top-5 (rather than per-source) keeps the legend stable as the
  // user picks different sources to drill into.
  const topExtensions = useMemo(
    () =>
      (typeQuery.data ?? [])
        .filter((r) => r.extension)
        .slice(0, 5)
        .map((r) => r.extension!.toLowerCase()),
    [typeQuery.data],
  );
  const extTrendQuery = useExtensionTrend(effectiveSource, topExtensions, 90);

  const typeData = (typeQuery.data ?? [])
    .slice(0, 10)
    .map((r) => ({
      label: r.extension || "(none)",
      size: r.total_size ?? 0,
      count: r.count,
    }))
    .reverse();

  const sourceData = (sourceQuery.data ?? [])
    .slice(0, 10)
    .map((r) => ({
      label: r.source_name || r.source_id.slice(0, 8),
      size: r.total_size ?? 0,
      count: r.count,
    }))
    .reverse();

  const largestColumns: Column<LargestFile>[] = [
    {
      key: "filename",
      header: "Name",
      render: (f) => (
        <span className="font-medium text-fg">{f.filename}</span>
      ),
    },
    {
      key: "size",
      header: "Size",
      render: (f) => (
        <span className="tabular-nums text-fg">
          {formatBytes(f.size_bytes)}
        </span>
      ),
    },
    {
      key: "source",
      header: "Source",
      render: (f) => (
        <span className="text-fg-muted">
          {sourceMap.get(f.source_id) ?? f.source_id.slice(0, 8)}
        </span>
      ),
    },
    {
      key: "path",
      header: "Path",
      render: (f) => (
        <span className="text-xs text-fg-subtle font-mono break-all">
          {f.path}
        </span>
      ),
    },
  ];

  return (
    <Page
      title="Analytics"
      description="Where your storage is going, broken down by axis."
      width="wide"
    >
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mb-5">
        <Card padding="md">
          <CardHeader
            title="Storage by file type"
            description="Total size per extension."
          />
          {typeQuery.isLoading ? (
            <ChartSkeleton />
          ) : typeData.length === 0 ? (
            <EmptyState title="No data" />
          ) : (
            <ChartCard data={typeData} />
          )}
        </Card>

        <Card padding="md">
          <CardHeader
            title="Storage by source"
            description="Total size per indexed source."
          />
          {sourceQuery.isLoading ? (
            <ChartSkeleton />
          ) : sourceData.length === 0 ? (
            <EmptyState title="No data" />
          ) : (
            <ChartCard data={sourceData} />
          )}
        </Card>
      </div>

      {/* Time-series row: depends on scan_snapshots being populated.
          Sources with zero snapshots render an empty state with a hint.
          A scan-completed source gets its first snapshot immediately;
          the nightly fallback fills in days where no scan ran. */}
      {sourcesQuery.data && sourcesQuery.data.length > 0 && (
        <div className="space-y-5 mb-5">
          <Card padding="md">
            <div className="flex items-center justify-between gap-4 mb-4">
              <div>
                <h3 className="text-base font-semibold text-fg">Source insights</h3>
                <p className="text-sm text-fg-muted">
                  Storage growth, capacity forecast, and file-type trends per source.
                </p>
              </div>
              <div className="w-56">
                <Select
                  value={effectiveSource}
                  onChange={(e) => setSelectedSource(e.target.value)}
                  options={sourcesQuery.data.map((s) => ({
                    value: s.id,
                    label: s.name,
                  }))}
                />
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
              <div>
                <CardHeader
                  title="Storage growth"
                  description="Total indexed bytes over the last 90 days."
                />
                {timeseriesQuery.isLoading ? (
                  <ChartSkeleton />
                ) : (timeseriesQuery.data ?? []).length === 0 ? (
                  <EmptyState
                    title="No snapshots yet"
                    description="Trigger a scan or wait for the nightly fallback to populate the growth chart."
                  />
                ) : (
                  <GrowthChart data={timeseriesQuery.data!} metric="size" />
                )}
              </div>

              <div>
                <CardHeader
                  title="Capacity forecast"
                  description={
                    forecastQuery.data?.forecast
                      ? `${formatBytes(
                          forecastQuery.data.forecast.slope_bytes_per_day,
                        )}/day · ${forecastQuery.data.forecast.horizon_days}d horizon`
                      : "Linear projection of recent growth."
                  }
                />
                {forecastQuery.isLoading ? (
                  <ChartSkeleton />
                ) : forecastQuery.data?.forecast === null ? (
                  <EmptyState
                    title="Not enough history"
                    description="Forecast needs at least 3 snapshots; check back after a few more scans."
                  />
                ) : forecastQuery.data ? (
                  <ForecastChart data={forecastQuery.data} />
                ) : null}
              </div>
            </div>

            <div className="mt-5">
              <CardHeader
                title="File-type trends"
                description={
                  topExtensions.length > 0
                    ? `Top file types: ${topExtensions.join(", ")}`
                    : "Top file types over time."
                }
              />
              {extTrendQuery.isLoading ? (
                <ChartSkeleton />
              ) : Object.keys(extTrendQuery.data ?? {}).length === 0 ? (
                <EmptyState title="No data" />
              ) : (
                <ExtensionTrendChart data={extTrendQuery.data!} />
              )}
            </div>
          </Card>
        </div>
      )}

      <Card padding="md">
        <CardHeader
          title="Largest files"
          description="Top files by size across the index."
        />
        <Table<LargestFile>
          columns={largestColumns}
          data={largestQuery.data ?? []}
          rowKey={(f) => f.id}
          loading={largestQuery.isLoading}
          emptyTitle="No files indexed"
        />
      </Card>
    </Page>
  );
}

interface ChartDatum {
  label: string;
  size: number;
  count: number;
}

function ChartCard({ data }: { data: ChartDatum[] }) {
  const c = useChartColors();
  return (
    <div className="h-64 -mx-2">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 4, right: 24, bottom: 4, left: 8 }}
        >
          <XAxis
            type="number"
            tickFormatter={(v) => formatBytes(v)}
            stroke={c.axis}
            fontSize={11}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            type="category"
            dataKey="label"
            width={110}
            stroke={c.axisLabel}
            fontSize={12}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip
            cursor={{ fill: c.cursorFill }}
            contentStyle={{
              background: c.tooltipBg,
              border: `1px solid ${c.tooltipBorder}`,
              borderRadius: 8,
              fontSize: 13,
              boxShadow: "0 4px 12px rgba(0,0,0,0.18)",
            }}
            formatter={(value: number, _name, item) => [
              `${formatBytes(value)} · ${formatNumber(item.payload.count)} files`,
              item.payload.label,
            ]}
            labelFormatter={() => ""}
          />
          <Bar dataKey="size" radius={[0, 4, 4, 0]}>
            {data.map((_, i) => (
              <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
