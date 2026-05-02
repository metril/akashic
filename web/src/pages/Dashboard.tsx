/**
 * Dashboard — analytics homepage.
 *
 * Phase 7 rebuilds Dashboard around eight click-through tiles, each
 * landing somewhere actionable rather than just rendering decorative
 * stats. Every tile asks "is anything wrong?" or "what's growing?";
 * none of them are dead-ends.
 *
 * The full state lives in one /api/dashboard/summary round-trip — old
 * Dashboard fired four parallel queries which hit the API harder and
 * still left the user staring at four independently-loading panels.
 */
import { useQuery } from "@tanstack/react-query";
import { useNavigate, Link } from "react-router-dom";

import { api } from "../api/client";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  Icon,
  Page,
  Skeleton,
  StatCard,
} from "../components/ui";
import {
  formatBytes,
  formatNumber,
  formatRelative,
} from "../lib/format";
import { useAuth } from "../hooks/useAuth";
import { useDashboardLiveRefresh } from "../hooks/useDashboardLiveRefresh";
import { serialize as serializeFilters } from "../lib/filterGrammar";

interface DashboardSummary {
  storage: {
    total_bytes: number;
    total_files: number;
    delta_30d_bytes: number | null;
    delta_30d_files: number | null;
  };
  scans: {
    active: number;
    total_sources: number;
  };
  forecast_hints: {
    source_id: string;
    source_name: string | null;
    current_bytes: number;
    slope_bytes_per_day: number;
  }[];
  top_owners: { owner: string; n: number; bytes: number }[];
  top_extensions_growth_30d: {
    extension: string;
    delta_bytes: number;
    current_bytes: number;
  }[];
  recent_scans: {
    id: string;
    source_id: string;
    source_name: string | null;
    scan_type: string;
    status: string;
    started_at: string | null;
    completed_at: string | null;
    files_new: number;
    files_changed: number;
  }[];
  // v0.4.4: kept on the type for backwards-compat (the api still
  // returns null here), but the actual count now ships via
  // GET /dashboard/access-risks (admin-only, lazy, server-cached
  // 60s) so a busy entries-table read doesn't block the rest of
  // the summary.
  access_risks: { public_read_count: number } | null;
  identity_health: {
    unbound_count: number;
    unresolved_sid_count: number;
  };
}

interface AccessRisksResponse {
  access_risks: { public_read_count: number } | null;
  cache_age_seconds?: number;
}

