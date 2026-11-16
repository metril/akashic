import { useMemo } from "react";
import type { SourceType } from "../components/sources/sourceTypes";
import { useQuery, useQueries, useMutation, useQueryClient } from "@tanstack/react-query";
import { client, ok } from "../api/client";
import type { components } from "../api/schema";
import type { Source, SourceListItem } from "../api/types";

type S = components["schemas"];

export function useSources() {
  return useQuery<SourceListItem[]>({
    queryKey: ["sources"],
    queryFn: async () => ok(await client.GET("/api/v1/sources")),
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
export function sourceDetailQueryOptions(sourceId: string | null) {
  return {
    queryKey: ["sources", sourceId, "detail"] as const,
    queryFn: async (): Promise<Source> =>
      ok(await client.GET("/api/v1/sources/{source_id}", { params: { path: { source_id: sourceId! } } })),
    enabled: sourceId != null,
    staleTime: 30_000,
  };
}

export function useSourceDetail(sourceId: string | null) {
  return useQuery<Source>(sourceDetailQueryOptions(sourceId));
}

/** Map of backing (local/nfs/smb/s3) source id -> type, for mirrors validation. */
export function useMirrorBackingTypes(): Map<string, SourceType> {
  const { data } = useSources();
  return useMemo(
    () =>
      new Map(
        (data ?? [])
          .filter((s) => MIRROR_BACKING_TYPES.has(s.type))
          .map((s) => [s.id, s.type as SourceType]),
      ),
    [data],
  );
}

export const MIRROR_BACKING_TYPES: ReadonlySet<string> = new Set(["local", "nfs", "smb", "s3"]);

/**
 * Backing source id -> app (immich/paperless) sources whose `mirrors`
 * name it. Reuses the cached per-source detail queries (30s stale).
 */
export function useMirroredBy(enabled = true): Map<string, { id: string; name: string }[]> {
  const { data } = useSources();
  const apps = useMemo(
    () =>
      enabled ? (data ?? []).filter((s) => s.type === "immich" || s.type === "paperless") : [],
    [data, enabled],
  );
  const details = useQueries({ queries: apps.map((s) => sourceDetailQueryOptions(s.id)) });
  const key = details.map((d) => d.dataUpdatedAt).join(",");
  return useMemo(() => {
    const out = new Map<string, { id: string; name: string }[]>();
    apps.forEach((app, i) => {
      const list = (details[i]?.data?.connection_config as { mirrors?: { source_id: string }[] } | undefined)?.mirrors;
      for (const id of new Set((list ?? []).map((m) => m.source_id))) {
        out.set(id, [...(out.get(id) ?? []), { id: app.id, name: app.name }]);
      }
    });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apps, key]);
}

export function useCreateSource() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (data: S["SourceCreate"]) => ok(await client.POST("/api/v1/sources", { body: data })),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
  });
}

export function useUpdateSource() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ id, data }: { id: string; data: S["SourceUpdate"] }) =>
      ok(await client.PATCH("/api/v1/sources/{source_id}", { params: { path: { source_id: id } }, body: data })),
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
export type TestScannersResultRow = S["TestScannersResultRow"];

export type TestScannersResponse = S["TestScannersResponse"];

export function useTestSourceScanners() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      sourceId,
      scannerIds,
    }: {
      sourceId: string;
      scannerIds?: string[];
    }) =>
      ok(
        await client.POST("/api/v1/sources/{source_id}/test-scanners", {
          params: { path: { source_id: sourceId } },
          body: { scanner_ids: scannerIds ?? null },
        }),
      ),
    onSuccess: (_, { sourceId }) => {
      queryClient.invalidateQueries({
        queryKey: ["sources", sourceId, "scanner-reachability"],
      });
      queryClient.invalidateQueries({
        queryKey: ["sources", sourceId, "reachability-summary"],
      });
      queryClient.invalidateQueries({
        queryKey: ["sources", sourceId, "reachability-history"],
      });
    },
  });
}

/**
 * Compact source-level reachability for badges + cards. Derived from
 * the latest reachability_results row (across all scanners) and the
 * latest successful scan, whichever is fresher.
 */
export type ReachabilitySummary = S["ReachabilitySummary"];

export function useReachabilitySummary(sourceId: string | null) {
  return useQuery<ReachabilitySummary>({
    queryKey: ["sources", sourceId, "reachability-summary"],
    queryFn: async () =>
      ok(
        await client.GET("/api/v1/sources/{source_id}/reachability-summary", {
          params: { path: { source_id: sourceId! } },
        }),
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

export type ScannerReachabilityRow = S["ScannerReachabilityRow"];

export function useSourceScannerReachability(sourceId: string | null) {
  return useQuery<ScannerReachabilityRow[]>({
    queryKey: ["sources", sourceId, "scanner-reachability"],
    queryFn: async () =>
      ok(
        await client.GET("/api/v1/sources/{source_id}/scanner-reachability", {
          params: { path: { source_id: sourceId! } },
        }),
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
export type ReachabilityOutcome = S["ReachabilityOutcome"];

export type PerScannerHistory = S["PerScannerHistory"];

export type ReachabilityHistory = S["ReachabilityHistory"];

export function useSourceReachabilityHistory(
  sourceId: string | null,
  enabled = true,
) {
  return useQuery<ReachabilityHistory>({
    queryKey: ["sources", sourceId, "reachability-history"],
    queryFn: async () =>
      ok(
        await client.GET("/api/v1/sources/{source_id}/reachability-history", {
          params: { path: { source_id: sourceId! } },
        }),
      ),
    enabled: sourceId != null && enabled,
    staleTime: 10_000,
  });
}

export function useUpdateSourceAllowedScanners() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ sourceId, scannerIds }: { sourceId: string; scannerIds: string[] }) =>
      ok(
        await client.PATCH("/api/v1/sources/{source_id}/allowed-scanners", {
          params: { path: { source_id: sourceId } },
          body: { scanner_ids: scannerIds },
        }),
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
    mutationFn: async ({ id, purgeEntries = false }: DeleteSourceArgs) => {
      await client.DELETE("/api/v1/sources/{source_id}", {
        params: { path: { source_id: id }, query: { purge_entries: purgeEntries } },
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      // Search results may include orphaned entries from this source
      // now (in the preserve flavour) — invalidate so the UI sees them.
      queryClient.invalidateQueries({ queryKey: ["search"] });
    },
  });
}
