/**
 * Admin-gated read/write of a single server-settings key.
 *
 * Reads coalesce per (key) in React Query's cache; the api applies a
 * 5s TTL on its own cache, so even if multiple components subscribe
 * the round-trip count stays bounded. Mutations PATCH and bust both
 * caches (api-side via pubsub fan-out, client-side via invalidation).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { useAuth } from "./useAuth";

interface SettingValue<T> {
  key: string;
  value: T;
}

export function useServerSetting<T>(key: string, defaultValue: T) {
  const { isAdmin } = useAuth();
  const qc = useQueryClient();

  const query = useQuery<T>({
    queryKey: ["server-setting", key],
    queryFn: async () => {
      try {
        const r = await api.get<SettingValue<T>>(`/server-settings/${key}`);
        return r.value;
      } catch {
        // 404 is normal for unset keys; surface the default rather
        // than treating a missing setting as an error.
        return defaultValue;
      }
    },
    enabled: isAdmin,
    staleTime: 10_000,
  });

  const set = useMutation<SettingValue<T>, Error, T>({
    mutationFn: (value) => api.patch<SettingValue<T>>(
      `/server-settings/${key}`, { value },
    ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["server-setting", key] }),
  });

  return { value: query.data ?? defaultValue, query, set };
}

// ── Schema-driven bulk settings (Settings → Server) ────────────────

export type ServerSettingType =
  | "bool" | "int" | "str" | "list[str]" | "dict[str,int]" | "enum";

export type ServerSettingValue =
  | boolean | number | string | string[] | Record<string, number> | null;

export interface ServerSettingDef {
  key: string;
  type: ServerSettingType;
  group: string;
  description: string;
  secret: boolean;
  restart: boolean;
  default: ServerSettingValue;
  value: ServerSettingValue;
  choices: string[] | null;
  /** Secrets only: whether a value is stored. */
  set?: boolean;
}

export interface ServerSettingGroup {
  key: string;
  label: string;
  settings: ServerSettingDef[];
}

export interface ServerSettingsResponse {
  groups: ServerSettingGroup[];
}

const ALL_KEY = ["server-settings", "all"] as const;

export function useServerSettings() {
  const { isAdmin } = useAuth();
  const qc = useQueryClient();

  const query = useQuery<ServerSettingsResponse>({
    queryKey: ALL_KEY,
    queryFn: () => api.get<ServerSettingsResponse>("/server-settings"),
    enabled: isAdmin,
    staleTime: 10_000,
  });

  /** Atomic PATCH: the API 422s and applies nothing if any value is invalid. */
  const save = useMutation<ServerSettingsResponse | unknown, Error, Record<string, ServerSettingValue>>({
    mutationFn: (values) => api.patch("/server-settings", { values }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ALL_KEY });
      qc.invalidateQueries({ queryKey: ["server-setting"] });
    },
  });

  return { query, save };
}
