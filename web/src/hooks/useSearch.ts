import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { SearchResult } from "../types";

interface SearchFilters {
  q: string;
  source_id?: number;
  extension?: string;
  min_size?: number;
  max_size?: number;
}

interface SearchResponse {
  results: SearchResult[];
  total: number;
  query: string;
}

export function useSearch(filters: SearchFilters) {
  const { q, source_id, extension, min_size, max_size } = filters;

  return useQuery<SearchResponse>({
    queryKey: ["search", filters],
    queryFn: () => {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (source_id !== undefined) params.set("source_id", String(source_id));
      if (extension) params.set("extension", extension);
      if (min_size !== undefined) params.set("min_size", String(min_size));
      if (max_size !== undefined) params.set("max_size", String(max_size));
      return api.get<SearchResponse>(`/search?${params.toString()}`);
    },
    enabled: q.trim().length > 0,
  });
}
