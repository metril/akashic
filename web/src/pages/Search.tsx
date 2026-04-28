import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { SearchResult, Source, FsPerson } from "../types";
import {
  Card,
  Input,
  Select,
  Badge,
  Spinner,
  EmptyState,
} from "../components/ui";
import { formatBytes } from "../lib/format";

interface SearchResponse {
  results: SearchResult[];
  total: number;
  query: string;
}

const SearchIcon = () => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className="h-4 w-4"
  >
    <circle cx="11" cy="11" r="7" />
    <path d="M21 21l-4.35-4.35" />
  </svg>
);

export default function Search() {
  const [query, setQuery] = useState("");
  const [sourceId, setSourceId] = useState<string>("");
  const [extension, setExtension] = useState("");
  const [minSize, setMinSize] = useState("");
  const [maxSize, setMaxSize] = useState("");
  const [permissionFilter, setPermissionFilter] = useState<"all" | "readable" | "writable" | null>(null);

  const sourcesQuery = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: () => api.get<Source[]>("/sources"),
  });

  const identitiesQ = useQuery<FsPerson[]>({
    queryKey: ["identities"],
    queryFn:  () => api.get<FsPerson[]>("/identities"),
  });
  const hasIdentities = (identitiesQ.data ?? []).length > 0;

  const effectivePermissionFilter: "all" | "readable" | "writable" =
    permissionFilter ?? (hasIdentities ? "readable" : "all");

  const sourceMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of sourcesQuery.data ?? []) m.set(s.id, s.name);
    return m;
  }, [sourcesQuery.data]);

  const sourceOptions = useMemo(
    () => [
      { value: "", label: "All sources" },
      ...(sourcesQuery.data ?? []).map((s) => ({
        value: s.id,
        label: s.name,
      })),
    ],
    [sourcesQuery.data],
  );

  const hasFilter = Boolean(
    query.trim() || sourceId || extension || minSize || maxSize,
  );

  const searchQuery = useQuery<SearchResponse>({
    queryKey: ["search", query, sourceId, extension, minSize, maxSize, effectivePermissionFilter],
    queryFn: () => {
      const params = new URLSearchParams();
      if (query.trim()) params.set("q", query.trim());
      if (sourceId) params.set("source_id", sourceId);
      if (extension) params.set("extension", extension);
      if (minSize) params.set("min_size", minSize);
      if (maxSize) params.set("max_size", maxSize);
      params.set("permission_filter", effectivePermissionFilter);
      return api.get<SearchResponse>(`/search?${params.toString()}`);
    },
    enabled: hasFilter,
  });

  const results = searchQuery.data?.results ?? [];

  return (
    <div className="px-8 py-7 max-w-5xl">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-gray-900 tracking-tight">
          Search
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Find files by name, path, or filter alone.
        </p>
      </div>

      <Card padding="md" className="mb-5">
        <Input
          leftIcon={<SearchIcon />}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search files…"
          className="h-11 text-[15px]"
          autoFocus
        />
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-3">
          <Select
            value={effectivePermissionFilter}
            onChange={(e) => setPermissionFilter(e.target.value as "all" | "readable" | "writable")}
            options={[
              { value: "readable", label: "Files I can read" },
              { value: "writable", label: "Files I can write" },
              { value: "all",      label: "All files I have access to" },
            ]}
          />
          <Select
            value={sourceId}
            onChange={(e) => setSourceId(e.target.value)}
            options={sourceOptions}
          />
          <Input
            value={extension}
            onChange={(e) => setExtension(e.target.value)}
            placeholder="Extension (pdf)"
          />
          <Input
            type="number"
            value={minSize}
            onChange={(e) => setMinSize(e.target.value)}
            placeholder="Min size (bytes)"
          />
          <Input
            type="number"
            value={maxSize}
            onChange={(e) => setMaxSize(e.target.value)}
            placeholder="Max size (bytes)"
          />
        </div>
      </Card>

      {!hasFilter ? (
        <Card padding="lg">
          <EmptyState
            title="Start searching"
            description="Type a query or pick a filter to see results."
          />
        </Card>
      ) : searchQuery.isLoading ? (
        <div className="flex items-center justify-center py-12 text-gray-400">
          <Spinner size="md" />
        </div>
      ) : searchQuery.isError ? (
        <Card>
          <p className="text-sm text-rose-600">
            {searchQuery.error instanceof Error
              ? searchQuery.error.message
              : "Search failed"}
          </p>
        </Card>
      ) : results.length === 0 ? (
        <Card padding="lg">
          <EmptyState
            title="No matches"
            description="Try a different query or relax the filters."
          />
        </Card>
      ) : (
        <>
          <div className="text-xs text-gray-500 mb-3">
            {searchQuery.data?.total.toLocaleString()} result
            {searchQuery.data?.total !== 1 && "s"}
          </div>
          <div className="space-y-2">
            {results.map((file) => (
              <Card
                key={file.id}
                padding="none"
                className="px-4 py-3 hover:border-accent-200 transition-colors"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="font-medium text-gray-900 truncate">
                        {file.filename}
                      </span>
                      {file.extension && (
                        <Badge variant="neutral">.{file.extension}</Badge>
                      )}
                    </div>
                    <div className="text-xs text-gray-500 font-mono truncate">
                      {file.path}
                    </div>
                  </div>
                  <div className="flex flex-col items-end flex-shrink-0">
                    <div className="text-sm font-medium text-gray-700 tabular-nums">
                      {formatBytes(file.size_bytes)}
                    </div>
                    <div className="text-xs text-gray-400 mt-0.5">
                      {sourceMap.get(file.source_id) ??
                        file.source_id.slice(0, 8)}
                    </div>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
