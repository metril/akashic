/**
 * Admin-gated CRUD over /api/scanner-discovery-requests.
 *
 * The list query is the source of truth for the "Pending claims"
 * pane. We also subscribe to the /ws/scanners stream and invalidate
 * on `scanner.discovery_*` events so a new request shows up live and
 * an approve/deny by another admin tab refreshes immediately.
 */
import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { useAuth } from "./useAuth";
import { useScannersStreamEvents } from "./useScannersStreamEvents";

export interface DiscoveryRequest {
  id: string;
  pairing_code: string;
  hostname: string | null;
  agent_version: string | null;
  requested_pool: string | null;
  requested_at: string;
  expires_at: string;
  status: "pending" | "approved" | "denied" | "expired";
  decided_at: string | null;
  deny_reason: string | null;
  approved_scanner_id: string | null;
  key_fingerprint: string;
}

export interface ApproveBody {
  name: string;
  pool?: string;
}

export interface DenyBody {
  reason?: string;
}

export function useDiscoveryRequests() {
  const { isAdmin } = useAuth();
  const qc = useQueryClient();

  const list = useQuery<DiscoveryRequest[]>({
    queryKey: ["scanner-discovery-requests"],
    queryFn: () => api.get<DiscoveryRequest[]>("/scanner-discovery-requests"),
    enabled: isAdmin,
    refetchInterval: 30_000,
  });

  const approve = useMutation<
    { scanner_id: string; name: string; pool: string },
    Error,
    { id: string; body: ApproveBody }
  >({
    mutationFn: ({ id, body }) =>
      api.post<{ scanner_id: string; name: string; pool: string }>(
        `/scanner-discovery-requests/${id}/approve`,
        body,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scanner-discovery-requests"] });
      qc.invalidateQueries({ queryKey: ["scanners"] });
    },
  });

  const deny = useMutation<void, Error, { id: string; body: DenyBody }>({
    mutationFn: ({ id, body }) =>
      api.post<void>(`/scanner-discovery-requests/${id}/deny`, body),
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
    if (!isAdmin) return;
    qc.invalidateQueries({ queryKey: ["scanner-discovery-requests"] });
  }, [isAdmin, qc]);

  return { list, approve, deny };
}
