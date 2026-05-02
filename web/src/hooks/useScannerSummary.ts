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
    // Refetch on focus is enough; we're not racing for a few-second
    // accuracy on this. Mutations to scanners (register / delete /
    // rotate) live on a different page and invalidate via the
    // ["scanners"] queryKey there — sympathetic invalidation works
    // because both keys start with "scanners".
    staleTime: 30_000,
  });
}
