/**
 * Subscribe the Dashboard to /ws/scans events and invalidate the
 * `["dashboard", "summary"]` query when something happens that could
 * change a tile.
 *
 * Sources page + Dashboard share the same underlying WebSocket via
 * useScansStreamEvents' module-level reference count, so mounting
 * both pages doesn't open two sockets.
 */
import { useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useScansStreamEvents } from "./useScansStreamEvents";

const RUNNING_DEBOUNCE_MS = 5_000;

export function useDashboardLiveRefresh() {
  const qc = useQueryClient();
  const debounceRef = useRef<number | null>(null);

  useScansStreamEvents((event) => {
    const invalidateNow = () =>
      qc.invalidateQueries({ queryKey: ["dashboard", "summary"] });

    // Material changes — invalidate immediately.
    if (
      event.kind === "source.created" ||
      event.kind === "source.deleted" ||
      (event.kind === "scan.state" &&
        ["completed", "failed", "cancelled"].includes(event.scan_status))
    ) {
      invalidateNow();
      return;
    }
    // Pending / running transitions affect the active-scans tile only;
    // coalesce so a long scan's heartbeats don't pound the summary
    // endpoint every few seconds.
    if (
      event.kind === "scan.state" &&
      (event.scan_status === "pending" || event.scan_status === "running")
    ) {
      if (debounceRef.current != null) {
        window.clearTimeout(debounceRef.current);
      }
      debounceRef.current = window.setTimeout(() => {
        debounceRef.current = null;
        invalidateNow();
      }, RUNNING_DEBOUNCE_MS);
    }
  });
}
