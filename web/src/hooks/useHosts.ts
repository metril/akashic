import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Host } from "../types";

export function useHosts() {
  return useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => api.get<Host[]>("/hosts"),
  });
}

export function useHostDetail(hostId: string | null) {
  return useQuery<Host>({
    queryKey: ["hosts", hostId, "detail"],
    queryFn: () => api.get<Host>(`/hosts/${hostId}`),
    enabled: hostId != null,
    staleTime: 30_000,
  });
}

export interface CreateHostInput {
  name: string;
  type: string;
  connection_config: Record<string, unknown>;
  credential_profile_id?: string | null;
}

export function useCreateHost() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateHostInput) => api.post<Host>("/hosts", data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["hosts"] });
    },
  });
}

export interface UpdateHostInput {
  name?: string;
  connection_config?: Record<string, unknown>;
  credential_profile_id?: string | null;
}

export function useUpdateHost() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: UpdateHostInput }) =>
      api.patch<Host>(`/hosts/${id}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["hosts"] });
      queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
  });
}

export function useDeleteHost() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/hosts/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["hosts"] });
    },
  });
}

// ── Share discovery + batch-add (v0.5.4) ─────────────────────────────────

export interface ListSharesResult {
  shares: string[];
  step: string | null;
  error: string | null;
}

export function useListShares() {
  return useMutation({
    mutationFn: (hostId: string) =>
      api.post<ListSharesResult>(`/hosts/${hostId}/list-shares`, {}),
  });
}

export interface AddSharesItem {
  name: string;
  share: string;
}

export interface AddSharesRequest {
  shares: AddSharesItem[];
  scan_schedule?: string | null;
  max_parallel_scanners?: number | null;
  exclude_patterns?: string[] | null;
  preferred_pool?: string | null;
  is_removable?: boolean | null;
}

export interface AddSharesResponse {
  created: number;
  skipped: number;
  sources: string[];  // ids of newly-created Source rows
}

export function useAddShares() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ hostId, body }: { hostId: string; body: AddSharesRequest }) =>
      api.post<AddSharesResponse>(`/hosts/${hostId}/add-shares`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      queryClient.invalidateQueries({ queryKey: ["hosts"] });
    },
  });
}

/** v0.28.1 — pure TCP "is the server up?" probe from the API. No
 * credentials, no share listing. Renamed from useTestHostConnection
 * to make the API's role explicit. Real reachability lives on the
 * scanners; see useTestSourceScanners / useTestHostShares.
 */
export interface HostOnlineCheckResult {
  result: {
    ok: boolean;
    step: string | null;
    error: string | null;
    tier?: string | null;
    warn?: string | null;
  };
  checked_at: string;
}

export function useHostOnlineCheck() {
  return useMutation({
    mutationFn: (hostId: string) =>
      api.post<HostOnlineCheckResult>(`/hosts/${hostId}/online-check`, {}),
  });
}

/** v0.28.1 — bulk reachability fan-out: every attached share × every
 * online scanner permitted to claim it. Each (source, scanner) pair
 * gets its own row in the response. Slow scanners come back as
 * `pending=true` and their results land later via the source-
 * reachability WS channel. */
export interface TestSharesResultRow {
  source_id: string;
  source_name: string;
  scanner_id: string;
  ok: boolean | null;
  step: string | null;
  error: string | null;
  pending: boolean;
  completed_at: string | null;
}

export interface TestSharesResponse {
  results: TestSharesResultRow[];
}

export function useTestHostShares() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      hostId,
      sourceIds,
    }: {
      hostId: string;
      sourceIds?: string[];
    }) =>
      api.post<TestSharesResponse>(
        `/hosts/${hostId}/test-shares`,
        { source_ids: sourceIds ?? null },
      ),
    onSuccess: (data, { hostId }) => {
      queryClient.invalidateQueries({
        queryKey: ["hosts", hostId, "scanner-summary"],
      });
      // Per-source reachability summaries change too — invalidate the
      // matching cards' badges. Use the source_ids that came back in
      // the response so we don't blow the whole sources cache away.
      const touched = new Set(data.results.map((r) => r.source_id));
      for (const sourceId of touched) {
        queryClient.invalidateQueries({
          queryKey: ["sources", sourceId, "reachability-summary"],
        });
        queryClient.invalidateQueries({
          queryKey: ["sources", sourceId, "scanner-reachability"],
        });
      }
    },
  });
}

// v0.5.7 — host-side eligibility-management hooks.

export interface HostScannerSummaryRow {
  scanner_id: string;
  name: string;
  pool: string | null;
  online: boolean;
  currently_allowed_count: number;
  reaches_count: number;
  unreachable_count: number;
  not_yet_probed_count: number;
  total_sources: number;
}

export function useHostScannerSummary(hostId: string | null) {
  return useQuery<HostScannerSummaryRow[]>({
    queryKey: ["hosts", hostId, "scanner-summary"],
    queryFn: () =>
      api.get<HostScannerSummaryRow[]>(
        `/hosts/${hostId}/scanner-reachability-summary`,
      ),
    enabled: hostId != null,
    staleTime: 10_000,
  });
}

export function useUpdateHostAllowedScanners() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ hostId, scannerIds }: { hostId: string; scannerIds: string[] }) =>
      api.patch<{ sources_touched: number; scanners_updated: number }>(
        `/hosts/${hostId}/allowed-scanners`,
        { scanner_ids: scannerIds },
      ),
    onSuccess: (_, { hostId }) => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      queryClient.invalidateQueries({ queryKey: ["scanners"] });
      queryClient.invalidateQueries({ queryKey: ["hosts"] });
      queryClient.invalidateQueries({
        queryKey: ["hosts", hostId, "scanner-summary"],
      });
    },
  });
}

// v0.5.7 — scanner-side eligibility-management hooks.

export interface SourceReachabilityRow {
  source_id: string;
  source_name: string;
  source_type: string;
  host_name: string | null;
  currently_allowed: boolean;
  ok: boolean | null;
  last_probed_at: string | null;
  step: string | null;
  error: string | null;
}

export function useScannerSourceReachability(scannerId: string | null) {
  return useQuery<SourceReachabilityRow[]>({
    queryKey: ["scanners", scannerId, "source-reachability"],
    queryFn: () =>
      api.get<SourceReachabilityRow[]>(
        `/scanners/${scannerId}/source-reachability`,
      ),
    enabled: scannerId != null,
    staleTime: 10_000,
  });
}

/**
 * v0.28.0 — scanner-side mirror of useTestSourceScanners. Triggers
 * on-demand probes of one (or all) sources from this scanner. Inline
 * for non-local sources; long-poll dispatched for local sources.
 */
export interface TestSourcesResultRow {
  source_id: string;
  ok: boolean | null;
  step: string | null;
  error: string | null;
  pending: boolean;
  completed_at: string | null;
}

export interface TestSourcesResponse {
  results: TestSourcesResultRow[];
}

export function useTestScannerSources() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      scannerId,
      sourceIds,
    }: {
      scannerId: string;
      sourceIds?: string[];
    }) =>
      api.post<TestSourcesResponse>(
        `/scanners/${scannerId}/test-sources`,
        { source_ids: sourceIds ?? null },
      ),
    onSuccess: (_, { scannerId }) => {
      queryClient.invalidateQueries({
        queryKey: ["scanners", scannerId, "source-reachability"],
      });
      queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
  });
}

export function useUpdateScannerAllowedSources() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ scannerId, sourceIds }: { scannerId: string; sourceIds: string[] | null }) =>
      api.patch<unknown>(`/scanners/${scannerId}`, {
        allowed_source_ids: sourceIds,
        clear_allowed_source_ids: sourceIds == null,
      }),
    onSuccess: (_, { scannerId }) => {
      queryClient.invalidateQueries({ queryKey: ["scanners"] });
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      queryClient.invalidateQueries({ queryKey: ["hosts"] });
      queryClient.invalidateQueries({
        queryKey: ["scanners", scannerId, "source-reachability"],
      });
    },
  });
}
