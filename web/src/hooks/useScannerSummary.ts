/**
 * Lightweight admin-gated query for the count of registered + online
 * scanner agents. Used by the Sources page's "no scanner registered"
 * banner. Cheaper than re-fetching the full /api/scanners list per
 * page mount.
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { useAuth } from "./useAuth";

interface ScannerCounts {
  registered: number;
  online: number;
}

export function useScannerSummary() {
  const { isAdmin } = useAuth();
  return useQuery<ScannerCounts>({
    queryKey: ["scanners", "summary"],
    queryFn: () => api.get<ScannerCounts>("/scanners/summary"),
    // Non-admins don't see the banner so the data is irrelevant.
    enabled: isAdmin,
    staleTime: 30_000,
    // Offline is time-derived from last_seen_at (no triggering event),
    // so a poll is the only way the online count self-heals when a
    // scanner goes quiet. Scanner-lifecycle events still invalidate
    // ["scanners"] live via useLiveDataRefresh; this is the backstop.
    refetchInterval: 30_000,
  });
}
