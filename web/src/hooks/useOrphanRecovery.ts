/**
 * Hooks for the Recover-orphans flow.
 *
 *   useReattachDryRun — preview matched/conflicts/ambiguous before
 *                       committing.
 *   useReattachCommit — perform the re-attach (writes to DB).
 *
 * v0.4.3 note: useOrphanMatchCount used to live here too and
 * powered a proactive banner on every source-detail open. That
 * fired a JOIN-heavy COUNT query even when the user had never
 * delete-with-preserved a source (the common case). Replaced
 * with an explicit "Recover orphans…" button on the panel — the
 * dry-run only fires when the operator opens the modal.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";

export type ReattachStrategy = "path" | "path_and_hash";

export interface ReattachResponse {
  matched: number;
  conflicts: number;
  ambiguous: number;
  committed: boolean;
}

export function useReattachDryRun(
  sourceId: string | null,
  strategy: ReattachStrategy,
  enabled: boolean,
) {
  return useQuery<ReattachResponse>({
    queryKey: ["sources", sourceId, "reattach-dry-run", strategy],
    queryFn: () =>
      api.post<ReattachResponse>(
        `/sources/${sourceId}/reattach-orphans`,
        { strategy, dry_run: true },
      ),
    enabled: enabled && sourceId != null,
    // Don't cache aggressively — the operator might rescan between
    // opens and want a fresh number.
    staleTime: 0,
  });
}

export function useReattachCommit(sourceId: string | null) {
  const qc = useQueryClient();
  return useMutation<ReattachResponse, Error, { strategy: ReattachStrategy }>({
    mutationFn: ({ strategy }) =>
      api.post<ReattachResponse>(
        `/sources/${sourceId}/reattach-orphans`,
        { strategy, dry_run: false },
      ),
    onSuccess: () => {
      // Re-attached entries now belong to this source — invalidate
      // every query that could reflect entry → source mapping.
      qc.invalidateQueries({ queryKey: ["sources"] });
      qc.invalidateQueries({ queryKey: ["search"] });
      qc.invalidateQueries({ queryKey: ["entry"] });
    },
  });
}
