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
  Table,
  Skeleton,
  EmptyState,
  Page,
} from "../components/ui";
import type { Column } from "../components/ui";
import { formatBytes, formatNumber } from "../lib/format";
import { useMemo } from "react";
import { useChartColors } from "../hooks/useChartColors";

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
