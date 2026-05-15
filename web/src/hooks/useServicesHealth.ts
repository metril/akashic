/**
 * Admin-gated polling of /api/health/services and /activity.
 *
 * Liveness: 30 s poll — the sidebar chip is glanceable; faster polls
 * just heat up the dashboard for no visible benefit.
 * Activity: 10 s poll, only while the System Status page is mounted.
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { useAuth } from "./useAuth";

export interface ServiceLiveness {
  ok: boolean;
  latency_ms: number | null;
  error?: string | null;
}

export interface ServicesHealth {
  postgres: ServiceLiveness;
  redis: ServiceLiveness;
  meilisearch: ServiceLiveness;
  tika: ServiceLiveness;
  checked_at: string;
}

export interface TikaActivity {
  queue_depth: number | null;
  failed_count: number | null;
  extracted_total: number | null;
  extracted_last_5min: number | null;
  last_extracted_at: string | null;
  ok: boolean;
  error?: string | null;
}

export interface MeiliActivity {
  documents_in_index: number | null;
  pending_tasks: number | null;
  last_task_at: string | null;
  last_task_status: string | null;
  ok: boolean;
  error?: string | null;
}

export interface ServicesActivity {
  tika: TikaActivity;
  meilisearch: MeiliActivity;
  checked_at: string;
}

export function useServicesHealth(options: { enabled?: boolean } = {}) {
  const { isAdmin } = useAuth();
  return useQuery<ServicesHealth>({
    queryKey: ["health", "services"],
    queryFn: () => api.get<ServicesHealth>("/health/services"),
    enabled: isAdmin && (options.enabled ?? true),
    refetchInterval: 30_000,
    // Stale-while-revalidate so the chip doesn't flicker on each poll.
    staleTime: 25_000,
  });
}

export function useServicesActivity(options: { enabled?: boolean } = {}) {
  const { isAdmin } = useAuth();
  return useQuery<ServicesActivity>({
    queryKey: ["health", "services", "activity"],
    queryFn: () => api.get<ServicesActivity>("/health/services/activity"),
    enabled: isAdmin && (options.enabled ?? true),
    refetchInterval: 10_000,
    staleTime: 8_000,
  });
}
