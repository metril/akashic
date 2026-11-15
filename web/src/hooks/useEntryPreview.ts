import { useQuery } from "@tanstack/react-query";
import { client, ok } from "../api/client";
import type { components } from "../api/schema";

type S = components["schemas"];

export type EntryPreview = S["PreviewResponse"];

export function useEntryPreview(entryId: string | null, enabled = true) {
  return useQuery<EntryPreview>({
    queryKey: ["entry-preview", entryId],
    queryFn: async () =>
      ok(await client.GET("/api/v1/entries/{entry_id}/preview", { params: { path: { entry_id: entryId! } } })),
    enabled: !!entryId && enabled,
    staleTime: Infinity,
  });
}
