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

export function useSourceStatusReconciler(): void {
  const qc = useQueryClient();
  useScansStreamEvents((event) => {
    if (event.kind !== "scan.state") return;
    const next = event.source_status;
    if (!next) return;

    qc.setQueryData<Source[] | undefined>(["sources"], (prev) => {
      if (!prev) return prev;
      // Only allocate a new array if a row actually changes — keeps
      // useSources() consumers' reference stable when the event was
      // for a status that already matched.
      let mutated = false;
      const out = prev.map((s) => {
        if (s.id === event.source_id && s.status !== next) {
          mutated = true;
          return { ...s, status: next };
        }
        return s;
      });
      return mutated ? out : prev;
    });

    qc.setQueryData<Source | undefined>(
      ["sources", event.source_id, "detail"],
      (prev) => (prev && prev.status !== next ? { ...prev, status: next } : prev),
    );
  });
}
