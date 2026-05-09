/**
 * CRUD over /api/credential-profiles.
 *
 * Profiles are referenced by hosts and sources via
 * `credential_profile_id`. The summary list (`useCredentialProfiles`)
 * is open to any authenticated user so the picker dropdown on the
 * host/source create forms can populate. Full CRUD (create, get,
 * patch, delete) is admin-only — handled in the routers, surfaced
 * here as React Query mutations.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";

export interface CredentialProfileSummary {
  id: string;
  name: string;
  type: "smb" | "nfs" | "s3";
  description: string | null;
}

export interface CredentialProfile {
  id: string;
  name: string;
  type: "smb" | "nfs" | "s3";
  /** Secret values arrive as "***" — see schemas/source.py _scrub_config. */
  credentials: Record<string, unknown>;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface CredentialProfileCreate {
  name: string;
  type: "smb" | "nfs" | "s3";
  credentials: Record<string, unknown>;
  description?: string | null;
}

export interface CredentialProfileUpdate {
  name?: string;
  description?: string | null;
  /** Partial. Values "***" / "********" mean "preserve stored value". */
  credentials?: Record<string, unknown>;
}

const KEY = ["credential-profiles"] as const;

export function useCredentialProfiles(type?: CredentialProfileSummary["type"]) {
  return useQuery<CredentialProfileSummary[]>({
    queryKey: type ? ["credential-profiles", type] : [...KEY],
    queryFn: () => {
      const q = type ? `?type=${encodeURIComponent(type)}` : "";
      return api.get<CredentialProfileSummary[]>(`/credential-profiles${q}`);
    },
  });
}

export function useCredentialProfile(id: string | null) {
  return useQuery<CredentialProfile>({
    queryKey: ["credential-profile", id],
    queryFn: () => api.get<CredentialProfile>(`/credential-profiles/${id}`),
    enabled: id != null,
  });
}

export function useCreateCredentialProfile() {
  const qc = useQueryClient();
  return useMutation<CredentialProfile, Error, CredentialProfileCreate>({
    mutationFn: (body) =>
      api.post<CredentialProfile>("/credential-profiles", body),
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
    mutationFn: ({ id, body }) =>
      api.patch<CredentialProfile>(`/credential-profiles/${id}`, body),
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
    mutationFn: (id) => api.delete<void>(`/credential-profiles/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}
