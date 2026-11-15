import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client, ok } from "../api/client";
import type { components } from "../api/schema";
import type { Host } from "../api/types";

type S = components["schemas"];

export function useHosts() {
  return useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: async () => ok(await client.GET("/api/v1/hosts")),
  });
}

export function useHostDetail(hostId: string | null) {
  return useQuery<Host>({
    queryKey: ["hosts", hostId, "detail"],
    queryFn: async () =>
      ok(await client.GET("/api/v1/hosts/{host_id}", { params: { path: { host_id: hostId! } } })),
    enabled: hostId != null,
    staleTime: 30_000,
  });
}

export type CreateHostInput = S["HostCreate"];

export function useCreateHost() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: CreateHostInput) => ok(await client.POST("/api/v1/hosts", { body: data })),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["hosts"] });
    },
  });
}

export type UpdateHostInput = S["HostUpdate"];

export function useUpdateHost() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, data }: { id: string; data: UpdateHostInput }) =>
      ok(await client.PATCH("/api/v1/hosts/{host_id}", { params: { path: { host_id: id } }, body: data })),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["hosts"] });
      queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
  });
}

export function useDeleteHost() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await client.DELETE("/api/v1/hosts/{host_id}", { params: { path: { host_id: id } } });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["hosts"] });
    },
  });
}

// ── Share discovery + batch-add (v0.5.4) ─────────────────────────────────

export type ListSharesResult = S["ListSharesResponse"];

export function useListShares() {
  return useMutation({
    mutationFn: async (hostId: string) =>
      ok(await client.POST("/api/v1/hosts/{host_id}/list-shares", { params: { path: { host_id: hostId } } })),
  });
}

export type AddSharesItem = S["AddSharesItem"];

export type AddSharesRequest = S["AddSharesRequest"];

export type AddSharesResponse = S["AddSharesResponse"];

export function useAddShares() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ hostId, body }: { hostId: string; body: AddSharesRequest }) =>
      ok(await client.POST("/api/v1/hosts/{host_id}/add-shares", { params: { path: { host_id: hostId } }, body })),
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
export type HostOnlineCheckResult = S["OnlineCheckResponse"];

export function useHostOnlineCheck() {
  return useMutation({
    mutationFn: async (hostId: string) =>
      ok(await client.POST("/api/v1/hosts/{host_id}/online-check", { params: { path: { host_id: hostId } } })),
  });
}

/** v0.28.1 — bulk reachability fan-out: every attached share × every
 * online scanner permitted to claim it. Each (source, scanner) pair
 * gets its own row in the response. Slow scanners come back as
 * `pending=true` and their results land later via the source-
 * reachability WS channel. */
export type TestSharesResultRow = S["TestSharesResultRow"];

export type TestSharesResponse = S["TestSharesResponse"];

export function useTestHostShares() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      hostId,
      sourceIds,
    }: {
      hostId: string;
      sourceIds?: string[];
    }) =>
      ok(
        await client.POST("/api/v1/hosts/{host_id}/test-shares", {
          params: { path: { host_id: hostId } },
          body: { source_ids: sourceIds ?? null },
        }),
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

export type HostScannerSummaryRow = S["HostScannerSummaryRow"];

export function useHostScannerSummary(hostId: string | null) {
  return useQuery<HostScannerSummaryRow[]>({
    queryKey: ["hosts", hostId, "scanner-summary"],
    queryFn: async () =>
      ok(
        await client.GET("/api/v1/hosts/{host_id}/scanner-reachability-summary", {
          params: { path: { host_id: hostId! } },
        }),
      ),
    enabled: hostId != null,
    staleTime: 10_000,
  });
}

export function useUpdateHostAllowedScanners() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ hostId, scannerIds }: { hostId: string; scannerIds: string[] }) =>
      ok(
        await client.PATCH("/api/v1/hosts/{host_id}/allowed-scanners", {
          params: { path: { host_id: hostId } },
          body: { scanner_ids: scannerIds },
        }),
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

export type SourceReachabilityRow = S["SourceReachabilityRow"];

export function useScannerSourceReachability(scannerId: string | null) {
  return useQuery<SourceReachabilityRow[]>({
    queryKey: ["scanners", scannerId, "source-reachability"],
    queryFn: async () =>
      ok(
        await client.GET("/api/v1/scanners/{scanner_id}/source-reachability", {
          params: { path: { scanner_id: scannerId! } },
        }),
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
export type TestSourcesResultRow = S["TestSourcesResultRow"];

export type TestSourcesResponse = S["TestSourcesResponse"];

export function useTestScannerSources() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      scannerId,
      sourceIds,
    }: {
      scannerId: string;
      sourceIds?: string[];
    }) =>
      ok(
        await client.POST("/api/v1/scanners/{scanner_id}/test-sources", {
          params: { path: { scanner_id: scannerId } },
          body: { source_ids: sourceIds ?? null },
        }),
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
    mutationFn: async ({ scannerId, sourceIds }: { scannerId: string; sourceIds: string[] | null }) =>
      ok(
        await client.PATCH("/api/v1/scanners/{scanner_id}", {
          params: { path: { scanner_id: scannerId } },
          body: {
            allowed_source_ids: sourceIds,
            clear_allowed_source_ids: sourceIds == null,
          },
        }),
      ),
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
