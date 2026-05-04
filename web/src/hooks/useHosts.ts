import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Host } from "../types";

export function useHosts() {
  return useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => api.get<Host[]>("/hosts"),
  });
}

export function useHostDetail(hostId: string | null) {
  return useQuery<Host>({
    queryKey: ["hosts", hostId, "detail"],
    queryFn: () => api.get<Host>(`/hosts/${hostId}`),
    enabled: hostId != null,
    staleTime: 30_000,
  });
}

export interface CreateHostInput {
  name: string;
  type: string;
  connection_config: Record<string, unknown>;
}

export function useCreateHost() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateHostInput) => api.post<Host>("/hosts", data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["hosts"] });
    },
  });
}

export interface UpdateHostInput {
  name?: string;
  connection_config?: Record<string, unknown>;
}

export function useUpdateHost() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: UpdateHostInput }) =>
      api.patch<Host>(`/hosts/${id}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["hosts"] });
      queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
  });
}

export function useDeleteHost() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/hosts/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["hosts"] });
    },
  });
}

// ── Share discovery + batch-add (v0.5.4) ─────────────────────────────────

export interface ListSharesResult {
  shares: string[];
  step: string | null;
  error: string | null;
}

export function useListShares() {
  return useMutation({
    mutationFn: (hostId: string) =>
      api.post<ListSharesResult>(`/hosts/${hostId}/list-shares`, {}),
  });
}

export interface AddSharesItem {
  name: string;
  share: string;
}

export interface AddSharesRequest {
  shares: AddSharesItem[];
  scan_schedule?: string | null;
  max_parallel_scanners?: number | null;
  exclude_patterns?: string[] | null;
  preferred_pool?: string | null;
  is_removable?: boolean | null;
}

export interface AddSharesResponse {
  created: number;
  skipped: number;
  sources: string[];  // ids of newly-created Source rows
}

export function useAddShares() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ hostId, body }: { hostId: string; body: AddSharesRequest }) =>
      api.post<AddSharesResponse>(`/hosts/${hostId}/add-shares`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      queryClient.invalidateQueries({ queryKey: ["hosts"] });
    },
  });
}

export interface HostTestResult {
  result: {
    ok: boolean;
    step: string | null;
    error: string | null;
    tier?: string | null;
    warn?: string | null;
  };
  checked_at: string;
}

export function useTestHostConnection() {
  return useMutation({
    mutationFn: (hostId: string) =>
      api.post<HostTestResult>(`/hosts/${hostId}/test-connection`, {}),
  });
}
