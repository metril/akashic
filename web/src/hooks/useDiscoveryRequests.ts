/**
 * Admin-gated CRUD over /api/v1/scanner-discovery-requests.
 *
 * The list query is the source of truth for the "Pending claims"
 * pane. We also subscribe to the /ws/v1/scanners stream and invalidate
 * on `scanner.discovery_*` events so a new request shows up live and
 * an approve/deny by another admin tab refreshes immediately.
 */
import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { client, ok } from "../api/client";
import type { components } from "../api/schema";

type S = components["schemas"];
import { useScopes } from "./useScopes";
import { useScannersStreamEvents } from "./useScannersStreamEvents";

export type DiscoveryRequest = S["DiscoveryAdminSummary"];

export type ApproveBody = S["ApproveBody"];

export type DenyBody = S["DenyBody"];

export function useDiscoveryRequests() {
  const { has } = useScopes();
  const canReadScanners = has("scanners:read");
  const qc = useQueryClient();

  const list = useQuery<DiscoveryRequest[]>({
    queryKey: ["scanner-discovery-requests"],
    queryFn: async () => ok(await client.GET("/api/v1/scanner-discovery-requests")),
    enabled: canReadScanners,
    refetchInterval: 30_000,
  });

  const approve = useMutation<
    S["DiscoveryApproved"],
    Error,
    { id: string; body: ApproveBody }
  >({
    mutationFn: async ({ id, body }) =>
      ok(
        await client.POST("/api/v1/scanner-discovery-requests/{discovery_id}/approve", {
          params: { path: { discovery_id: id } },
          body,
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scanner-discovery-requests"] });
      qc.invalidateQueries({ queryKey: ["scanners"] });
    },
  });

  const deny = useMutation<void, Error, { id: string; body: DenyBody }>({
    mutationFn: async ({ id, body }) => {
      await client.POST("/api/v1/scanner-discovery-requests/{discovery_id}/deny", {
        params: { path: { discovery_id: id } },
        body,
      });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scanner-discovery-requests"] }),
  });

  // Live invalidation: any scanner-lifecycle event re-fetches the
  // pending list. Cheap because the list is small (≤ a few rows).
  useScannersStreamEvents((event) => {
    if (event.kind.startsWith("scanner.discovery_")) {
      qc.invalidateQueries({ queryKey: ["scanner-discovery-requests"] });
    }
    if (
      event.kind === "scanner.discovery_approved" ||
      event.kind === "scanner.claim_redeemed"
    ) {
      qc.invalidateQueries({ queryKey: ["scanners"] });
    }
  });

  // Also invalidate once on mount so a freshly-loaded page that
  // missed the most-recent event still ends up consistent.
  useEffect(() => {
    if (!canReadScanners) return;
    qc.invalidateQueries({ queryKey: ["scanner-discovery-requests"] });
  }, [canReadScanners, qc]);

  return { list, approve, deny };
}
