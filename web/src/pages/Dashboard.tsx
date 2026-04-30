import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
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
import type { Scan, Source, StorageByType } from "../types";
import {
  Card,
  CardHeader,
  StatCard,
  EmptyState,
  Icon,
  Page,
  Badge,
  Button,
  Skeleton,
} from "../components/ui";
import {
  formatBytes,
  formatDuration,
  formatNumber,
  formatRelative,
} from "../lib/format";
import { useRecentScans } from "../hooks/useRecentScans";
import { useChartColors } from "../hooks/useChartColors";

// Distinct hues so the chart reads as categorical (which it is — file
// extensions are unordered) rather than ordinal. The original single-
// purple gradient looked like a heatmap.
const CHART_COLORS = [
  "#6366f1", // indigo
  "#10b981", // emerald
  "#f59e0b", // amber
  "#ef4444", // rose
  "#8b5cf6", // violet
  "#06b6d4", // cyan
  "#ec4899", // pink
  "#84cc16", // lime
  "#f97316", // orange
  "#0ea5e9", // sky
];

// Tailwind class used for the round status dot in the source-health
// row. Keeps colors aligned with the existing Badge variant set without
// re-importing the Badge component for a 6 px circle.
const STATUS_DOT: Record<string, string> = {
  online: "bg-emerald-500",
  scanning: "bg-accent-500",
  failed: "bg-rose-500",
  offline: "bg-gray-400",
};

interface SourceRowProps {
  source: Source;
  onOpen: (id: string) => void;
}

function SourceHealthRow({ source, onOpen }: SourceRowProps) {
  const dot = STATUS_DOT[source.status] ?? "bg-gray-300";
  return (
    <button
      type="button"
      onClick={() => onOpen(source.id)}
      className="w-full flex items-center gap-3 px-3 py-2 rounded-md hover:bg-surface-muted text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
    >
      <span
        className={`h-2 w-2 rounded-full flex-shrink-0 ${dot} ${source.status === "scanning" ? "animate-pulse" : ""}`}
        aria-label={source.status}
      />
      <span className="font-medium text-fg truncate flex-1 min-w-0">
        {source.name}
      </span>
      <span className="text-xs text-fg-muted uppercase tracking-wide flex-shrink-0">
        {source.type}
      </span>
      <span
        className="text-xs text-fg-muted tabular-nums flex-shrink-0 w-24 text-right"
        title={source.last_scan_at ?? "never"}
      >
        {source.last_scan_at ? formatRelative(source.last_scan_at) : "never"}
      </span>
    </button>
  );
}

interface RecentScanRowProps {
  scan: Scan;
  sourceName: string | undefined;
}

