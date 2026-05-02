import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Link } from "react-router-dom";
import { useSources } from "../hooks/useSources";
import { useScansStream } from "../hooks/useScansStream";
import { useScannerSummary } from "../hooks/useScannerSummary";
import {
  Card,
  Badge,
  Skeleton,
  EmptyState,
  Page,
} from "../components/ui";
import type { BadgeVariant } from "../components/ui";
import type { Scan, Source } from "../types";
import { computeETA, formatDate, formatDuration, formatNumber } from "../lib/format";
import { formatSourceSummary } from "../lib/sources";
import { BucketSecurityCard } from "../components/acl/BucketSecurityCard";
import { AddSourceForm } from "../components/sources/AddSourceForm";
import { ScanLogPanel } from "../components/scans/ScanLogPanel";
import { SourceDetail } from "../components/sources/SourceDetail";
import { api } from "../api/client";
import { useQueryClient } from "@tanstack/react-query";

const KNOWN_STATUSES: BadgeVariant[] = [
  "online",
  "offline",
  "scanning",
  "failed",
];

function statusVariant(status: string): BadgeVariant {
  return (KNOWN_STATUSES as string[]).includes(status)
    ? (status as BadgeVariant)
    : "neutral";
}

function statusLabel(status: string): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

interface SourceCardProps {
  source: Source;
  activeScan: Scan | undefined;
  onOpen: () => void;
  onOpenLog: (scanId: string) => void;
}

function SourceCard({ source, activeScan, onOpen, onOpenLog }: SourceCardProps) {
  const summary = formatSourceSummary(source);
  const isScanning = source.status === "scanning";
  // Phase-2 multi-scanner: a source can have a queued scan that no
  // agent has claimed yet. Distinct from "scanning" (agent in flight)
  // so the user can tell why nothing's happening.
  const isQueued = !isScanning && activeScan?.status === "pending";
  const queryClient = useQueryClient();
  const [stopping, setStopping] = useState(false);

  async function handleStop() {
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
      // Re-fetch sources + scans so the card snaps to "online" without
      // waiting for the next polling tick.
      await queryClient.invalidateQueries({ queryKey: ["sources"] });
      await queryClient.invalidateQueries({ queryKey: ["scans", "active"] });
    } catch {
      // Toast already surfaced the error.
    } finally {
      setStopping(false);
    }
  }

  // Compose progress subtitle for in-flight scans.
  const progressLine = isScanning && activeScan ? buildProgressLine(activeScan) : null;

  // Show watchdog/error message for failed scans on the previous run.
  const errorMessage =
    source.status === "failed" && activeScan?.error_message
      ? activeScan.error_message
      : null;

  return (
    <Card padding="md" className="flex flex-col">
      <button
        type="button"
        onClick={onOpen}
        className="text-left flex flex-col grow rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-1"
      >
        <div className="flex items-start justify-between gap-3 mb-1">
          <h3 className="text-base font-semibold text-fg truncate">
            {source.name}
          </h3>
          <Badge variant={isQueued ? "neutral" : statusVariant(source.status)}>
            {isQueued ? "Queued" : statusLabel(source.status)}
          </Badge>
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
          <div className="mb-3 rounded-md bg-blue-50 border border-blue-100 dark:bg-blue-500/10 dark:border-blue-500/30 px-2.5 py-2">
            <p className="text-xs text-blue-900 font-medium">{progressLine.summary}</p>
            {progressLine.currentPath && (
              <p className="text-[11px] text-blue-700 font-mono mt-0.5 truncate">
                {progressLine.currentPath}
              </p>
            )}
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
          <div className="flex gap-2">
            <dt className="text-fg-subtle">Last scan</dt>
            <dd>{formatDate(source.last_scan_at)}</dd>
          </div>
        </dl>
      </button>

      {/* Live-log shortcut stays on the card so users don't have to
          open the drawer just to peek at progress. Other actions
          (edit, scan now, delete) live inside the drawer to keep the
          card minimal. */}
      {isScanning && activeScan && (
        <div className="mt-3 pt-2 border-t border-line-subtle flex items-center gap-3">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onOpenLog(activeScan.id);
            }}
            className="text-xs text-blue-700 hover:text-blue-900 font-medium"
          >
            View live log →
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              handleStop();
            }}
            disabled={stopping}
            className="text-xs text-rose-700 hover:text-rose-900 font-medium disabled:opacity-50"
          >
            {stopping ? "Stopping…" : "Stop scan"}
          </button>
        </div>
      )}

      {source.type === "s3" && <BucketSecurityCard source={source} />}
    </Card>
  );
}

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

export default function Sources() {
  const { data: sources, isLoading, error } = useSources();
  // /ws/scans push stream replaces the old 2s polling. Same shape:
  // { byScan, bySource, hasActive } so the JSX below didn't change.
  const activeScans = useScansStream();
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

  const openSource = openSourceId
    ? sources?.find((s) => s.id === openSourceId) ?? null
    : null;
  const activeScanForOpen = openSource
    ? activeScans?.bySource[openSource.id]
    : undefined;

  const logScanSourceName = logScanId
    ? sources?.find((s) => activeScans?.byScan[logScanId]?.source_id === s.id)?.name
    : undefined;

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
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {(sources ?? []).map((s) => (
                <SourceCard
                  key={s.id}
                  source={s}
                  activeScan={activeScans?.bySource[s.id]}
                  onOpen={() => setOpenSourceId(s.id)}
                  onOpenLog={setLogScanId}
                />
              ))}
            </div>
          )}
        </div>

        <div>
          <AddSourceForm />
        </div>
      </div>

      <SourceDetail
        source={openSource}
        open={openSource !== null}
        onClose={() => setOpenSourceId(null)}
        activeScanId={activeScanForOpen?.id ?? null}
      />

      <ScanLogPanel
        open={logScanId !== null}
        onClose={() => setLogScanId(null)}
        scanId={logScanId}
        sourceName={logScanSourceName}
      />
    </Page>
  );
}
