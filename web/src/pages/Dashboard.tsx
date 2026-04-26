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
import { Card, CardHeader, StatCard, EmptyState } from "../components/ui";
import { formatBytes, formatNumber } from "../lib/format";

const CHART_COLORS = [
  "#6366f1",
  "#8b5cf6",
  "#a78bfa",
  "#c4b5fd",
  "#d8b4fe",
  "#e9d5ff",
];

const PageIcon = ({ d }: { d: string }) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className="h-4 w-4"
  >
    <path d={d} />
  </svg>
);

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
          icon={<PageIcon d="M3 7h18M3 12h18M3 17h18" />}
        />
        <StatCard
          label="Files indexed"
          value={formatNumber(totalFiles)}
          loading={storageQuery.isLoading}
          icon={
            <PageIcon d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8l-6-6zM14 2v6h6" />
          }
        />
        <StatCard
          label="Total storage"
          value={formatBytes(totalSize)}
          loading={storageQuery.isLoading}
          icon={
            <PageIcon d="M22 12H2M22 12a10 10 0 01-20 0M22 12a10 10 0 00-20 0" />
          }
        />
        <StatCard
          label="File types"
          value={formatNumber(storageByType.length)}
          loading={storageQuery.isLoading}
          icon={
            <PageIcon d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z" />
          }
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
