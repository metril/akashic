import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { AdminApiToken, ApiToken, ApiTokenCreate, ApiTokenCreated, ScopeCatalog } from "../types";

export function useScopeCatalog() {
  return useQuery<ScopeCatalog>({
    queryKey: ["scopes"],
    queryFn: () => api.get<ScopeCatalog>("/scopes"),
    staleTime: 60 * 60 * 1000,
  });
}

export function useMyTokens() {
  return useQuery<ApiToken[]>({
    queryKey: ["api-tokens", "mine"],
    queryFn: () => api.get<ApiToken[]>("/me/tokens"),
  });
}

export function useAllTokens(enabled: boolean) {
  return useQuery<AdminApiToken[]>({
    queryKey: ["api-tokens", "all"],
    queryFn: () => api.get<AdminApiToken[]>("/admin/tokens"),
    enabled,
  });
}

export function useTokenMutations() {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["api-tokens"] });
  return {
    create: useMutation<ApiTokenCreated, Error, ApiTokenCreate>({
      mutationFn: (body) => api.post<ApiTokenCreated>("/me/tokens", body),
      onSuccess: refresh,
    }),
    rename: useMutation<ApiToken, Error, { id: string; name: string }>({
      mutationFn: ({ id, name }) => api.patch<ApiToken>(`/me/tokens/${id}`, { name }),
      onSuccess: refresh,
    }),
    revoke: useMutation<void, Error, string>({
      mutationFn: (id) => api.delete<void>(`/me/tokens/${id}`),
      onSuccess: refresh,
    }),
    adminRevoke: useMutation<void, Error, string>({
      mutationFn: (id) => api.delete<void>(`/admin/tokens/${id}`),
      onSuccess: refresh,
    }),
  };
}