function RecentScanRow({ scan, sourceName }: RecentScanRowProps) {
  const finished = scan.completed_at;
  const started = scan.started_at;
  const duration =
    finished && started
      ? (new Date(finished).getTime() - new Date(started).getTime()) / 1000
      : null;

  // Status → Badge variant mapping. Some api statuses (running, pending)
  // are normal; others (failed) get a destructive variant.
  let variant: "online" | "scanning" | "failed" | "neutral" = "neutral";
  if (scan.status === "completed") variant = "online";
  else if (scan.status === "running" || scan.status === "pending")
    variant = "scanning";
  else if (scan.status === "failed") variant = "failed";

  return (
    <li
      className="px-3 py-2 hover:bg-surface-muted/60 transition-colors"
      title={scan.error_message ?? undefined}
    >
      <div className="flex items-baseline justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-medium text-fg truncate">
              {sourceName ?? scan.source_id.slice(0, 8)}
            </span>
            <Badge variant="neutral">{scan.scan_type}</Badge>
          </div>
          <div className="text-xs text-fg-muted mt-0.5">
            {finished
              ? formatRelative(finished)
              : started
                ? `started ${formatRelative(started)}`
                : "queued"}
            {scan.files_new > 0 && (
              <>
                {" · "}
                <span className="text-emerald-700 font-medium">
                  +{formatNumber(scan.files_new)}
                </span>{" "}
                files
              </>
            )}
            {duration !== null && (
              <> · {formatDuration(duration)}</>
            )}
          </div>
        </div>
        <Badge variant={variant}>{scan.status}</Badge>
      </div>
    </li>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();
  const chartColors = useChartColors();

  const sourcesQuery = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: () => api.get<Source[]>("/sources"),
  });

  const storageQuery = useQuery<StorageByType[]>({
    queryKey: ["analytics", "storage-by-type"],
    queryFn: () => api.get<StorageByType[]>("/analytics/storage-by-type"),
  });

  const recentScansQuery = useRecentScans(6);

  const sources = sourcesQuery.data ?? [];
  const storageByType = storageQuery.data ?? [];
  const recentScans = recentScansQuery.data ?? [];

  // Source name lookup for the recent-activity rows.
  const sourceNameById = new Map(sources.map((s) => [s.id, s.name]));

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

  const chartLoading = sourcesQuery.isLoading || storageQuery.isLoading;

  // Sort sources for health row: failed > scanning > online > offline,
  // then alphabetical. Surfaces problems first.
  const STATUS_ORDER: Record<string, number> = {
    failed: 0,
    scanning: 1,
    online: 2,
    offline: 3,
  };
  const sortedSources = [...sources].sort((a, b) => {
    const so =
      (STATUS_ORDER[a.status] ?? 99) - (STATUS_ORDER[b.status] ?? 99);
    return so !== 0 ? so : a.name.localeCompare(b.name);
  });

  return (
    <Page
      title="Dashboard"
      description="What's healthy, what's running, and where your storage went."
      width="wide"
    >
      {/* Stat strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
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

      {/* Source health row */}
      <Card padding="sm" className="mb-5">
        <div className="flex items-baseline justify-between mb-2 px-3 pt-1">
          <h2 className="text-base font-semibold text-fg">
            Source health
          </h2>
          <button
            type="button"
            onClick={() => navigate("/sources")}
            className="text-xs text-accent-700 hover:text-accent-600 font-medium"
          >
            Manage →
          </button>
        </div>
        {sourcesQuery.isLoading ? (
          <div className="space-y-1.5 px-3 pb-2">
            <Skeleton className="h-9" />
            <Skeleton className="h-9" />
            <Skeleton className="h-9" />
          </div>
        ) : sortedSources.length === 0 ? (
          <EmptyState
            title="No sources yet"
            description="Add a source to start indexing."
            action={
              <Button size="sm" onClick={() => navigate("/sources")}>
                Add a source
              </Button>
            }
          />
        ) : (
          <div className="space-y-0.5">
            {sortedSources.map((s) => (
              <SourceHealthRow
                key={s.id}
                source={s}
                onOpen={(id) => navigate(`/sources?open=${id}`)}
              />
            ))}
          </div>
        )}
      </Card>

      {/* Two-column row: chart + recent activity */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <Card padding="md">
          <CardHeader
            title="Storage by file type"
            description="Top 10 extensions, by total size."
          />
          {chartLoading ? (
            <Skeleton className="h-72" />
          ) : chartData.length === 0 ? (
            <EmptyState
              title="No files indexed yet"
              description="Add a source and trigger a scan to see storage breakdowns."
              action={
                <Button size="sm" onClick={() => navigate("/sources")}>
                  Open Sources
                </Button>
              }
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
                    stroke={chartColors.axis}
                    fontSize={11}
                    tickLine={false}
                    axisLine={false}
                  />
                  <YAxis
                    type="category"
                    dataKey="extension"
                    width={70}
                    stroke={chartColors.axisLabel}
                    fontSize={12}
                    tickLine={false}
                    axisLine={false}
                  />
                  <Tooltip
                    cursor={{ fill: chartColors.cursorFill }}
                    contentStyle={{
                      background: chartColors.tooltipBg,
                      border: `1px solid ${chartColors.tooltipBorder}`,
                      borderRadius: 8,
                      fontSize: 13,
                      boxShadow: "0 4px 12px rgba(0,0,0,0.18)",
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

        <Card padding="none">
          <div className="px-5 pt-5 pb-3">
            <CardHeader
              title="Recent activity"
              description="Last 6 scans across all sources."
              className="mb-0"
            />
          </div>
          {recentScansQuery.isLoading ? (
            <div className="space-y-1 px-3 pb-3">
              <Skeleton className="h-12" />
              <Skeleton className="h-12" />
              <Skeleton className="h-12" />
            </div>
          ) : recentScans.length === 0 ? (
            <EmptyState
              title="No scans yet"
              description="Trigger a scan from the Sources page to see activity here."
              action={
                <Button size="sm" onClick={() => navigate("/sources")}>
                  Open Sources
                </Button>
              }
            />
          ) : (
            <ul className="divide-y divide-line-subtle">
              {recentScans.map((scan) => (
                <RecentScanRow
                  key={scan.id}
                  scan={scan}
                  sourceName={sourceNameById.get(scan.source_id)}
                />
              ))}
            </ul>
          )}
        </Card>
      </div>
    </Page>
  );
}
