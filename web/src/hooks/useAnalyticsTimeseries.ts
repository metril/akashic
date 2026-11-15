import { useQuery } from "@tanstack/react-query";

import { client, ok } from "../api/client";
import type { components } from "../api/schema";

type S = components["schemas"];

export type TimeseriesPoint = S["SeriesPoint"];

export type ForecastPoint = S["ForecastPoint"];

export type ForecastResponse = S["ForecastResponse"];

export type ExtensionTrendPoint = S["ExtensionTrendPoint"];

export type OwnerDistribution = S["OwnerDistribution"];

export function useStorageTimeseries(
  sourceId: string | null | undefined,
  metric: "size" | "count" = "size",
  days = 90,
) {
  return useQuery<TimeseriesPoint[]>({
    queryKey: ["analytics", "timeseries", sourceId, metric, days],
    queryFn: async () =>
      ok(
        await client.GET("/api/v1/analytics/timeseries", {
          params: { query: { source_id: sourceId!, metric, days } },
        }),
      ),
    enabled: Boolean(sourceId),
  });
}

export function useStorageForecast(
  sourceId: string | null | undefined,
  horizonDays = 30,
  lookbackDays = 90,
) {
  return useQuery<ForecastResponse>({
    queryKey: ["analytics", "forecast", sourceId, horizonDays, lookbackDays],
    queryFn: async () =>
      ok(
        await client.GET("/api/v1/analytics/forecast", {
          params: { query: { source_id: sourceId!, horizon_days: horizonDays, lookback_days: lookbackDays } },
        }),
      ),
    enabled: Boolean(sourceId),
  });
}

export function useExtensionTrend(
  sourceId: string | null | undefined,
  extensions: string[],
  days = 90,
) {
  const extParam = extensions.join(",");
  return useQuery<Record<string, ExtensionTrendPoint[]>>({
    queryKey: ["analytics", "extension-trend", sourceId, extParam, days],
    queryFn: async () =>
      ok(
        await client.GET("/api/v1/analytics/extension-trend", {
          params: { query: { source_id: sourceId!, extensions: extParam, days } },
        }),
      ) as Record<string, ExtensionTrendPoint[]>,
    enabled: Boolean(sourceId) && extensions.length > 0,
  });
}

export function useOwnerDistribution(sourceId: string | null | undefined) {
  return useQuery<OwnerDistribution>({
    queryKey: ["analytics", "owner-distribution", sourceId],
    queryFn: async () =>
      ok(
        await client.GET("/api/v1/analytics/owner-distribution", {
          params: { query: { source_id: sourceId! } },
        }),
      ),
    enabled: Boolean(sourceId),
  });
}
