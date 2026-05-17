import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Link } from "react-router-dom";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useSources, useSourceDetail } from "../hooks/useSources";
import { useActiveScanForSource, useOpenScanForSource } from "../hooks/useScansStream";
import { useSourceStatusReconciler } from "../hooks/useSourceStatusReconciler";
import { useScannerSummary } from "../hooks/useScannerSummary";
import {
  Button,
  Card,
  Badge,
  ConfirmDialog,
  Skeleton,
  EmptyState,
  Page,
} from "../components/ui";
import { useBulkTriggerScans } from "../hooks/useScanActions";
import type { Scan, Source } from "../types";
import { computeETA, formatDateTime, formatDuration, formatNumber, formatRelative } from "../lib/format";
import { deriveSourcePill, formatSourceSummary, type SourcePillState } from "../lib/sources";
import { BucketSecurityCard } from "../components/acl/BucketSecurityCard";
import { AddSourceForm } from "../components/sources/AddSourceForm";
import { ReachabilityBadge } from "../components/sources/ReachabilityBadge";
import { ScanLogPanel } from "../components/scans/ScanLogPanel";
import { SourceDetail } from "../components/sources/SourceDetail";
import { HostHeader } from "../components/sources/HostHeader";
import {
  buildGroupedRows,
  buildUngroupedRows,
  readGroupByPref,
  readHostCollapsed,
  writeGroupByPref,
  writeHostCollapsed,
  type GroupBy,
  type SourceRow,
} from "../lib/sourcesGrouping";
import { api } from "../api/client";

function SourcePill({ state }: { state: SourcePillState }) {
  switch (state.kind) {
    case "scanning":
      return <Badge variant="scanning">Scanning</Badge>;
    case "queued":
      return <Badge variant="neutral">Queued</Badge>;
    case "failed":
      return <Badge variant="failed">Failed</Badge>;
    case "lastScanned":
      return (
        <Badge variant="neutral" title={`Last scanned ${formatDateTime(state.at)}`}>
          Last scanned {formatRelative(state.at)}
        </Badge>
      );
    case "neverScanned":
      return (
        <Badge variant="neutral" className="opacity-70">
          Never scanned
        </Badge>
      );
  }
}

interface SourceCardProps {
  source: Source;
  onOpen: (id: string) => void;
  onOpenLog: (scanId: string) => void;
}

