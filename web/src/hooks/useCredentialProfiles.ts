/**
 * CRUD over /api/v1/credential-profiles.
 *
 * Profiles are referenced by hosts and sources via
 * `credential_profile_id`. The summary list (`useCredentialProfiles`)
 * is open to any authenticated user so the picker dropdown on the
 * host/source create forms can populate. Full CRUD (create, get,
 * patch, delete) is admin-only — handled in the routers, surfaced
 * here as React Query mutations.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { client, ok } from "../api/client";
import type { components } from "../api/schema";

type S = components["schemas"];

export type CredentialProfileSummary = S["CredentialProfileSummary"];

export type CredentialProfile = S["CredentialProfileResponse"];

export type CredentialProfileCreate = S["CredentialProfileCreate"];

export type CredentialProfileUpdate = S["CredentialProfileUpdate"];

const KEY = ["credential-profiles"] as const;

export function useCredentialProfiles(type?: CredentialProfileSummary["type"]) {
  return useQuery<CredentialProfileSummary[]>({
    queryKey: type ? ["credential-profiles", type] : [...KEY],
    queryFn: async () =>
      ok(await client.GET("/api/v1/credential-profiles", { params: { query: { type } } })),
  });
}

export function useCredentialProfile(id: string | null) {
  return useQuery<CredentialProfile>({
    queryKey: ["credential-profile", id],
    queryFn: async () =>
      ok(await client.GET("/api/v1/credential-profiles/{profile_id}", { params: { path: { profile_id: id! } } })),
    enabled: id != null,
  });
}

export function useCreateCredentialProfile() {
  const qc = useQueryClient();
  return useMutation<CredentialProfile, Error, CredentialProfileCreate>({
    mutationFn: async (body) => ok(await client.POST("/api/v1/credential-profiles", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useUpdateCredentialProfile() {
  const qc = useQueryClient();
  return useMutation<
    CredentialProfile,
    Error,
    { id: string; body: CredentialProfileUpdate }
  >({
    mutationFn: async ({ id, body }) =>
      ok(await client.PATCH("/api/v1/credential-profiles/{profile_id}", { params: { path: { profile_id: id } }, body })),
    onSuccess: (_, { id }) => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: ["credential-profile", id] });
      // Hosts/sources display via lazy="joined" so an updated profile
      // can change effective credentials. Invalidate consumers.
      qc.invalidateQueries({ queryKey: ["hosts"] });
      qc.invalidateQueries({ queryKey: ["sources"] });
    },
  });
}

export function useDeleteCredentialProfile() {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: async (id) => {
      await client.DELETE("/api/v1/credential-profiles/{profile_id}", { params: { path: { profile_id: id } } });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}
