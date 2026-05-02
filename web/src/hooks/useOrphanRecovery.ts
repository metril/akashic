/**
 * Hook trio that powers the Recover-orphans flow in the source
 * detail view.
 *
 *   useOrphanMatchCount(sourceId) — cheap COUNT, drives the
 *     "N orphaned files match this source's tree" banner.
 *   useReattachDryRun(sourceId, strategy) — preview the matcher's
 *     output (matched/conflicts/ambiguous) before any DB change.
 *   useReattachCommit() — actually perform the re-attach.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";

export type ReattachStrategy = "path" | "path_and_hash";

export interface OrphanMatchCount {
  count: number;
}

export interface ReattachResponse {
  matched: number;
  conflicts: number;
  ambiguous: number;
  committed: boolean;
}

export function useOrphanMatchCount(sourceId: string | null) {
  return useQuery<OrphanMatchCount>({
    queryKey: ["sources", sourceId, "orphan-match-count"],
    queryFn: () =>
      api.get<OrphanMatchCount>(`/sources/${sourceId}/orphan-match-count`),
    enabled: sourceId != null,
    staleTime: 15_000,
  });
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
