/**
 * Subscribe the Dashboard to /ws/scans events and invalidate the
 * `["dashboard", "summary"]` query when something happens that
 * could change a tile.
 *
 * Sources page + Dashboard share the same underlying WebSocket via
 * useScansStreamEvents' module-level reference count, so mounting
 * both pages doesn't open two sockets.
 *
 * v0.4.4 fix: replaced the prior trailing-edge debounce with a
 * proper THROTTLE (leading + maxWait) for running/pending events.
 *
 * Why: the old code did `clearTimeout(t); t = setTimeout(fire, 5s)`
 * — meaning while events streamed in faster than 5s, the timer
 * never fired AT ALL. Users saw zero live updates during a busy
 * scan, then a thundering refetch at the end. The fix: fire on
 * the first event in a window (leading), then ignore subsequent
 * events for 5s. Result: live tiles tick along at most once per
 * 5s while scans are running, AND every running event no longer
 * pays a clearTimeout/setTimeout cost.
 */
import { useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useScansStreamEvents } from "./useScansStreamEvents";

const RUNNING_THROTTLE_MS = 5_000;

export function useDashboardLiveRefresh() {
  const qc = useQueryClient();
  // Last time we fired an invalidation from a running/pending event.
  // 0 = never. monotonic-ish via Date.now() — drift across the 5s
  // window doesn't matter for this throttle.
  const lastFiredAt = useRef(0);

  useScansStreamEvents((event) => {
    // Bail early on ping — keeps the listener body off the hot
    // path of the 30s heartbeat. Saves a few function-call cycles
    // per ping per consumer; meaningful at scale.
    if (event.kind === "ping" || event.kind === "error") return;

    const invalidateNow = () =>
      qc.invalidateQueries({ queryKey: ["dashboard", "summary"] });

    // Material changes — invalidate immediately and reset the
    // throttle window so a subsequent running heartbeat doesn't
    // double-fire 50ms later.
    if (
      event.kind === "source.created" ||
      event.kind === "source.deleted" ||
      (event.kind === "scan.state" &&
        ["completed", "failed", "cancelled"].includes(event.scan_status))
    ) {
      lastFiredAt.current = Date.now();
      invalidateNow();
      return;
    }

    // Pending / running heartbeats — leading-edge throttle. Fires
    // immediately on the first event in a window, then suppresses
    // until the window elapses. Ensures live-tile movement during
    // long scans without per-batch refetch storms.
    if (
      event.kind === "scan.state" &&
      (event.scan_status === "pending" || event.scan_status === "running")
    ) {
      const now = Date.now();
      if (now - lastFiredAt.current >= RUNNING_THROTTLE_MS) {
        lastFiredAt.current = now;
        invalidateNow();
      }
    }
  });
}
