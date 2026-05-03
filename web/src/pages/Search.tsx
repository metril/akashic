import { useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
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
import { BulkTagDialog } from "../components/tags/BulkTagDialog";
import { useAuth } from "../hooks/useAuth";
import { useEntryDetail } from "../hooks/useEntryDetail";
import { useFilterUrlState } from "../hooks/useFilterUrlState";
import { serialize as serializeFilters } from "../lib/filterGrammar";

interface SearchResponse {
  results: SearchResult[];
  total: number;
  query: string;
}

// v0.4.14 — Search is now infinite-scroll. Page size matches the
// API's new default (api/akashic/routers/search.py); the cap mirrors
// MAX_TOTAL_HITS in api/akashic/services/search.py so the footer can
// surface "showing top N — refine your query" when we hit it.
const PAGE_SIZE = 100;
const MAX_TOTAL_HITS = 100_000;

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
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [tagDialogOpen, setTagDialogOpen] = useState(false);

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

  const searchQuery = useInfiniteQuery<SearchResponse>({
    queryKey: ["search", query, sourceId, extension, minSize, maxSize, effectivePermissionFilter, searchAs, filtersEncoded],
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams();
      if (query.trim()) params.set("q", query.trim());
      if (sourceId) params.set("source_id", sourceId);
      if (extension) params.set("extension", extension);
      if (minSize) params.set("min_size", minSize);
      if (maxSize) params.set("max_size", maxSize);
      params.set("permission_filter", effectivePermissionFilter);
      if (searchAs) params.set("search_as", JSON.stringify(searchAs));
      if (filtersEncoded) params.set("filters", filtersEncoded);
      params.set("offset", String(pageParam ?? 0));
      params.set("limit", String(PAGE_SIZE));
      return api.get<SearchResponse>(`/search?${params.toString()}`);
    },
    initialPageParam: 0,
    getNextPageParam: (last, allPages) => {
      const fetched = allPages.reduce((s, p) => s + p.results.length, 0);
      if (fetched >= last.total) return undefined;        // no more matches
      if (fetched >= MAX_TOTAL_HITS) return undefined;    // hit the Meili cap
      return fetched;
    },
    enabled: hasFilter,
  });

  const results = useMemo(
    () => searchQuery.data?.pages.flatMap((p) => p.results) ?? [],
    [searchQuery.data],
  );
  const totalFromApi = searchQuery.data?.pages[0]?.total ?? 0;
  const reachedCap = totalFromApi >= MAX_TOTAL_HITS;
  const totalDisplay = reachedCap
    ? `${MAX_TOTAL_HITS.toLocaleString()}+`
    : totalFromApi.toLocaleString();

  // Auto-fetch the next page when the sentinel scrolls into view.
  // Same pattern Browse uses; Search renders a flat <ul> so we just
  // observe a div at the bottom of the list.
  const sentinelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    if (!searchQuery.hasNextPage || searchQuery.isFetchingNextPage) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          void searchQuery.fetchNextPage();
        }
      },
      { rootMargin: "200px" },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [searchQuery.hasNextPage, searchQuery.isFetchingNextPage, searchQuery]);

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
              {totalDisplay} result{totalFromApi !== 1 && "s"}
              {searchAs && (
                <span className="ml-2 text-amber-700">
                  (filtered as {searchAs.type}:{searchAs.identifier})
                </span>
              )}
            </div>
          </div>

          {isAdmin && (
            <SelectionBar
              results={results}
              selectedIds={selectedIds}
              setSelectedIds={setSelectedIds}
              onTagSelected={() => setTagDialogOpen(true)}
            />
          )}
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
                        {file.source_id == null ? (
                          <span
                            className="italic text-fg-subtle"
                            title="The source this file was indexed from has been deleted. The entry survives but content fetch is no longer possible."
                          >
                            (deleted source)
                          </span>
                        ) : (
                          <FilterableCell
                            predicate={{ kind: "source", value: file.source_id }}
                          >
                            {sourceMap.get(file.source_id) ??
                              file.source_id.slice(0, 8)}
                          </FilterableCell>
                        )}
                      </div>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
            <div
              ref={sentinelRef}
              className="px-4 py-3 text-xs text-fg-muted text-center"
            >
              {searchQuery.isFetchingNextPage
                ? "Loading more…"
                : reachedCap && results.length >= MAX_TOTAL_HITS
                  ? `Showing top ${MAX_TOTAL_HITS.toLocaleString()} of ${totalDisplay} matches — refine your query for more`
                  : !searchQuery.hasNextPage && results.length > 0
                    ? `End of results (${results.length.toLocaleString()})`
                    : ""}
            </div>
          </Card>
        </>
      )}

      <BulkTagDialog
        open={tagDialogOpen}
        onClose={() => setTagDialogOpen(false)}
        entryIds={Array.from(selectedIds)}
        onApplied={() => setSelectedIds(new Set())}
      />
    </Page>
  );
}

