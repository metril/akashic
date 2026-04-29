import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Scan, Source } from "../types";

/**
 * useActiveScans — list-level poll for the currently-running scan per
 * source. Polls every 2 s, ONLY while at least one source is `scanning`.
 * When all scans are idle, the interval reverts to a slow refetch on
 * focus/mount.
 *
 * The endpoint returns a flat list of scans; we collapse to the latest
 * row per source so consumers can do `byScans[sourceId]?.current_path`.
 *
 * Polls aren't enabled on hidden tabs.
 */
export interface ActiveScansResult {
  byScan: Record<string, Scan>;        // scan_id → latest scan row
  bySource: Record<string, Scan>;      // source_id → latest scan row
  hasActive: boolean;
}

export function useActiveScans(sources: Source[] | undefined) {
  // Drive the gating off the cards' `source.status` to avoid a chicken-
  // and-egg: we shouldn't fast-poll when nothing is scanning, and the
  // list-level useSources() query already polls on its own cadence.
  const hasScanning = (sources ?? []).some((s) => s.status === "scanning");
  return useQuery<ActiveScansResult>({
    queryKey: ["scans", "active"],
    queryFn: async () => {
      // Include failed so the UI can surface watchdog error_messages on
      // sources whose latest scan failed; the endpoint sorts by
      // started_at desc, so the first hit per source_id is the most
      // recent.
      const rows = await api.get<Scan[]>(
        "/scans?status=running,pending,failed&limit=200"
      );
      const byScan: Record<string, Scan> = {};
      const bySource: Record<string, Scan> = {};
      for (const r of rows) {
        byScan[r.id] = r;
        // Multiple scan rows per source are possible (e.g., a stuck
        // pending row plus a real running one) — keep the most recent
        // by started_at.
        const existing = bySource[r.source_id];
        if (
          !existing ||
          (r.started_at &&
            (!existing.started_at || r.started_at > existing.started_at))
        ) {
          bySource[r.source_id] = r;
        }
      }
      return { byScan, bySource, hasActive: rows.length > 0 };
    },
    refetchInterval: hasScanning ? 2000 : false,
    refetchIntervalInBackground: false,
  });
}
