import { useQuery } from "@tanstack/react-query";
import { client, ok } from "../api/client";
import type { Scan } from "../api/types";

// Most-recent N scans across all sources, in started_at desc order
// (server already sorts that way). Used by the dashboard's Recent
// activity card. Refetches on focus so the card stays fresh after a
// scan completes elsewhere.
export function useRecentScans(limit = 6) {
  return useQuery<Scan[]>({
    queryKey: ["scans", "recent", limit],
    queryFn: async () =>
      ok(await client.GET("/api/v1/scans", { params: { query: { limit } } })).items,
    staleTime: 30 * 1000,
  });
}
