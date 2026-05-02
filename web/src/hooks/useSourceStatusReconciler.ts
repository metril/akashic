/**
 * useSourceStatusReconciler — patches cached Source rows when WS
 * scan.state events report a status change (v0.4.5).
 *
 * Without this, source.status only refreshes when the user mutates
 * the source or 30s passes — which is why the "Scanning…" badge
 * wouldn't appear until a full page refresh after clicking
 * "Scan now". The Phase-2 multi-scanner split moved status flips to
 * /lease, so the trigger response no longer carries the new state;
 * the WS scan.state event does, but no consumer was reading the
 * source_status field off it.
 *
 * Mount once at the page level. Patches BOTH cache keys:
 *   - ["sources"] (list view, virtualized SourceCards)
 *   - ["sources", id, "detail"] (per-source panel header + DisplayRows)
 *
 * The `if (s.status !== next)` guard preserves array/object identity
 * when nothing actually changed — critical for React.memo'd
 * consumers downstream.
 */
import { useQueryClient } from "@tanstack/react-query";

import type { Source } from "../types";
import { useScansStreamEvents } from "./useScansStreamEvents";

/**
 * Apply a single (source_id, source_status) reconciliation to both
 * cache keys. Pulled out so scan.state and snapshot can share it.
 */
function patchSourceStatus(
  qc: ReturnType<typeof useQueryClient>,
  sourceId: string,
  next: string,
): void {
  qc.setQueryData<Source[] | undefined>(["sources"], (prev) => {
    if (!prev) return prev;
    // Only allocate a new array if a row actually changes — keeps
    // useSources() consumers' reference stable when the event was
    // for a status that already matched.
    let mutated = false;
    const out = prev.map((s) => {
      if (s.id === sourceId && s.status !== next) {
        mutated = true;
        return { ...s, status: next };
      }
      return s;
    });
    return mutated ? out : prev;
  });

  qc.setQueryData<Source | undefined>(
    ["sources", sourceId, "detail"],
    (prev) => (prev && prev.status !== next ? { ...prev, status: next } : prev),
  );
}

/**
 * Apply N (sourceId, status) pairs in a SINGLE setQueryData call.
 * v0.4.7: each setQueryData call notifies useSources subscribers, so
 * a snapshot with N active scans used to trigger N consecutive
 * Sources.tsx re-renders. By collapsing the patches into one array
 * walk we get one notification regardless of how many sources need
 * updating.
 */
function patchManySourceStatuses(
  qc: ReturnType<typeof useQueryClient>,
  updates: Map<string, string>,
): void {
  if (updates.size === 0) return;

  qc.setQueryData<Source[] | undefined>(["sources"], (prev) => {
    if (!prev) return prev;
    let mutated = false;
    const out = prev.map((s) => {
      const next = updates.get(s.id);
      if (next !== undefined && s.status !== next) {
        mutated = true;
        return { ...s, status: next };
      }
      return s;
    });
    return mutated ? out : prev;
  });

  // Per-source detail caches still need individual patches (each is
  // a different cache key). Only fires for sources whose detail panel
  // has been opened recently enough to still be cached, so this is
  // typically 0-1 calls in practice.
  for (const [sourceId, next] of updates) {
    qc.setQueryData<Source | undefined>(
      ["sources", sourceId, "detail"],
      (prev) => (prev && prev.status !== next ? { ...prev, status: next } : prev),
    );
  }
}

export function useSourceStatusReconciler(): void {
  const qc = useQueryClient();
  useScansStreamEvents((event) => {
    if (event.kind === "scan.state") {
      if (!event.source_status) return;
      patchSourceStatus(qc, event.source_id, event.source_status);
      return;
    }
    // v0.4.6: also apply on snapshot frames. The /ws/scans server
    // sends a snapshot on every (re)connect carrying source_status
    // for each active scan. Without this, a panel opened right
    // after a reconnect would show source.status from the REST
    // fetch — stale if a scan started during the WS gap — until
    // the next live scan.state event happened to fire.
    //
    // v0.4.7: collapse all patches into ONE setQueryData call so
    // a 10-scan snapshot triggers one re-render of useSources
    // subscribers, not ten.
    if (event.kind === "snapshot") {
      const updates = new Map<string, string>();
      for (const s of event.scans) {
        if (s.source_status) {
          updates.set(s.source_id, s.source_status);
        }
      }
      patchManySourceStatuses(qc, updates);
    }
  });
}
