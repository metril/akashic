/**
 * Admin-gated CRUD over /api/v1/scanner-claim-tokens.
 *
 * The list query is the source of truth for the "Join tokens"
 * section. Mutations invalidate it so a freshly-minted token shows
 * up immediately, and a redeemed-by-scanner token's status flips
 * from active → used the next render.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { client, ok } from "../api/client";
import type { components } from "../api/schema";

type S = components["schemas"];
import { useScopes } from "./useScopes";

export type ClaimTokenSummary = S["ClaimTokenSummary"];

export type ClaimTokenCreated = S["ClaimTokenCreated"];

export type CreateTokenBody = S["ClaimTokenCreate"];

export function useScannerClaimTokens() {
  const { has } = useScopes();
  const canReadScanners = has("scanners:read");
  const qc = useQueryClient();

  const list = useQuery<ClaimTokenSummary[]>({
    queryKey: ["scanner-claim-tokens"],
    queryFn: async () => ok(await client.GET("/api/v1/scanner-claim-tokens")),
    enabled: canReadScanners,
    refetchInterval: 30_000,
  });

  const create = useMutation<ClaimTokenCreated, Error, CreateTokenBody>({
    mutationFn: async (body) => ok(await client.POST("/api/v1/scanner-claim-tokens", { body })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scanner-claim-tokens"] });
      // The freshly-created token can immediately be claimed by a
      // scanner, which adds a row to /scanners; invalidate so the
      // active-list reflects that the moment it lands.
      qc.invalidateQueries({ queryKey: ["scanners"] });
    },
  });

  const revoke = useMutation<void, Error, string>({
    mutationFn: async (id) => {
      await client.DELETE("/api/v1/scanner-claim-tokens/{token_id}", { params: { path: { token_id: id } } });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scanner-claim-tokens"] }),
  });

  return { list, create, revoke };
}
