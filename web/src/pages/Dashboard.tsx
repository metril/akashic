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
import type { Source, StorageByType } from "../types";
import { Card, CardHeader, StatCard, EmptyState, Icon } from "../components/ui";
import { formatBytes, formatNumber } from "../lib/format";

const CHART_COLORS = [
  "#6366f1",
  "#8b5cf6",
  "#a78bfa",
  "#c4b5fd",
  "#d8b4fe",
  "#e9d5ff",
];

export default function Dashboard() {
  const sourcesQuery = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: () => api.get<Source[]>("/sources"),
  });

  const storageQuery = useQuery<StorageByType[]>({
    queryKey: ["analytics", "storage-by-type"],
    queryFn: () => api.get<StorageByType[]>("/analytics/storage-by-type"),
  });

  const sources = sourcesQuery.data ?? [];
  const storageByType = storageQuery.data ?? [];

  const totalFiles = storageByType.reduce((s, r) => s + r.count, 0);
  const totalSize = storageByType.reduce((s, r) => s + (r.total_size ?? 0), 0);
  const activeSources = sources.filter(
    (s) => s.status === "online" || s.status === "scanning",
  ).length;

  const chartData = storageByType
    .slice(0, 10)
    .map((r) => ({
      extension: r.extension || "(none)",
      size: r.total_size ?? 0,
      count: r.count,
    }))
    .reverse();

  const isLoading = sourcesQuery.isLoading || storageQuery.isLoading;

  return (
    <div className="px-8 py-7 max-w-7xl">
      <div className="mb-7">
        <h1 className="text-2xl font-semibold text-gray-900 tracking-tight">
          Dashboard
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Overview of your indexed file archive.
        </p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard
          label="Sources"
          value={formatNumber(sources.length)}
          subtext={`${activeSources} active`}
          loading={sourcesQuery.isLoading}
          icon={<Icon name="sources" className="size-4" />}
        />
        <StatCard
          label="Files indexed"
          value={formatNumber(totalFiles)}
          loading={storageQuery.isLoading}
          icon={<Icon name="file" className="size-4" />}
        />
        <StatCard
          label="Total storage"
          value={formatBytes(totalSize)}
          loading={storageQuery.isLoading}
          icon={<Icon name="database" className="size-4" />}
        />
        <StatCard
          label="File types"
          value={formatNumber(storageByType.length)}
          loading={storageQuery.isLoading}
          icon={<Icon name="box" className="size-4" />}
        />
      </div>

      <Card padding="md">
        <CardHeader
          title="Storage by file type"
          description="Top 10 extensions by total size"
        />
        {isLoading ? (
          <div className="h-72 animate-pulse bg-gray-100 rounded-md" />
        ) : chartData.length === 0 ? (
          <EmptyState
            title="No files indexed yet"
            description="Add a source and trigger a scan to see storage breakdowns."
          />
        ) : (
          <div className="h-72 -mx-2">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={chartData}
                layout="vertical"
                margin={{ top: 8, right: 24, bottom: 8, left: 8 }}
              >
                <XAxis
                  type="number"
                  tickFormatter={(v) => formatBytes(v)}
                  stroke="#9ca3af"
                  fontSize={11}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="extension"
                  width={70}
                  stroke="#6b7280"
                  fontSize={12}
                  tickLine={false}
                  axisLine={false}
                />
                <Tooltip
                  cursor={{ fill: "rgba(99,102,241,0.06)" }}
                  contentStyle={{
                    background: "white",
                    border: "1px solid #e5e7eb",
                    borderRadius: 8,
                    fontSize: 13,
                    boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
                  }}
                  formatter={(value: number, _name, item) => [
                    `${formatBytes(value)} · ${formatNumber(item.payload.count)} files`,
                    item.payload.extension,
                  ]}
                  labelFormatter={() => ""}
                />
                <Bar dataKey="size" radius={[0, 4, 4, 0]}>
                  {chartData.map((_, i) => (
                    <Cell
                      key={i}
                      fill={CHART_COLORS[i % CHART_COLORS.length]}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </Card>
    </div>
  );
}
