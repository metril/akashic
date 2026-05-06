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

import { api } from "../api/client";

export type OAuthProviderName = "google" | "microsoft" | "dropbox" | "box";

export interface OAuthProviderSummary {
  provider: OAuthProviderName;
  client_id: string;
  has_secret: boolean;
  redirect_uri: string;
  configured_at: string;
  updated_at: string;
}

export interface OAuthProviderUpsert {
  client_id: string;
  client_secret: string;
  redirect_uri: string;
}

export interface OAuthCredentialSummary {
  id: string;
  source_id: string | null;
  provider: OAuthProviderName;
  account_email: string | null;
  account_label: string | null;
  scope: string | null;
  access_token_expires_at: string | null;
  created_at: string;
  updated_at: string;
  // v0.21.0 — name of the Source this credential is attached to.
  // Null when unattached. Surfaced on the SettingsOAuth row.
  source_name?: string | null;
}

export interface OAuthStartResponse {
  authorization_url: string;
  state: string;
}

export interface OAuthRefreshResponse {
  access_token: string;
  expires_at: string | null;
}

export const PROVIDER_LABELS: Record<OAuthProviderName, string> = {
  google: "Google (Drive)",
  microsoft: "Microsoft (OneDrive / SharePoint)",
  dropbox: "Dropbox",
  box: "Box",
};

export function useOAuthProviders() {
  return useQuery<OAuthProviderSummary[]>({
    queryKey: ["oauth", "providers"],
    queryFn: () => api.get<OAuthProviderSummary[]>("/oauth/providers"),
  });
}

export function useUpsertOAuthProvider() {
  const qc = useQueryClient();
  return useMutation<
    OAuthProviderSummary,
    Error,
    { provider: OAuthProviderName; body: OAuthProviderUpsert }
  >({
    mutationFn: ({ provider, body }) =>
      api.put<OAuthProviderSummary>(`/oauth/providers/${provider}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["oauth", "providers"] });
    },
  });
}

export function useDeleteOAuthProvider() {
  const qc = useQueryClient();
  return useMutation<void, Error, OAuthProviderName>({
    mutationFn: (provider) => api.delete(`/oauth/providers/${provider}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["oauth", "providers"] });
    },
  });
}

export function useOAuthCredentials() {
  return useQuery<OAuthCredentialSummary[]>({
    queryKey: ["oauth", "credentials"],
    queryFn: () => api.get<OAuthCredentialSummary[]>("/oauth/credentials"),
  });
}

export function useDeleteOAuthCredential() {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (id) => api.delete(`/oauth/credentials/${id}`),
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
    mutationFn: ({ provider, mode }) =>
      api.post<OAuthStartResponse>("/oauth/start", { provider, mode }),
  });
}

export function useRefreshOAuthCredential() {
  const qc = useQueryClient();
  return useMutation<OAuthRefreshResponse, Error, string>({
    mutationFn: (id) =>
      api.post<OAuthRefreshResponse>(`/oauth/credentials/${id}/refresh`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["oauth", "credentials"] });
    },
  });
}
