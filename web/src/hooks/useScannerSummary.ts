/**
 * Lightweight admin-gated query for the count of registered + online
 * scanner agents. Used by the Sources page's "no scanner registered"
 * banner. Cheaper than re-fetching the full /api/v1/scanners list per
 * page mount.
 */
import { useQuery } from "@tanstack/react-query";

import { client, ok } from "../api/client";
import type { components } from "../api/schema";

type S = components["schemas"];
import { useScopes } from "./useScopes";

export function useScannerSummary() {
  const { has } = useScopes();
  const canReadScanners = has("scanners:read");
  return useQuery<S["ScannerCounts"]>({
    queryKey: ["scanners", "summary"],
    queryFn: async () => ok(await client.GET("/api/v1/scanners/summary")),
    // Non-admins don't see the banner so the data is irrelevant.
    enabled: canReadScanners,
    staleTime: 30_000,
    // Offline is time-derived from last_seen_at (no triggering event),
    // so a poll is the only way the online count self-heals when a
    // scanner goes quiet. Scanner-lifecycle events still invalidate
    // ["scanners"] live via useLiveDataRefresh; this is the backstop.
    refetchInterval: 30_000,
  });
}
