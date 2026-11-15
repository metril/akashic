/**
 * React Query bindings for the source-OAuth foundation
 * (see api/akashic/routers/source_oauth.py).
 *
 * Two resource families:
 *  - "providers" — per-provider client_id / client_secret config
 *    set by the deployment owner.
 *  - "credentials" — per-source OAuth grants (refresh tokens) the
 *    API has stored. PR-A surfaces them as a "Connected accounts"
 *    list; PR-C wires individual rows to source records.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { client, ok } from "../api/client";
import type { components } from "../api/schema";

type S = components["schemas"];

export type OAuthProviderName = "google" | "microsoft" | "dropbox";

export type OAuthProviderSummary = S["OAuthAppConfigSummary"];

export type OAuthProviderUpsert = S["OAuthAppConfigUpsert"];

export type OAuthCredentialSummary = S["OAuthCredentialSummary"];

export type OAuthStartResponse = S["OAuthStartResponse"];

export type OAuthRefreshResponse = S["OAuthRefreshResponse"];

export const PROVIDER_LABELS: Record<OAuthProviderName, string> = {
  google: "Google (Drive)",
  microsoft: "Microsoft (OneDrive)",
  dropbox: "Dropbox",
};

export function useOAuthProviders() {
  return useQuery<OAuthProviderSummary[]>({
    queryKey: ["oauth", "providers"],
    queryFn: async () => ok(await client.GET("/api/v1/oauth/providers")),
  });
}

export function useUpsertOAuthProvider() {
  const qc = useQueryClient();
  return useMutation<
    OAuthProviderSummary,
    Error,
    { provider: OAuthProviderName; body: OAuthProviderUpsert }
  >({
    mutationFn: async ({ provider, body }) =>
      ok(await client.PUT("/api/v1/oauth/providers/{provider}", { params: { path: { provider } }, body })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["oauth", "providers"] });
    },
  });
}

export function useDeleteOAuthProvider() {
  const qc = useQueryClient();
  return useMutation<void, Error, OAuthProviderName>({
    mutationFn: async (provider) => {
      await client.DELETE("/api/v1/oauth/providers/{provider}", { params: { path: { provider } } });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["oauth", "providers"] });
    },
  });
}

export function useOAuthCredentials() {
  return useQuery<OAuthCredentialSummary[]>({
    queryKey: ["oauth", "credentials"],
    queryFn: async () => ok(await client.GET("/api/v1/oauth/credentials")),
  });
}

export function useDeleteOAuthCredential() {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: async (id) => {
      await client.DELETE("/api/v1/oauth/credentials/{credential_id}", { params: { path: { credential_id: id } } });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["oauth", "credentials"] });
    },
  });
}

export function useStartOAuth() {
  return useMutation<
    OAuthStartResponse,
    Error,
    { provider: OAuthProviderName; mode: "associate" | "test" }
  >({
    mutationFn: async ({ provider, mode }) =>
      ok(await client.POST("/api/v1/oauth/start", { body: { provider, mode } })),
  });
}

export function useRefreshOAuthCredential() {
  const qc = useQueryClient();
  return useMutation<OAuthRefreshResponse, Error, string>({
    mutationFn: async (id) =>
      ok(await client.POST("/api/v1/oauth/credentials/{credential_id}/refresh", { params: { path: { credential_id: id } } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["oauth", "credentials"] });
    },
  });
}
