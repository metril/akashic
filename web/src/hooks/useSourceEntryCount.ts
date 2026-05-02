/**
 * Cheap query: how many indexed entries does this source have?
 * Powers the blast-radius display on the delete-source modal so the
 * operator knows whether they're about to nuke a 12-file test
 * source or a 50,000-file production catalog.
 *
 * Only enabled when explicitly fetched (the modal opens) — no need
 * to spam the count endpoint on the Sources list page itself.
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";

interface SourceEntryCount {
  count: number;
}

export function useSourceEntryCount(sourceId: string | null) {
  return useQuery<SourceEntryCount>({
    queryKey: ["sources", sourceId, "entry-count"],
    queryFn: () => api.get<SourceEntryCount>(`/sources/${sourceId}/entry-count`),
    enabled: sourceId != null,
    staleTime: 10_000,
  });
}