const SourceCard = memo(function SourceCard({ source, onOpen, onOpenLog }: SourceCardProps) {
  // Per-source slice subscription (v0.4.5) — re-renders ONLY when
  // this source's scan changes. A scan.state event for a different
  // source flips no listeners on this card.
  const activeScan = useActiveScanForSource(source.id);

  const summary = useMemo(() => formatSourceSummary(source), [source]);
  const isScanning = source.status === "scanning";
  // Phase-2 multi-scanner: a source can have a queued scan that no
  // agent has claimed yet. Distinct from "scanning" (agent in flight)
  // so the user can tell why nothing's happening.
  const isQueued = !isScanning && activeScan?.status === "pending";
  const [stopping, setStopping] = useState(false);

  const handleStop = useCallback(async () => {
    if (!activeScan) return;
    if (stopping) return;
    setStopping(true);
    const p = api.cancelScan(activeScan.id);
    toast.promise(p, {
      loading: "Stopping scan…",
      success: "Scan stopped.",
      error: (e: unknown) =>
        `Couldn't stop scan: ${e instanceof Error ? e.message : "unknown error"}`,
    });
    try {
      await p;
      // v0.4.7: removed invalidateQueries — /scans/{id}/cancel
      // publishes a scan.state event with scan_status="cancelled"
      // and source_status="online"; the singleton store + reconciler
      // pick those up and the card snaps to "online" within one WS
      // frame without a refetch round-trip.
    } catch {
      // Toast already surfaced the error.
    } finally {
      setStopping(false);
    }
  }, [activeScan, stopping]);

  // Compose progress subtitle for in-flight scans. Memoized on the
  // fields that drive the visible string so identical-shape events
  // don't recompute it.
  const progressLine = useMemo<ProgressLine | null>(
    () => (isScanning && activeScan ? buildProgressLine(activeScan) : null),
    [
      isScanning,
      activeScan?.id,
      activeScan?.files_found,
      activeScan?.current_path,
      activeScan?.phase,
      activeScan?.total_estimated,
      activeScan?.previous_scan_files,
      activeScan?.started_at,
    ],
  );

  // Show watchdog/error message for failed scans on the previous run.
  const errorMessage =
    source.status === "failed" && activeScan?.error_message
      ? activeScan.error_message
      : null;

  const handleClick = useCallback(() => onOpen(source.id), [onOpen, source.id]);

  return (
    <Card padding="md" className="flex flex-col">
      <button
        type="button"
        onClick={handleClick}
        className="text-left flex flex-col grow rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-1"
      >
        <div className="flex items-start justify-between gap-3 mb-1">
          <h3 className="text-base font-semibold text-fg truncate">
            {source.name}
          </h3>
          <SourcePill state={deriveSourcePill(source, isQueued)} />
        </div>
        <p className="text-xs text-fg-muted break-all mb-3">{summary}</p>
        {isQueued && (
          <div className="mb-3 rounded-md bg-amber-50 border border-amber-100 dark:bg-amber-500/10 dark:border-amber-500/30 px-2.5 py-2">
            <p className="text-xs text-amber-900 dark:text-amber-200">
              Waiting for a scanner to claim this scan.
            </p>
          </div>
        )}

        {progressLine && (
          // v0.4.11: pinned min-h so the row's outer height stays
          // constant whether or not current_path is populated. Without
          // this, the path slot toggled on/off between heartbeat events
          // and the row's height oscillated by ~16px, causing the
          // virtualizer's ResizeObserver to fire on every scan.state
          // event — which contended with cursor input on the open
          // Drawer and produced the hover stutter.
          <div className="mb-3 rounded-md bg-blue-50 border border-blue-100 dark:bg-blue-500/10 dark:border-blue-500/30 px-2.5 py-2 min-h-[3.25rem]">
            <p className="text-xs text-blue-900 font-medium truncate">{progressLine.summary}</p>
            {/* Always-rendered path slot. Non-breaking-space fallback
                keeps the line height stable when current_path is null
                (e.g., during prewalk before the scanner has descended). */}
            <p className="text-[11px] text-blue-700 font-mono mt-0.5 truncate min-h-[1rem]">
              {progressLine.currentPath ?? " "}
            </p>
          </div>
        )}

        {errorMessage && (
          <div className="mb-3 rounded-md bg-rose-50 border border-rose-100 dark:bg-rose-500/10 dark:border-rose-500/30 px-2.5 py-2">
            <p className="text-xs text-rose-800 font-medium">Last scan failed</p>
            <p className="text-[11px] text-rose-700 dark:text-rose-300 mt-0.5">{errorMessage}</p>
          </div>
        )}

        <dl className="text-xs text-fg-muted space-y-1 mt-auto">
          <div className="flex gap-2">
            <dt className="text-fg-subtle">Type</dt>
            <dd>{source.type}</dd>
          </div>
          {/* "Last scan" row removed — the pill in the title carries the
              relative time, with an absolute-timestamp tooltip. v0.5.9 */}
          <div className="flex gap-2 pt-0.5">
            <dt className="text-fg-subtle">Reachability</dt>
            <dd className="min-w-0">
              <ReachabilityBadge sourceId={source.id} compact />
            </dd>
          </div>
        </dl>
      </button>

      {/* Live-log shortcut stays on the card so users don't have to
          open the drawer just to peek at progress. Other actions
          (edit, scan now, delete) live inside the drawer to keep the
          card minimal. */}
      {isScanning && activeScan && (
        <div className="mt-3 pt-2 border-t border-line-subtle flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={(e) => {
              e.stopPropagation();
              onOpenLog(activeScan.id);
            }}
            aria-label="View live scan log"
          >
            View live log
          </Button>
          <Button
            size="sm"
            variant="danger"
            onClick={(e) => {
              e.stopPropagation();
              handleStop();
            }}
            loading={stopping}
          >
            Stop scan
          </Button>
        </div>
      )}

      {source.type === "s3" && <BucketSecurityCard source={source} />}
    </Card>
  );
});

interface ProgressLine {
  summary: string;
  currentPath: string | null;
}

