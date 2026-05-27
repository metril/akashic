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
      host_id?: string | null;
      connection_config: Record<string, unknown>;
      scan_schedule?: string | null;
      exclude_patterns?: string[] | null;
      preferred_pool?: string | null;
      max_parallel_scanners?: number | null;
      scan_chunk_size?: number | null;
      is_removable?: boolean | null;
      credential_profile_id?: string | null;
    }) => api.post<Source>("/sources", data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
  });
}

export function useUpdateSource() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<Pick<Source, "name" | "connection_config" | "scan_schedule" | "exclude_patterns" | "is_removable" | "max_parallel_scanners" | "scan_chunk_size" | "credential_profile_id">> }) =>
      api.patch<Source>(`/sources/${id}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
  });
}

/**
 * v0.28.0 — replaces useCheckSourceReachability. The new endpoint
 * runs probes for one or more (source, scanner) pairs on demand:
 * inline for non-local sources, dispatched to the agent over long-
 * poll for local sources. Returns one result row per scanner (with
 * `pending: true` for slow agents whose result lands later via the
 * source-reachability WS channel).
 */
export interface TestScannersResultRow {
  scanner_id: string | null;
  ok: boolean | null;
  step: string | null;
  error: string | null;
  pending: boolean;
  completed_at: string | null;
}

export interface TestScannersResponse {
  results: TestScannersResultRow[];
}

export function useTestSourceScanners() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      sourceId,
      scannerIds,
    }: {
      sourceId: string;
      scannerIds?: string[];
    }) =>
      api.post<TestScannersResponse>(
        `/sources/${sourceId}/test-scanners`,
        { scanner_ids: scannerIds ?? null },
      ),
    onSuccess: (_, { sourceId }) => {
      queryClient.invalidateQueries({
        queryKey: ["sources", sourceId, "scanner-reachability"],
      });
      queryClient.invalidateQueries({
        queryKey: ["sources", sourceId, "reachability-summary"],
      });
    },
  });
}

/**
 * Compact source-level reachability for badges + cards. Derived from
 * the latest reachability_results row (across all scanners) and the
 * latest successful scan, whichever is fresher.
 */
export interface ReachabilitySummary {
  ok: boolean | null;
  last_at: string | null;
  last_step: string | null;
  last_error: string | null;
  last_scanner_id: string | null;
  last_scanner_name: string | null;
}

export function useReachabilitySummary(sourceId: string | null) {
  return useQuery<ReachabilitySummary>({
    queryKey: ["sources", sourceId, "reachability-summary"],
    queryFn: () =>
      api.get<ReachabilitySummary>(
        `/sources/${sourceId}/reachability-summary`,
      ),
    enabled: sourceId != null,
    staleTime: 30_000,
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

// v0.5.7 — eligibility-management hooks. The api computes a per-
// source view of "which scanners are allowed, and what each scanner's
// last reachability probe said". Saving a new allowed-scanner set
// translates into per-scanner allowed_source_ids writes server-side.

export interface ScannerReachabilityHistoryEntry {
  ok: boolean;
  completed_at: string | null;
  step: string | null;
  error: string | null;
}

export interface ScannerReachabilityRow {
  scanner_id: string;
  name: string;
  pool: string | null;
  online: boolean;
  currently_allowed: boolean;
  ok: boolean | null;
  last_probed_at: string | null;
  step: string | null;
  error: string | null;
  history: ScannerReachabilityHistoryEntry[];
}

export function useSourceScannerReachability(sourceId: string | null) {
  return useQuery<ScannerReachabilityRow[]>({
    queryKey: ["sources", sourceId, "scanner-reachability"],
    queryFn: () =>
      api.get<ScannerReachabilityRow[]>(
        `/sources/${sourceId}/scanner-reachability`,
      ),
    enabled: sourceId != null,
    staleTime: 10_000,
  });
}

// v0.41.0 — deeper per-scanner reachability history for the new
// Reachability tab on SourceDetail. Separate hook + endpoint from
// useSourceScannerReachability (which keeps a 5-row slice for the
// eligibility panel) so the tab gets up to 20 rows per scanner
// without bloating the panel's payload.
export interface ReachabilityOutcome {
  ok: boolean;
  step: string | null;
  error: string | null;
  started_at: string;
  completed_at: string;
}

export interface PerScannerHistory {
  scanner_id: string | null;
  scanner_name: string | null;
  outcomes: ReachabilityOutcome[];
}

export interface ReachabilityHistory {
  per_scanner: PerScannerHistory[];
}

export function useSourceReachabilityHistory(
  sourceId: string | null,
  enabled = true,
) {
  return useQuery<ReachabilityHistory>({
    queryKey: ["sources", sourceId, "reachability-history"],
    queryFn: () =>
      api.get<ReachabilityHistory>(
        `/sources/${sourceId}/reachability-history`,
      ),
    enabled: sourceId != null && enabled,
    staleTime: 10_000,
  });
}

export function useUpdateSourceAllowedScanners() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sourceId, scannerIds }: { sourceId: string; scannerIds: string[] }) =>
      api.patch<{ updated_scanners: number }>(
        `/sources/${sourceId}/allowed-scanners`,
        { scanner_ids: scannerIds },
      ),
    onSuccess: (_, { sourceId }) => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      queryClient.invalidateQueries({ queryKey: ["scanners"] });
      queryClient.invalidateQueries({ queryKey: ["hosts"] });
      queryClient.invalidateQueries({
        queryKey: ["sources", sourceId, "scanner-reachability"],
      });
    },
  });
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
