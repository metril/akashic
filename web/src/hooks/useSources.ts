import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Source } from "../types";

export function useSources() {
  return useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: () => api.get<Source[]>("/sources"),
  });
}

/**
 * Per-source detail fetch — returns the FULL Source row (including
 * connection_config + security_metadata) that the lean list endpoint
 * omits. Used by SourceDetail panel when the user opens a source so
 * the edit/test/display flows have the data they need.
 *
 * Cache is per-id. Mutations (update / rotate-keys) invalidate via
 * the broad ["sources"] key, which sympathetically refreshes this
 * key too because react-query matches on prefix.
 */
export function useSourceDetail(sourceId: string | null) {
  return useQuery<Source>({
    queryKey: ["sources", sourceId, "detail"],
    queryFn: () => api.get<Source>(`/sources/${sourceId}`),
    enabled: sourceId != null,
    staleTime: 30_000,
  });
}

export function useCreateSource() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: {
      name: string;
      type: string;
      connection_config: Record<string, unknown>;
      scan_schedule?: string | null;
      exclude_patterns?: string[] | null;
      preferred_pool?: string | null;
    }) => api.post<Source>("/sources", data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
  });
}

export function useUpdateSource() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<Pick<Source, "name" | "connection_config" | "scan_schedule" | "exclude_patterns">> }) =>
      api.patch<Source>(`/sources/${id}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
  });
}

export interface DeleteSourceArgs {
  id: string;
  /**
   * v0.4.0 — when false (default), the source row is removed but
   * indexed entries survive with `source_id = NULL`; they stay
   * searchable and can be re-attached to a new source via
   * POST /sources/{id}/reattach-orphans. When true, every
   * indexed entry is purged alongside the source.
   */
  purgeEntries?: boolean;
}

export function useDeleteSource() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, purgeEntries = false }: DeleteSourceArgs) =>
      api.delete<void>(
        `/sources/${id}?purge_entries=${purgeEntries ? "true" : "false"}`,
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      // Search results may include orphaned entries from this source
      // now (in the preserve flavour) — invalidate so the UI sees them.
      queryClient.invalidateQueries({ queryKey: ["search"] });
    },
  });
}