function buildProgressLine(scan: Scan): ProgressLine {
  const filesScanned = scan.files_found ?? 0;
  const eta = computeETA(
    filesScanned,
    scan.total_estimated,
    scan.previous_scan_files,
    scan.started_at,
  );

  let summary: string;
  if (scan.phase === "prewalk") {
    const counted = scan.total_estimated ?? 0;
    summary = `Estimating tree size: ${formatNumber(counted)} files counted…`;
  } else if (eta) {
    summary = `${formatNumber(filesScanned)} / ~${formatNumber(eta.total)} files · ETA ${formatDuration(eta.etaSeconds)}`;
  } else {
    summary = `${formatNumber(filesScanned)} files scanned`;
  }

  return {
    summary,
    currentPath: scan.current_path ?? null,
  };
}

/**
 * Virtualized vertical list of source cards. Renders only the cards
 * visible in the scroll viewport (+ a small overscan), so mount cost
 * is constant regardless of how many sources the install has. v0.4.3.
 *
 * Why a vertical list instead of the prior 2-column grid: 2D
 * virtualization is meaningfully more code, and the typical
 * Sources-page width is wide enough that single-column actually
 * scans easier (each card has more horizontal room for badges +
 * progress strip). If the user pushback on this, switch to a
 * 2D virtualizer pattern (~30 more LOC).
 */
