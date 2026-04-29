import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

export interface EntryPreview {
  encoding: string | null;
  text: string | null;
  truncated: boolean;
  byte_size_total: number;
  binary: boolean;
}

export function useEntryPreview(entryId: string | null, enabled = true) {
  return useQuery<EntryPreview>({
    queryKey: ["entry-preview", entryId],
    queryFn: () => api.get<EntryPreview>(`/entries/${entryId}/preview`),
    enabled: !!entryId && enabled,
    staleTime: Infinity,
  });
}
