import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";

export interface TimeseriesPoint {
  taken_at: string;
  value: number;
}

export interface ForecastPoint {
  taken_at: string;
  value: number;
  low: number;
  high: number;
}

export interface ForecastResponse {
  history: TimeseriesPoint[];
  forecast:
    | {
        points: ForecastPoint[];
        slope_bytes_per_day: number;
        horizon_days: number;
      }
    | null;
  reason: string;
}

export interface ExtensionTrendPoint {
  taken_at: string;
  n: number;
  bytes: number;
}

export interface OwnerDistribution {
  taken_at: string | null;
  owners: { owner: string; n: number; bytes: number }[];
}

export function useStorageTimeseries(
  sourceId: string | null | undefined,
  metric: "size" | "count" = "size",
  days = 90,
) {
  return useQuery<TimeseriesPoint[]>({
    queryKey: ["analytics", "timeseries", sourceId, metric, days],
    queryFn: () =>
      api.get<TimeseriesPoint[]>(
        `/analytics/timeseries?source_id=${sourceId}&metric=${metric}&days=${days}`,
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
    queryFn: () =>
      api.get<ForecastResponse>(
        `/analytics/forecast?source_id=${sourceId}&horizon_days=${horizonDays}&lookback_days=${lookbackDays}`,
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
    queryFn: () =>
      api.get<Record<string, ExtensionTrendPoint[]>>(
        `/analytics/extension-trend?source_id=${sourceId}&extensions=${encodeURIComponent(extParam)}&days=${days}`,
      ),
    enabled: Boolean(sourceId) && extensions.length > 0,
  });
}

export function useOwnerDistribution(sourceId: string | null | undefined) {
  return useQuery<OwnerDistribution>({
    queryKey: ["analytics", "owner-distribution", sourceId],
    queryFn: () =>
      api.get<OwnerDistribution>(
        `/analytics/owner-distribution?source_id=${sourceId}`,
      ),
    enabled: Boolean(sourceId),
  });
}