function VirtualSourceList({
  rows, onOpen, onOpenLog, onToggleHost,
}: {
  rows: SourceRow[];
  onOpen: (id: string) => void;
  onOpenLog: (scanId: string) => void;
  /** Called when a host header's chevron is clicked (group-by-host
   *  mode only). Pre-grouped row streams already reflect the new
   *  collapsed state on the next render via the parent's `collapsed`
   *  Set, so this just updates persistence. */
  onToggleHost: (hostKey: string) => void;
}) {
  const parentRef = useRef<HTMLDivElement>(null);
  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    // Headers are tight (~36px); cards run ~120-168px depending on
    // the optional progress strip. measureElement auto-corrects the
    // initial estimate after mount so the per-kind switch is just an
    // optimisation, not a correctness requirement.
    estimateSize: (i) => (rows[i]?.kind === "header" ? 36 : 168),
    overscan: 4,
    // v0.5.5: track measurements by row.key (e.g. "card:<src-id>",
    // "header:<host-id>") rather than by integer index. Without this,
    // toggling Group-by Host ↔ None reshuffles the rows array but the
    // virtualizer keeps the *index*-keyed cached size — a header's
    // 36px gets re-used for whatever card slots into index 0 next,
    // visually collapsing all the cards on top of each other.
    getItemKey: (i) => rows[i]?.key ?? `__idx_${i}`,
  });

  return (
    <div
      ref={parentRef}
      className="overflow-auto rounded-lg"
      // Fill the available column height. Page chrome + filters
      // + the header take ~280px on a typical viewport.
      style={{ height: "calc(100vh - 280px)", minHeight: "400px" }}
    >
      <div
        style={{
          height: rowVirtualizer.getTotalSize(),
          position: "relative",
        }}
      >
        {rowVirtualizer.getVirtualItems().map((vrow) => {
          const r = rows[vrow.index];
          return (
            <div
              key={r.key}
              ref={rowVirtualizer.measureElement}
              data-index={vrow.index}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                right: 0,
                transform: `translateY(${vrow.start}px)`,
                paddingBottom: r.kind === "card" ? "1rem" : "0",
              }}
            >
              {r.kind === "header" ? (
                <HostHeader
                  hostId={r.hostId}
                  hostName={r.hostName}
                  hostType={r.hostType}
                  count={r.count}
                  overrideCount={r.overrideCount}
                  collapsed={false /* parent rebuilds rows on toggle */}
                  onToggle={() => onToggleHost(r.hostId ?? "__none__")}
                />
              ) : (
                /* SourceCard subscribes to its own scan slice via
                   useActiveScanForSource, so we no longer pass
                   activeScans down. Selector-based subscription means
                   a scan event for source A never re-renders source
                   B's card. */
                <SourceCard
                  source={r.source}
                  onOpen={onOpen}
                  onOpenLog={onOpenLog}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function Sources() {
  const { data: sources, isLoading, error } = useSources();
  // v0.4.5: source.status updates via WS scan.state events get
  // patched directly into the React Query cache by the reconciler,
  // so the badge / DisplayRows reflect "scanning" within one WS
  // frame instead of waiting for a full page refresh.
  useSourceStatusReconciler();
  const scannerSummary = useScannerSummary();
  const showNoScannerBanner =
    (sources?.length ?? 0) > 0 &&
    scannerSummary.data !== undefined &&
    scannerSummary.data.online === 0;
  const [openSourceId, setOpenSourceId] = useState<string | null>(null);
  const [logScanId, setLogScanId] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();

  // Deep-link entry: dashboard rows navigate to /sources?open=<id> to
  // open the detail drawer for a specific source. Strip the param after
  // reading so a back-nav doesn't keep re-opening the drawer.
  useEffect(() => {
    const openParam = searchParams.get("open");
    if (openParam && openParam !== openSourceId) {
      setOpenSourceId(openParam);
      const next = new URLSearchParams(searchParams);
      next.delete("open");
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams, openSourceId]);

  // Lean version of the open source from the list payload — used for
  // the seed render while the full version (with connection_config)
  // loads. Avoids a flash-of-empty-panel when you click a card.
  const openSourceLean = openSourceId
    ? sources?.find((s) => s.id === openSourceId) ?? null
    : null;
  // Full source — fetched on click since the list endpoint dropped
  // connection_config + security_metadata + exclude_patterns to keep
  // the page-load payload small (v0.4.3).
  const openSourceDetailQ = useSourceDetail(openSourceId);
  const openSource = openSourceDetailQ.data ?? openSourceLean;
  // v0.4.5: per-source slice subscription. The page used to read the
  // entire bySource map and re-render on every WS event; now it
  // bails unless the open source's scan id actually flips.
  //
  // v0.4.8: split into TWO selectors. The "open scan" (pending/
  // running only) drives the disabled state of the Scan-now button
  // — terminal scans must not gate it, or the user can't trigger
  // a follow-up scan once one has failed. The "active scan" stays
  // around for the log panel's source-name lookup.
  const openScanForOpen = useOpenScanForSource(openSource?.id);
  const latestScanForOpen = useActiveScanForSource(openSource?.id);

  // The log panel needs the source name for the drawer title. The
  // scan id → source id mapping comes from the LATEST scans map for
  // running scans; for terminal scans (the panel can stay open after
  // a scan completes) we fall back to the lean list. We accept that
  // a recently-completed scan whose entry has been pruned might lose
  // the name — the title just shows "Live scan log" without the
  // suffix, which is fine.
  const logScanSourceName = logScanId
    ? sources?.find((s) => s.id === latestScanForOpen?.source_id)?.name
    : undefined;

  const handleOpen = useCallback((id: string) => setOpenSourceId(id), []);
  const handleClose = useCallback(() => setOpenSourceId(null), []);
  const handleOpenLog = useCallback((id: string) => setLogScanId(id), []);
  const handleCloseLog = useCallback(() => setLogScanId(null), []);

  // Group-by + collapse state. Default to "host" when at least one
  // source actually has a host_id; otherwise "none" so the toggle
  // doesn't pop a single empty header. localStorage wins over the
  // auto-default once the user has expressed a preference.
  const anyHostAttached = (sources ?? []).some((s) => s.host_id != null);
  const [groupBy, setGroupBy] = useState<GroupBy>(() => {
    const stored = readGroupByPref();
    return stored;
  });
  const effectiveGroupBy: GroupBy =
    !anyHostAttached ? "none" : groupBy;
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  // Populate collapsed Set from localStorage once we know which hosts
  // exist (after the sources query lands). The Set is rebuilt only
  // when the host_ids actually change, so per-card re-renders don't
  // ripple here.
  const knownHostKeys = useMemo(() => {
    const out = new Set<string>();
    for (const s of sources ?? []) {
      out.add(s.host_id ?? "__none__");
    }
    return out;
  }, [sources]);
  useEffect(() => {
    const next = new Set<string>();
    for (const k of knownHostKeys) {
      if (readHostCollapsed(k)) next.add(k);
    }
    setCollapsed(next);
  }, [knownHostKeys]);

  const handleToggleHost = useCallback((hostKey: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(hostKey)) {
        next.delete(hostKey);
        writeHostCollapsed(hostKey, false);
      } else {
        next.add(hostKey);
        writeHostCollapsed(hostKey, true);
      }
      return next;
    });
  }, []);

  const rows: SourceRow[] = useMemo(() => {
    if (!sources || sources.length === 0) return [];
    return effectiveGroupBy === "host"
      ? buildGroupedRows(sources, collapsed)
      : buildUngroupedRows(sources);
  }, [sources, effectiveGroupBy, collapsed]);

  // v0.5.6 — page-level "Scan all" button. Triggers an incremental
  // scan for every visible source via the dedup-aware /scans/trigger
  // endpoint. Confirms first because 50+ scans is a non-trivial
  // amount of work to enqueue.
  const bulkTrigger = useBulkTriggerScans();
  const [confirmingScanAll, setConfirmingScanAll] = useState(false);
  const visibleSourceIds = useMemo(
    () => (sources ?? []).map((s) => s.id),
    [sources],
  );

  return (
    <Page
      title="Sources"
      description="Filesystem locations Akashic indexes and watches. Click a card to view details, edit, or scan."
      width="wide"
    >
      {showNoScannerBanner && (
        <div className="mb-4 rounded-md bg-amber-50 border border-amber-200 dark:bg-amber-500/10 dark:border-amber-500/40 px-4 py-3">
          <p className="text-sm text-amber-900 dark:text-amber-200">
            <span className="font-medium">No scanner agent is online.</span>{" "}
            Scans will queue indefinitely until you register one.{" "}
            <Link
              to="/settings/scanners"
              className="underline font-medium hover:text-amber-700"
            >
              Settings → Scanners
            </Link>
          </p>
        </div>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2 space-y-4">
          {isLoading ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <Skeleton className="h-44" />
              <Skeleton className="h-44" />
            </div>
          ) : error ? (
            <Card>
              <p className="text-sm text-rose-600">
                {error instanceof Error
                  ? error.message
                  : "Error loading sources"}
              </p>
            </Card>
          ) : (sources ?? []).length === 0 ? (
            <Card padding="lg">
              <EmptyState
                title="No sources yet"
                description="Add your first source on the right to start indexing."
              />
            </Card>
          ) : (
            <>
              <div className="flex items-center justify-end gap-2 -mt-1 mb-2">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => setConfirmingScanAll(true)}
                  loading={bulkTrigger.isPending}
                  disabled={visibleSourceIds.length === 0}
                  title="Trigger an incremental scan for every visible source."
                >
                  Scan all
                </Button>
                {anyHostAttached && (
                  <>
                    <span className="text-xs text-fg-muted ml-2">Group by:</span>
                    <div
                      className="inline-flex rounded-md border border-line text-xs overflow-hidden"
                      role="group"
                      aria-label="Group sources"
                    >
                      {(["host", "none"] as const).map((opt) => (
                        <button
                          key={opt}
                          type="button"
                          onClick={() => {
                            setGroupBy(opt);
                            writeGroupByPref(opt);
                          }}
                          aria-pressed={effectiveGroupBy === opt}
                          className={`px-2.5 py-1 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-1 ${
                            effectiveGroupBy === opt
                              ? "bg-accent-600 text-white"
                              : "bg-surface text-fg-muted hover:bg-surface-muted"
                          }`}
                        >
                          {opt === "host" ? "Host" : "None"}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
              <VirtualSourceList
                rows={rows}
                onOpen={handleOpen}
                onOpenLog={handleOpenLog}
                onToggleHost={handleToggleHost}
              />
            </>
          )}
        </div>

        <div>
          <AddSourceForm />
        </div>
      </div>

      <SourceDetail
        source={openSource}
        open={openSource !== null}
        onClose={handleClose}
        activeScanId={openScanForOpen?.id ?? null}
        latestScanId={latestScanForOpen?.id ?? null}
      />

      <ScanLogPanel
        open={logScanId !== null}
        onClose={handleCloseLog}
        scanId={logScanId}
        sourceName={logScanSourceName}
      />

      <ConfirmDialog
        open={confirmingScanAll}
        title={`Trigger scans for ${visibleSourceIds.length} source${visibleSourceIds.length === 1 ? "" : "s"}?`}
        description="Each source queues an incremental scan. Sources already running a scan are skipped automatically."
        confirmLabel={`Scan ${visibleSourceIds.length}`}
        loading={bulkTrigger.isPending}
        onConfirm={async () => {
          try {
            await bulkTrigger.mutateAsync({
              ids: visibleSourceIds,
              scanType: "incremental",
            });
          } catch (e) {
            // Surface the failure (review W-I1). Pre-fix the
            // unhandled rejection left the dialog stuck in its
            // loading state.
            toast.error(e instanceof Error ? e.message : "Scan all failed");
          } finally {
            setConfirmingScanAll(false);
          }
        }}
        onCancel={() => !bulkTrigger.isPending && setConfirmingScanAll(false)}
      />
    </Page>
  );
}
