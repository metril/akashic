export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB", "PB"];
  const i = Math.min(
    Math.floor(Math.log(bytes) / Math.log(k)),
    sizes.length - 1,
  );
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

export function formatNumber(n: number | null | undefined): string {
  return (n ?? 0).toLocaleString();
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDuration(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm ? `${h}h ${rm}m` : `${h}h`;
}

/**
 * Compute an estimated time to completion for an in-flight scan.
 *
 * Two paths:
 * 1. `total_estimated` is set (prewalk produced a count): linear
 *    extrapolation from elapsed time.
 * 2. Fall back to `previous_scan_files`: same idea but using last
 *    scan's final count as the estimate.
 *
 * Returns null when neither path is available or `files_scanned` is 0.
 */
export function computeETA(
  filesScanned: number,
  totalEstimated: number | null | undefined,
  previousScanFiles: number | null | undefined,
  startedAt: string | null | undefined,
): { etaSeconds: number; total: number } | null {
  if (!startedAt || filesScanned <= 0) return null;
  const total = totalEstimated || previousScanFiles || 0;
  if (total <= filesScanned) return null;
  const elapsed = (Date.now() - new Date(startedAt).getTime()) / 1000;
  if (elapsed <= 0) return null;
  const remaining = total - filesScanned;
  const eta = remaining * (elapsed / filesScanned);
  return { etaSeconds: eta, total };
}
