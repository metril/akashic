/**
 * useLiveDataRefresh — app-wide cache invalidation on terminal scan
 * events and scanner-lifecycle events. Mounted once in Layout so every
 * authed page self-heals without a manual refresh.
 *
 * Why this exists: the WS reconcilers (useSourceStatusReconciler,
 * useDashboardLiveRefresh) patch narrow fields in-memory or only cover
 * the dashboard summary tile. Everything else a scan touches —
 * last_scan_at, file counts, byte totals on Sources/Hosts rows, the
 * SourceDetail last-completed summary, the access-risks tile — comes
 * from queries that were only invalidated by their OWN mutations, never
 * by a scan finishing. So a completed scan left those fields stale until
 * a page reload.
 *
 * Cheap because invalidateQueries does a PREFIX match and only refetches
 * ACTIVE (mounted) queries — inactive ones are marked stale and refetch
 * lazily on next mount. So invalidating broad prefixes on every page
 * only refetches what's currently on screen.
 *
 * Deliberately ignores running/pending heartbeats: invalidating a list
 * on every progress tick would refetch it constantly during a long
 * scan. Those keep the in-memory status patch in useSourceStatusReconciler.
 */
import type { QueryKey } from "@tanstack/react-query";
import { useQueryClient } from "@tanstack/react-query";

import { useScannersStreamEvents } from "./useScannersStreamEvents";
import type { ScannersStreamEvent } from "./useScannersStreamEvents";
import { useScansStreamEvents } from "./useScansStreamEvents";
import type { ScansStreamEvent } from "./useScansStreamEvents";

const TERMINAL_STATUSES = ["completed", "failed", "cancelled"];

// Everything a finished scan can change, by query-key prefix. Prefix
// match means ["sources"] also covers ["sources", id, "detail"], and
// ["scans"] covers ["scans", "last-completed", id], etc.
const SCAN_DERIVED_PREFIXES: QueryKey[] = [
  ["sources"],
  ["hosts"],
  ["scans"],
  ["dashboard"],
];

/**
 * Pure: which query-key prefixes a scans-stream event should invalidate.
 * Empty array = invalidate nothing (e.g. a running heartbeat).
 */
export function scanEventInvalidations(event: ScansStreamEvent): QueryKey[] {
  switch (event.kind) {
    case "snapshot":
      // Reconnect: terminal events may have been missed while the socket
      // was down — resync everything scan-derived.
      return SCAN_DERIVED_PREFIXES;
    case "source.created":
    case "source.updated":
    case "source.deleted":
      return [["sources"]];
    case "host.changed":
      return [["hosts"]];
    case "scan.state":
      return TERMINAL_STATUSES.includes(event.scan_status)
        ? SCAN_DERIVED_PREFIXES
        : [];
    default:
      return [];
  }
}

/**
 * Pure: which query-key prefixes a scanners-stream event should
 * invalidate. Any lifecycle event can move the list or summary
 * (online/offline, version, concurrency, registration, removal).
 */
export function scannerEventInvalidations(
  event: ScannersStreamEvent,
): QueryKey[] {
  if (event.kind === "ping" || event.kind === "error") return [];
  return [["scanners"]];
}

export function useLiveDataRefresh(): void {
  const qc = useQueryClient();

  useScansStreamEvents((event) => {
    for (const queryKey of scanEventInvalidations(event)) {
      qc.invalidateQueries({ queryKey });
    }
  });

  useScannersStreamEvents((event) => {
    for (const queryKey of scannerEventInvalidations(event)) {
      qc.invalidateQueries({ queryKey });
    }
  });
}