interface SelectionBarProps {
  results: SearchResult[];
  selectedIds: Set<string>;
  setSelectedIds: (next: Set<string>) => void;
  onTagSelected: () => void;
}

/**
 * Sub-header above the result list. Renders a master checkbox
 * (with indeterminate state when some-but-not-all visible
 * results are selected) plus quick "Select all visible" /
 * "Clear" actions. The "Tag selected" button only appears once
 * something's actually selected.
 *
 * "Visible" = the results currently rendered (which under
 * infinite-scroll is everything fetched so far).
 */
function SelectionBar({
  results,
  selectedIds,
  setSelectedIds,
  onTagSelected,
}: SelectionBarProps) {
  const masterRef = useRef<HTMLInputElement>(null);
  const allVisibleSelected =
    results.length > 0 && results.every((r) => selectedIds.has(r.id));
  const someVisibleSelected =
    !allVisibleSelected && results.some((r) => selectedIds.has(r.id));

  useEffect(() => {
    if (masterRef.current) {
      masterRef.current.indeterminate = someVisibleSelected;
    }
  }, [someVisibleSelected]);

  function toggleAllVisible() {
    const next = new Set(selectedIds);
    if (allVisibleSelected) {
      for (const r of results) next.delete(r.id);
    } else {
      for (const r of results) next.add(r.id);
    }
    setSelectedIds(next);
  }

  return (
    <div className="flex items-center gap-3 mb-3 px-3 py-1.5 rounded-md border border-line-subtle bg-surface-muted/40 text-xs">
      <input
        ref={masterRef}
        type="checkbox"
        checked={allVisibleSelected}
        onChange={toggleAllVisible}
        aria-label="Select all visible results"
        className="cursor-pointer"
      />
      {selectedIds.size === 0 ? (
        <span className="text-fg-muted">
          Select results to bulk-tag, or
          <button
            type="button"
            onClick={toggleAllVisible}
            className="ml-1 text-accent-700 hover:underline"
            disabled={results.length === 0}
          >
            select all {results.length.toLocaleString()} visible
          </button>
          .
        </span>
      ) : (
        <>
          <span className="font-medium text-fg">
            {selectedIds.size.toLocaleString()} selected
          </span>
          <button
            type="button"
            onClick={toggleAllVisible}
            className="text-accent-700 hover:underline"
          >
            {allVisibleSelected ? "Deselect all visible" : `Select all ${results.length.toLocaleString()} visible`}
          </button>
          <button
            type="button"
            onClick={() => setSelectedIds(new Set())}
            className="text-fg-muted hover:text-fg hover:underline"
          >
            Clear
          </button>
          <div className="flex-1" />
          <Button size="sm" onClick={onTagSelected}>
            Tag selected ({selectedIds.size})
          </Button>
        </>
      )}
    </div>
  );
}
