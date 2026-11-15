/**
 * Admin-gated polling of /api/v1/health/services and /activity.
 *
 * Liveness: 30 s poll — the sidebar chip is glanceable; faster polls
 * just heat up the dashboard for no visible benefit.
 * Activity: 10 s poll, only while the System Status page is mounted.
 */
import { useQuery } from "@tanstack/react-query";

import { client, ok } from "../api/client";
import type { components } from "../api/schema";

type S = components["schemas"];
import { useScopes } from "./useScopes";

export type ServiceLiveness = S["ServiceProbe"];

export type ServicesHealth = S["ServicesHealth"];

export type TikaActivity = S["TikaActivity"];

export type MeiliActivity = S["MeiliActivity"];

export type ServicesActivity = S["ServicesActivity"];

export function useServicesHealth(options: { enabled?: boolean } = {}) {
  const { has } = useScopes();
  const canReadHealth = has("health:read");
  return useQuery<ServicesHealth>({
    queryKey: ["health", "services"],
    queryFn: async () => ok(await client.GET("/api/v1/health/services")),
    enabled: canReadHealth && (options.enabled ?? true),
    refetchInterval: 30_000,
    // Stale-while-revalidate so the chip doesn't flicker on each poll.
    staleTime: 25_000,
  });
}

export function useServicesActivity(options: { enabled?: boolean } = {}) {
  const { has } = useScopes();
  const canReadHealth = has("health:read");
  return useQuery<ServicesActivity>({
    queryKey: ["health", "services", "activity"],
    queryFn: async () => ok(await client.GET("/api/v1/health/services/activity")),
    enabled: canReadHealth && (options.enabled ?? true),
    refetchInterval: 10_000,
    staleTime: 8_000,
  });
}
