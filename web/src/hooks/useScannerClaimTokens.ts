/**
 * Admin-gated CRUD over /api/scanner-claim-tokens.
 *
 * The list query is the source of truth for the "Join tokens"
 * section. Mutations invalidate it so a freshly-minted token shows
 * up immediately, and a redeemed-by-scanner token's status flips
 * from active → used the next render.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { useAuth } from "./useAuth";

export interface ClaimTokenSummary {
  id: string;
  label: string;
  pool: string;
  allowed_source_ids: string[] | null;
  allowed_scan_types: string[] | null;
  status: "active" | "used" | "expired";
  created_at: string;
  expires_at: string;
  used_at: string | null;
  used_by_scanner_id: string | null;
}

export interface ClaimTokenCreated {
  id: string;
  label: string;
  pool: string;
  allowed_source_ids: string[] | null;
  allowed_scan_types: string[] | null;
  token: string;
  expires_at: string;
  snippets: {
    shell: string;
    docker_run: string;
    compose: string;
    k8s: string;
    env: string;
  };
}

export interface CreateTokenBody {
  label: string;
  pool?: string;
  ttl_minutes?: number;
  allowed_source_ids?: string[];
  allowed_scan_types?: string[];
}

export function useScannerClaimTokens() {
  const { isAdmin } = useAuth();
  const qc = useQueryClient();

  const list = useQuery<ClaimTokenSummary[]>({
    queryKey: ["scanner-claim-tokens"],
    queryFn: () => api.get<ClaimTokenSummary[]>("/scanner-claim-tokens"),
    enabled: isAdmin,
    refetchInterval: 30_000,
  });

  const create = useMutation<ClaimTokenCreated, Error, CreateTokenBody>({
    mutationFn: (body) => api.post<ClaimTokenCreated>("/scanner-claim-tokens", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scanner-claim-tokens"] });
      // The freshly-created token can immediately be claimed by a
      // scanner, which adds a row to /scanners; invalidate so the
      // active-list reflects that the moment it lands.
      qc.invalidateQueries({ queryKey: ["scanners"] });
    },
  });

  const revoke = useMutation<void, Error, string>({
    mutationFn: (id) => api.delete<void>(`/scanner-claim-tokens/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scanner-claim-tokens"] }),
  });

  return { list, create, revoke };
}
