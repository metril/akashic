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

import { client, ok } from "../api/client";
import type { components } from "../api/schema";

type S = components["schemas"];

export function useSourceEntryCount(sourceId: string | null) {
  return useQuery<S["SourceEntryCount"]>({
    queryKey: ["sources", sourceId, "entry-count"],
    queryFn: async () =>
      ok(await client.GET("/api/v1/sources/{source_id}/entry-count", { params: { path: { source_id: sourceId! } } })),
    enabled: sourceId != null,
    staleTime: 10_000,
  });
}
