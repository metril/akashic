/**
 * Admin-gated read/write of a single server-settings key.
 *
 * Reads coalesce per (key) in React Query's cache; the api applies a
 * 5s TTL on its own cache, so even if multiple components subscribe
 * the round-trip count stays bounded. Mutations PATCH and bust both
 * caches (api-side via pubsub fan-out, client-side via invalidation).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { client, ok } from "../api/client";
import type { components } from "../api/schema";

type S = components["schemas"];
import { useScopes } from "./useScopes";

export function useServerSetting<T>(key: string, defaultValue: T) {
  const { has } = useScopes();
  const canReadSettings = has("settings:read");
  const qc = useQueryClient();

  const query = useQuery<T>({
    queryKey: ["server-setting", key],
    queryFn: async () => {
      try {
        const r = ok(
          await client.GET("/api/v1/server-settings/{key}", { params: { path: { key } } }),
        );
        return r.value as T;
      } catch {
        // 404 is normal for unset keys; surface the default rather
        // than treating a missing setting as an error.
        return defaultValue;
      }
    },
    enabled: canReadSettings,
    staleTime: 10_000,
  });

  const set = useMutation<S["SettingValue"], Error, T>({
    mutationFn: async (value) =>
      ok(
        await client.PATCH("/api/v1/server-settings/{key}", {
          params: { path: { key } },
          body: { value },
        }),
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

export type ServerSettingDef = S["SettingDescription"];

export type ServerSettingGroup = S["SettingGroup"];

export type ServerSettingsResponse = S["SettingsListResponse"];

const ALL_KEY = ["server-settings", "all"] as const;

export function useServerSettings() {
  const { has } = useScopes();
  const canReadSettings = has("settings:read");
  const qc = useQueryClient();

  const query = useQuery<ServerSettingsResponse>({
    queryKey: ALL_KEY,
    queryFn: async () => ok(await client.GET("/api/v1/server-settings")),
    enabled: canReadSettings,
    staleTime: 10_000,
  });

  /** Atomic PATCH: the API 422s and applies nothing if any value is invalid. */
  const save = useMutation<unknown, Error, Record<string, ServerSettingValue>>({
    mutationFn: async (values) =>
      ok(await client.PATCH("/api/v1/server-settings", { body: { values } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ALL_KEY });
      qc.invalidateQueries({ queryKey: ["server-setting"] });
    },
  });

  return { query, save };
}
