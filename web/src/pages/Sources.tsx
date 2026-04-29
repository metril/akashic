import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  useSources,
  useDeleteSource,
} from "../hooks/useSources";
import { useActiveScans } from "../hooks/useActiveScans";
import { api } from "../api/client";
import {
  Card,
  Button,
  Badge,
  Skeleton,
  EmptyState,
} from "../components/ui";
import type { BadgeVariant } from "../components/ui";
import type { Scan, Source } from "../types";
import { computeETA, formatDate, formatDuration, formatNumber } from "../lib/format";
import { BucketSecurityCard } from "../components/acl/BucketSecurityCard";
import { AddSourceForm } from "../components/sources/AddSourceForm";
import { ScanLogPanel } from "../components/scans/ScanLogPanel";

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
  onOpenLog: (scanId: string) => void;
}

function SourceCard({ source, activeScan, onOpenLog }: SourceCardProps) {
  const deleteSource = useDeleteSource();
  const queryClient = useQueryClient();

  const triggerScan = useMutation({
    mutationFn: (sourceId: string) =>
      api.post("/scans/trigger", {
        source_id: sourceId,
        scan_type: "incremental",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
  });

  const path =
    typeof source.connection_config?.path === "string"
      ? source.connection_config.path
      : JSON.stringify(source.connection_config);

  function handleDelete() {
    if (confirm(`Delete source "${source.name}"?`)) {
      deleteSource.mutate(source.id);
    }
  }

  const isScanning = source.status === "scanning";
  const canScan = !isScanning;

  // Compose progress subtitle for in-flight scans.
  const progressLine = isScanning && activeScan
    ? buildProgressLine(activeScan)
    : null;

  // Show watchdog/error message for failed scans on the previous run.
  const errorMessage = source.status === "failed" && activeScan?.error_message
    ? activeScan.error_message
    : null;

  return (
    <Card padding="md" className="flex flex-col">
      <div className="flex items-start justify-between gap-3 mb-1">
        <h3 className="text-base font-semibold text-gray-900 truncate">
          {source.name}
        </h3>
        <Badge variant={statusVariant(source.status)}>
          {statusLabel(source.status)}
        </Badge>
      </div>
      <p className="text-xs text-gray-500 font-mono break-all mb-3">{path}</p>

      {progressLine && (
        <div className="mb-3 rounded-md bg-blue-50 border border-blue-100 px-2.5 py-2">
          <p className="text-xs text-blue-900 font-medium">{progressLine.summary}</p>
          {progressLine.currentPath && (
            <p className="text-[11px] text-blue-700 font-mono mt-0.5 truncate">
              {progressLine.currentPath}
            </p>
          )}
        </div>
      )}

      {errorMessage && (
        <div className="mb-3 rounded-md bg-rose-50 border border-rose-100 px-2.5 py-2">
          <p className="text-xs text-rose-800 font-medium">Last scan failed</p>
          <p className="text-[11px] text-rose-700 mt-0.5">{errorMessage}</p>
        </div>
      )}

      <dl className="text-xs text-gray-500 space-y-1 mb-4">
        <div className="flex gap-2">
          <dt className="text-gray-400">Type</dt>
          <dd>{source.type}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="text-gray-400">Last scan</dt>
          <dd>{formatDate(source.last_scan_at)}</dd>
        </div>
      </dl>
      <div className="mt-auto flex items-center gap-2 pt-2 flex-wrap">
        <Button
          size="sm"
          variant="secondary"
          onClick={() => triggerScan.mutate(source.id)}
          disabled={!canScan}
          loading={triggerScan.isPending}
        >
          {isScanning ? "Scanning…" : "Scan now"}
        </Button>
        {isScanning && activeScan && (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => onOpenLog(activeScan.id)}
          >
            View live log
          </Button>
        )}
        <Button
          size="sm"
          variant="danger"
          onClick={handleDelete}
          loading={deleteSource.isPending}
        >
          Delete
        </Button>
      </div>
      {triggerScan.isError && (
        <p className="text-xs text-rose-600 mt-2">
          {triggerScan.error instanceof Error
            ? triggerScan.error.message
            : "Failed to trigger scan"}
        </p>
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
    // Prewalk's running count is reported via total_estimated.
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
  const { data: activeScans } = useActiveScans(sources);
  const [logScanId, setLogScanId] = useState<string | null>(null);

  const logScanSourceName = logScanId
    ? sources?.find((s) =>
        activeScans?.byScan[logScanId]?.source_id === s.id,
      )?.name
    : undefined;

  return (
    <div className="px-8 py-7 max-w-7xl">
      <div className="mb-7 flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900 tracking-tight">
            Sources
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Filesystem locations Akashic indexes and watches.
          </p>
        </div>
      </div>

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

      <ScanLogPanel
        open={logScanId !== null}
        onClose={() => setLogScanId(null)}
        scanId={logScanId}
        sourceName={logScanSourceName}
      />
    </div>
  );
}
