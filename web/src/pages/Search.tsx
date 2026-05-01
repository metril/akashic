import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { SearchResult, Source, FsPerson, SearchAsOverride } from "../types";
import {
  Button,
  Card,
  Input,
  Select,
  Badge,
  Spinner,
  EmptyState,
  Page,
  FilterableCell,
  FilterChips,
} from "../components/ui";
import { formatBytes } from "../lib/format";
import { SearchAsForm } from "../components/search/SearchAsForm";
import { useAuth } from "../hooks/useAuth";
import { useEntryDetail } from "../hooks/useEntryDetail";
import { useFilterUrlState } from "../hooks/useFilterUrlState";
import { serialize as serializeFilters } from "../lib/filterGrammar";

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
  const [searchAs, setSearchAs] = useState<SearchAsOverride | null>(null);
  const [showSearchAs, setShowSearchAs] = useState(false);
  const { openEntry } = useEntryDetail();
  const { filters } = useFilterUrlState();
  const { isAdmin } = useAuth();
  const queryClient = useQueryClient();
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [tagDialogOpen, setTagDialogOpen] = useState(false);
  const [tagDraft, setTagDraft] = useState("");

  const bulkApplyMut = useMutation({
    mutationFn: (tags: string[]) =>
      api.post<void>("/tags/bulk-apply", {
        entry_ids: Array.from(selectedIds),
        tags,
      }),
    onSuccess: () => {
      // Each tagged entry's drawer query needs to refresh.
      for (const id of selectedIds) {
        queryClient.invalidateQueries({ queryKey: ["entry", id] });
      }
      setSelectedIds(new Set());
      setTagDialogOpen(false);
      setTagDraft("");
    },
  });

  function toggleSelected(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

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

  // Phase-6 chip-driven filters from `?filters=` count toward "has any
  // filter" — a query with just chips and no text/source still queries.
  const filtersEncoded = filters.length > 0 ? serializeFilters(filters) : "";

  const hasFilter = Boolean(
    query.trim() || sourceId || extension || minSize || maxSize || filtersEncoded,
  );

  const searchQuery = useQuery<SearchResponse>({
    queryKey: ["search", query, sourceId, extension, minSize, maxSize, effectivePermissionFilter, searchAs, filtersEncoded],
    queryFn: () => {
      const params = new URLSearchParams();
      if (query.trim()) params.set("q", query.trim());
      if (sourceId) params.set("source_id", sourceId);
      if (extension) params.set("extension", extension);
      if (minSize) params.set("min_size", minSize);
      if (maxSize) params.set("max_size", maxSize);
      params.set("permission_filter", effectivePermissionFilter);
      if (searchAs) params.set("search_as", JSON.stringify(searchAs));
      if (filtersEncoded) params.set("filters", filtersEncoded);
      return api.get<SearchResponse>(`/search?${params.toString()}`);
    },
    enabled: hasFilter,
  });

  const results = searchQuery.data?.results ?? [];

  return (
    <Page
      title="Search"
      description="Find files by name, path, or filter alone."
      width="default"
    >
      <div className="flex items-center justify-end mb-2">
        <button
          type="button"
          onClick={() => setShowSearchAs((v) => !v)}
          className="text-xs text-fg-muted hover:text-fg"
        >
          {showSearchAs ? "▾" : "▸"} Search as…
        </button>
      </div>
      {showSearchAs && (
        <SearchAsForm value={searchAs} onChange={setSearchAs} />
      )}

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

      <FilterChips className="mb-3" />

      {!hasFilter ? (
        <Card padding="lg">
          <EmptyState
            title="Start searching"
            description="Type a query or pick a filter to see results."
          />
        </Card>
      ) : searchQuery.isLoading ? (
        <div className="flex items-center justify-center py-12 text-fg-subtle">
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
          <div className="flex items-center justify-between mb-3">
            <div className="text-xs text-fg-muted">
              {searchQuery.data?.total.toLocaleString()} result
              {searchQuery.data?.total !== 1 && "s"}
              {searchAs && (
                <span className="ml-2 text-amber-700">
                  (filtered as {searchAs.type}:{searchAs.identifier})
                </span>
              )}
            </div>
            {isAdmin && selectedIds.size > 0 && (
              <Button
                size="sm"
                onClick={() => setTagDialogOpen(true)}
              >
                Tag selected ({selectedIds.size})
              </Button>
            )}
          </div>
          <Card padding="none">
            <ul className="divide-y divide-line-subtle">
              {results.map((file) => (
                <li
                  key={file.id}
                  onClick={() => openEntry(file.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      openEntry(file.id);
                    }
                  }}
                  tabIndex={0}
                  className="px-4 py-2.5 hover:bg-surface-muted/60 transition-colors cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-500"
                >
                  <div className="flex items-baseline justify-between gap-4">
                    {isAdmin && (
                      <input
                        type="checkbox"
                        checked={selectedIds.has(file.id)}
                        onChange={() => toggleSelected(file.id)}
                        onClick={(e) => e.stopPropagation()}
                        className="mt-1 flex-shrink-0"
                        aria-label={`Select ${file.filename}`}
                      />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-fg truncate">
                          {file.filename}
                        </span>
                        {file.extension && (
                          <FilterableCell
                            predicate={{ kind: "extension", value: file.extension }}
                          >
                            <Badge variant="neutral">.{file.extension}</Badge>
                          </FilterableCell>
                        )}
                      </div>
                      <div className="text-xs text-fg-muted font-mono truncate mt-0.5">
                        {file.path}
                      </div>
                    </div>
                    <div className="flex flex-col items-end flex-shrink-0 text-right">
                      <div className="text-sm font-medium text-fg tabular-nums">
                        {formatBytes(file.size_bytes)}
                      </div>
                      <div className="text-xs text-fg-muted mt-0.5">
                        <FilterableCell
                          predicate={{ kind: "source", value: file.source_id }}
                        >
                          {sourceMap.get(file.source_id) ??
                            file.source_id.slice(0, 8)}
                        </FilterableCell>
                      </div>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        </>
      )}

      {tagDialogOpen && (
        <div
          className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
          onClick={() => setTagDialogOpen(false)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="bg-surface rounded-lg shadow-xl border border-line w-full max-w-md p-5"
          >
            <h2 className="text-base font-semibold text-fg mb-2">
              Tag {selectedIds.size} selected
            </h2>
            <p className="text-xs text-fg-muted mb-3">
              Comma-separated. Tagging a directory inherits to every
              descendant.
            </p>
            <Input
              autoFocus
              value={tagDraft}
              onChange={(e) => setTagDraft(e.target.value)}
              placeholder="archive, fy26"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  const items = tagDraft
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean);
                  if (items.length > 0) bulkApplyMut.mutate(items);
                }
              }}
            />
            <div className="flex justify-end gap-2 mt-4">
              <Button
                variant="ghost"
                onClick={() => {
                  setTagDialogOpen(false);
                  setTagDraft("");
                }}
              >
                Cancel
              </Button>
              <Button
                onClick={() => {
                  const items = tagDraft
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean);
                  if (items.length > 0) bulkApplyMut.mutate(items);
                }}
                loading={bulkApplyMut.isPending}
                disabled={!tagDraft.trim()}
              >
                Apply
              </Button>
            </div>
          </div>
        </div>
      )}
    </Page>
  );
}
