import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Scan } from "../types";

// Most-recent N scans across all sources, in started_at desc order
// (server already sorts that way). Used by the dashboard's Recent
// activity card. Refetches on focus so the card stays fresh after a
// scan completes elsewhere.
export function useRecentScans(limit = 6) {
  return useQuery<Scan[]>({
    queryKey: ["scans", "recent", limit],
    queryFn: () => api.get<Scan[]>(`/scans?limit=${limit}`),
    staleTime: 30 * 1000,
  });
}