export default function Dashboard() {
  const navigate = useNavigate();
  const { isAdmin } = useAuth();

  const summaryQ = useQuery<DashboardSummary>({
    queryKey: ["dashboard", "summary"],
    queryFn: () => api.get<DashboardSummary>("/dashboard/summary"),
    // v0.4.4: keep data fresh for 10s after invalidation so a burst
    // of WS events from useDashboardLiveRefresh's leading-edge
    // throttle (5s) never causes back-to-back refetches.
    staleTime: 10_000,
    refetchOnWindowFocus: false,
  });

  // v0.4.4: access_risks split off into its own lazy fetch — the
  // underlying COUNT scans a hot table during scans and was
  // bottlenecking the whole summary. 60s staleTime + server-side
  // 60s cache means at most one fetch per minute per browser.
  const accessRisksQ = useQuery<AccessRisksResponse>({
    queryKey: ["dashboard", "access-risks"],
    queryFn: () => api.get<AccessRisksResponse>("/dashboard/access-risks"),
    enabled: isAdmin,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

  // Phase-2 multi-scanner: tiles refresh on /ws/scans events instead
  // of waiting for a manual reload.
  useDashboardLiveRefresh();

  const data = summaryQ.data;
  const accessRisks = accessRisksQ.data?.access_risks ?? null;
  const loading = summaryQ.isLoading;

  return (
    <Page
      title="Dashboard"
      description="What's healthy, what's growing, what's at risk."
      width="wide"
    >
      {/* Row 1 — headline stats. Click-through to deeper context. */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        <Link to="/storage" className="contents" aria-label="Storage explorer">
          <StatCard
            label="Total storage"
            value={data ? formatBytes(data.storage.total_bytes) : "—"}
            subtext={renderDelta(data?.storage.delta_30d_bytes, "bytes")}
            loading={loading}
            icon={<Icon name="database" className="size-4" />}
            className="cursor-pointer hover:border-accent-300 transition-colors"
          />
        </Link>
        <Link to="/sources" className="contents" aria-label="Active scans">
          <StatCard
            label="Active scans"
            value={data ? formatNumber(data.scans.active) : "—"}
            subtext={data ? `${data.scans.total_sources} sources` : undefined}
            loading={loading}
            icon={<Icon name="sources" className="size-4" />}
            className="cursor-pointer hover:border-accent-300 transition-colors"
          />
        </Link>
        <Link to="/analytics" className="contents" aria-label="Files indexed">
          <StatCard
            label="Files indexed"
            value={data ? formatNumber(data.storage.total_files) : "—"}
            subtext={renderDelta(data?.storage.delta_30d_files, "files")}
            loading={loading}
            icon={<Icon name="file" className="size-4" />}
            className="cursor-pointer hover:border-accent-300 transition-colors"
          />
        </Link>
        {isAdmin && (
          <Link
            to={`/admin/access?principal=${encodeURIComponent("*")}`}
            className="contents"
            aria-label="Open access risks"
          >
            <StatCard
              label="Public-readable files"
              value={
                accessRisks
                  ? formatNumber(accessRisks.public_read_count)
                  : "—"
              }
              subtext={
                accessRisks?.public_read_count
                  ? "tap to inspect"
                  : "no risk found"
              }
              // v0.4.4: tile loads independently of the summary so a
              // slow access-risks query doesn't block storage / scans
              // / owner tiles from rendering.
              loading={accessRisksQ.isLoading}
              icon={<Icon name="shield" className="size-4" />}
              className={
                accessRisks?.public_read_count
                  ? "cursor-pointer hover:border-rose-300 transition-colors border-rose-200/50 dark:border-rose-700/30"
                  : "cursor-pointer hover:border-accent-300 transition-colors"
              }
            />
          </Link>
        )}
      </div>

      {/* Row 2 — top owners + extension growth (each routes to a
          pre-filtered Search). These are the "what's growing" signals. */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mb-5">
        <Card padding="md">
          <CardHeader
            title="Top owners by size"
            description="Who owns the most data — click to see their files in Search."
          />
          {loading ? (
            <Skeleton className="h-40" />
          ) : (data?.top_owners ?? []).length === 0 ? (
            <EmptyState
              title="No data yet"
              description="Owners appear here after the first scan completes."
            />
          ) : (
            <ul className="space-y-1.5">
              {data!.top_owners.map((o) => (
                <li key={o.owner}>
                  <button
                    type="button"
                    onClick={() => navigate(ownerSearchHref(o.owner))}
                    className="w-full flex items-center gap-3 px-2 py-1.5 rounded-md hover:bg-surface-muted text-left transition-colors"
                  >
                    <span className="font-medium text-fg truncate flex-1 min-w-0">
                      {o.owner}
                    </span>
                    <span className="text-xs text-fg-muted tabular-nums flex-shrink-0">
                      {formatNumber(o.n)} files
                    </span>
                    <span className="text-sm font-medium text-fg tabular-nums tabular-nums w-20 text-right">
                      {formatBytes(o.bytes)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card padding="md">
          <CardHeader
            title="Extensions growing fastest"
            description="Top file types by 30d size growth."
          />
          {loading ? (
            <Skeleton className="h-40" />
          ) : (data?.top_extensions_growth_30d ?? []).length === 0 ? (
            <EmptyState
              title="No growth signal yet"
              description="Need at least 30 days of snapshot history to compute growth."
            />
          ) : (
            <ul className="space-y-1.5">
              {data!.top_extensions_growth_30d.map((e) => (
                <li key={e.extension}>
                  <button
                    type="button"
                    onClick={() => navigate(extensionSearchHref(e.extension))}
                    className="w-full flex items-center gap-3 px-2 py-1.5 rounded-md hover:bg-surface-muted text-left transition-colors"
                  >
                    <Badge variant="neutral" className="flex-shrink-0">.{e.extension}</Badge>
                    <span className="text-xs text-fg-muted tabular-nums flex-shrink-0">
                      now {formatBytes(e.current_bytes)}
                    </span>
                    <span className="text-sm font-medium text-emerald-700 tabular-nums ml-auto">
                      +{formatBytes(e.delta_bytes)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* Row 3 — forecast hints + recent scans. */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mb-5">
        <Card padding="md">
          <CardHeader
            title="Capacity forecast"
            description="Daily growth rate per top-3 source — open Analytics for the full forecast."
          />
          {loading ? (
            <Skeleton className="h-32" />
          ) : (data?.forecast_hints ?? []).length === 0 ? (
            <EmptyState
              title="Not enough history"
              description="Forecast hints appear once a source has at least 30 days of snapshots."
            />
          ) : (
            <ul className="space-y-2">
              {data!.forecast_hints.map((h) => (
                <li
                  key={h.source_id}
                  className="flex items-center gap-3 px-2 py-1.5 rounded-md"
                >
                  <span className="font-medium text-fg truncate flex-1 min-w-0">
                    {h.source_name ?? h.source_id.slice(0, 8)}
                  </span>
                  <span className="text-xs text-fg-muted tabular-nums">
                    {formatBytes(h.current_bytes)}
                  </span>
                  <span
                    className={
                      "text-sm tabular-nums w-28 text-right font-medium " +
                      (h.slope_bytes_per_day >= 0
                        ? "text-emerald-700"
                        : "text-rose-700")
                    }
                  >
                    {h.slope_bytes_per_day >= 0 ? "+" : "−"}
                    {formatBytes(Math.abs(h.slope_bytes_per_day))}/day
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card padding="none">
          <div className="px-5 pt-5 pb-3">
            <CardHeader
              title="Recent scans"
              description="Last 6 across visible sources."
              className="mb-0"
            />
          </div>
          {loading ? (
            <div className="space-y-1 px-3 pb-3">
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
            </div>
          ) : (data?.recent_scans ?? []).length === 0 ? (
            <EmptyState
              title="No scans yet"
              description="Trigger a scan from the Sources page."
              action={
                <Button size="sm" onClick={() => navigate("/sources")}>
                  Open Sources
                </Button>
              }
            />
          ) : (
            <ul className="divide-y divide-line-subtle">
              {data!.recent_scans.map((s) => (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => navigate(`/sources?open=${s.source_id}`)}
                    className="w-full px-4 py-2 hover:bg-surface-muted/60 text-left transition-colors"
                  >
                    <div className="flex items-baseline justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-fg truncate">
                            {s.source_name ?? s.source_id.slice(0, 8)}
                          </span>
                          <Badge variant="neutral">{s.scan_type}</Badge>
                        </div>
                        <div className="text-xs text-fg-muted mt-0.5">
                          {scanWhen(s)}
                          {s.files_new > 0 && (
                            <> · <span className="text-emerald-700 font-medium">+{formatNumber(s.files_new)}</span> files</>
                          )}
                        </div>
                      </div>
                      <Badge variant={scanVariant(s.status)}>{s.status}</Badge>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* Row 4 — identities health (admin sees global; users see own). */}
      <Card padding="md">
        <CardHeader
          title="Identities"
          description="Linkage between Akashic users and on-source principals."
        />
        {loading ? (
          <Skeleton className="h-16" />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <button
              type="button"
              onClick={() => navigate("/settings/identities")}
              className="flex items-center gap-3 px-3 py-2.5 rounded-md hover:bg-surface-muted text-left transition-colors"
            >
              <span className="flex-1 text-sm">
                <span className="block text-xs text-fg-muted">Unbound identities</span>
                <span className="text-fg font-medium">
                  {formatNumber(data!.identity_health.unbound_count)}
                </span>
              </span>
              <span className="text-xs text-accent-700">Manage →</span>
            </button>
            {isAdmin && (
              <button
                type="button"
                onClick={() => navigate("/settings/identities")}
                className="flex items-center gap-3 px-3 py-2.5 rounded-md hover:bg-surface-muted text-left transition-colors"
              >
                <span className="flex-1 text-sm">
                  <span className="block text-xs text-fg-muted">Unresolved SIDs</span>
                  <span className="text-fg font-medium">
                    {formatNumber(data!.identity_health.unresolved_sid_count)}
                  </span>
                </span>
                <span className="text-xs text-accent-700">Open →</span>
              </button>
            )}
          </div>
        )}
      </Card>
    </Page>
  );
}

function renderDelta(delta: number | null | undefined, kind: "bytes" | "files"): string | undefined {
  if (delta == null) return undefined;
  const fmt = kind === "bytes" ? formatBytes : formatNumber;
  if (delta === 0) return "no change in 30d";
  const sign = delta > 0 ? "+" : "−";
  return `${sign}${fmt(Math.abs(delta))} in 30d`;
}

function scanWhen(s: { started_at: string | null; completed_at: string | null }): string {
  if (s.completed_at) return formatRelative(s.completed_at);
  if (s.started_at) return `started ${formatRelative(s.started_at)}`;
  return "queued";
}

function scanVariant(status: string): "online" | "scanning" | "failed" | "neutral" {
  if (status === "completed") return "online";
  if (status === "running" || status === "pending") return "scanning";
  if (status === "failed") return "failed";
  return "neutral";
}

function ownerSearchHref(owner: string): string {
  // Owner predicate filters Search to entries whose owner_name matches.
  // The Phase-6 grammar handles base64url + escape edge cases.
  const params = serializeFilters([{ kind: "owner", value: owner }]);
  return `/search?filters=${params}`;
}

function extensionSearchHref(ext: string): string {
  const params = serializeFilters([{ kind: "extension", value: ext }]);
  return `/search?filters=${params}`;
}
