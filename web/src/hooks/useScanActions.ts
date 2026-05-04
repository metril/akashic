import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../api/client";

interface TriggerResponse {
  scan_id: string;
  source_id: string;
  source_name: string;
  scan_type: string;
  last_scan_at: string | null;
  /** v0.5.6 — true on a fresh insert, false on dedup (pending/running scan returned). */
  created: boolean;
}

export type BulkScanType = "incremental" | "full";

export interface BulkScanResult {
  triggered: number;
  alreadyRunning: number;
  failed: number;
  failures: Array<{ id: string; error: string }>;
}

const CONCURRENCY = 8;

async function runWithConcurrency<T>(
  ids: string[],
  fn: (id: string) => Promise<T>,
  concurrency = CONCURRENCY,
): Promise<PromiseSettledResult<T>[]> {
  const results: PromiseSettledResult<T>[] = new Array(ids.length);
  let cursor = 0;
  async function worker() {
    while (true) {
      const i = cursor++;
      if (i >= ids.length) return;
      try {
        const v = await fn(ids[i]);
        results[i] = { status: "fulfilled", value: v };
      } catch (err) {
        results[i] = { status: "rejected", reason: err };
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, ids.length) }, worker));
  return results;
}

/**
 * Bulk-trigger scans across many sources via repeated POSTs to
 * `/scans/trigger`. The endpoint dedups idempotently per source —
 * concurrent triggers for the same source return the existing
 * pending/running scan with `created=false`. v0.5.6.
 *
 * Returns counts so the caller's toast can surface them; also
 * invalidates the React Query caches scans + sources read from.
 */
export function useBulkTriggerScans() {
  const qc = useQueryClient();
  return useMutation<BulkScanResult, Error, { ids: string[]; scanType: BulkScanType }>({
    mutationFn: async ({ ids, scanType }) => {
      const settled = await runWithConcurrency(ids, (id) =>
        api.post<TriggerResponse>("/scans/trigger", {
          source_id: id,
          scan_type: scanType,
        }),
      );
      let triggered = 0;
      let alreadyRunning = 0;
      let failed = 0;
      const failures: BulkScanResult["failures"] = [];
      for (let i = 0; i < settled.length; i++) {
        const r = settled[i];
        if (r.status === "fulfilled") {
          if (r.value.created) triggered++;
          else alreadyRunning++;
        } else {
          failed++;
          failures.push({
            id: ids[i],
            error: r.reason instanceof Error ? r.reason.message : "unknown error",
          });
        }
      }
      return { triggered, alreadyRunning, failed, failures };
    },
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["scans"] });
      qc.invalidateQueries({ queryKey: ["sources"] });
      const parts = [`Triggered ${result.triggered}`];
      if (result.alreadyRunning > 0) {
        parts.push(`${result.alreadyRunning} already running`);
      }
      if (result.failed > 0) {
        parts.push(`${result.failed} failed`);
      }
      const msg = parts.join(" · ");
      if (result.failed > 0) {
        toast.warning(msg);
      } else {
        toast.success(msg);
      }
    },
  });
}
