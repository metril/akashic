import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client, ok } from "../api/client";
import type { AdminApiToken, ApiToken, ApiTokenCreate, ApiTokenCreated, ScopeCatalog } from "../api/types";

export function useScopeCatalog() {
  return useQuery<ScopeCatalog>({
    queryKey: ["scopes"],
    queryFn: async () => ok(await client.GET("/api/v1/scopes")),
    staleTime: 60 * 60 * 1000,
  });
}

export function useMyTokens() {
  return useQuery<ApiToken[]>({
    queryKey: ["api-tokens", "mine"],
    queryFn: async () => ok(await client.GET("/api/v1/me/tokens")),
  });
}

export function useAllTokens(enabled: boolean) {
  return useQuery<AdminApiToken[]>({
    queryKey: ["api-tokens", "all"],
    queryFn: async () => ok(await client.GET("/api/v1/admin/tokens")),
    enabled,
  });
}

export function useTokenMutations() {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["api-tokens"] });
  return {
    create: useMutation<ApiTokenCreated, Error, ApiTokenCreate>({
      mutationFn: async (body) => ok(await client.POST("/api/v1/me/tokens", { body })),
      onSuccess: refresh,
    }),
    rename: useMutation<ApiToken, Error, { id: string; name: string }>({
      mutationFn: async ({ id, name }) =>
        ok(await client.PATCH("/api/v1/me/tokens/{token_id}", { params: { path: { token_id: id } }, body: { name } })),
      onSuccess: refresh,
    }),
    revoke: useMutation<void, Error, string>({
      mutationFn: async (id) => {
        await client.DELETE("/api/v1/me/tokens/{token_id}", { params: { path: { token_id: id } } });
      },
      onSuccess: refresh,
    }),
    adminRevoke: useMutation<void, Error, string>({
      mutationFn: async (id) => {
        await client.DELETE("/api/v1/admin/tokens/{token_id}", { params: { path: { token_id: id } } });
      },
      onSuccess: refresh,
    }),
  };
}
