import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { SearchResult, Source, FsPerson, SearchAsOverride } from "../types";

type SearchMode = "fuzzy" | "glob" | "regex";
type SortField = "relevance" | "name" | "size" | "mtime";
type SortOrder = "asc" | "desc";

const SORT_OPTIONS: { value: SortField; label: string }[] = [
  { value: "relevance", label: "Relevance" },
  { value: "name", label: "Name" },
  { value: "size", label: "Size" },
  { value: "mtime", label: "Modified" },
];

function isSortField(s: string | null): s is SortField {
  return s === "relevance" || s === "name" || s === "size" || s === "mtime";
}
function isSortOrder(s: string | null): s is SortOrder {
  return s === "asc" || s === "desc";
}

const MODE_HINTS: Record<SearchMode, string> = {
  fuzzy:
    "Typo-tolerant prefix matching. Use Glob or Regex for exact patterns.",
  glob:
    "* matches any name, ** matches across paths, ? matches one char. Example: **/invoices/*.csv",
  regex:
    "Postgres POSIX regex on the path. Example: ^/data/[0-9]{4}-Q[1-4]\\.pdf$",
};

const MODE_OPTIONS: { value: SearchMode; label: string }[] = [
  { value: "fuzzy", label: "Fuzzy" },
  { value: "glob", label: "Glob" },
  { value: "regex", label: "Regex" },
];

function isSearchMode(s: string | null): s is SearchMode {
  return s === "fuzzy" || s === "glob" || s === "regex";
}
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
import { DomainMetadataFacets } from "../components/search/DomainMetadataFacets";
import { ResultFacets } from "../components/search/ResultFacets";
import { BulkTagDialog } from "../components/tags/BulkTagDialog";
import { useAuth } from "../hooks/useAuth";
import { useEntryDetail } from "../hooks/useEntryDetail";
import { useFilterUrlState } from "../hooks/useFilterUrlState";
import { serialize as serializeFilters } from "../lib/filterGrammar";

interface SearchResponse {
  results: SearchResult[];
  total: number;
  query: string;
  // v0.6.0 — populated when the Meili path serves the request and the
  // result set carries any well-known domain_metadata key. Null on the
  // SQL fallback path; the facet panel just elides itself.
  facet_distribution?: Record<string, Record<string, number>> | null;
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
  // v0.5.11 — URL is the source of truth for `q` and `mode` so the
  // command palette's "Show all results in Search →" link, browser
  // refresh, and copy-paste links all populate the input. The
  // existing `?filters=` chips already follow this pattern via
  // useFilterUrlState.
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState(() => searchParams.get("q") ?? "");
  const [mode, setMode] = useState<SearchMode>(() => {
    const raw = searchParams.get("mode");
    return isSearchMode(raw) ? raw : "fuzzy";
  });
  const [sort, setSort] = useState<SortField>(() => {
    const raw = searchParams.get("sort");
    return isSortField(raw) ? raw : "relevance";
  });
  const [order, setOrder] = useState<SortOrder>(() => {
    const raw = searchParams.get("order");
    return isSortOrder(raw) ? raw : "desc";
  });
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

  // Stable identity so memoised SearchResultRow doesn't re-render the
  // whole list every time selectedIds changes.
  const toggleSelected = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // Mirror q + mode back to the URL, debounced so the address bar
  // doesn't churn on every keystroke. `replace: true` keeps the back
  // button useful (one history entry per real navigation, not per
  // keystroke).
  useEffect(() => {
    const id = setTimeout(() => {
      const next = new URLSearchParams(searchParams);
      const trimmed = query.trim();
      if (trimmed) next.set("q", trimmed); else next.delete("q");
      if (mode !== "fuzzy") next.set("mode", mode); else next.delete("mode");
      if (sort !== "relevance") next.set("sort", sort); else next.delete("sort");
      // Order only matters when there's a sort field — clean default to
      // keep the URL bar tidy. desc is the right intuition for size and
      // mtime; the asc toggle is the user's explicit ask.
      if (sort !== "relevance" && order !== "desc") next.set("order", order);
      else next.delete("order");
      setSearchParams(next, { replace: true });
    }, 250);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, mode, sort, order]);

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
    queryKey: ["search", query, mode, sourceId, extension, minSize, maxSize, effectivePermissionFilter, searchAs, filtersEncoded, sort, order],
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams();
      if (query.trim()) params.set("q", query.trim());
      if (mode !== "fuzzy") params.set("mode", mode);
      if (sourceId) params.set("source_id", sourceId);
      if (extension) params.set("extension", extension);
      if (minSize) params.set("min_size", minSize);
      if (maxSize) params.set("max_size", maxSize);
      params.set("permission_filter", effectivePermissionFilter);
      if (searchAs) params.set("search_as", JSON.stringify(searchAs));
      if (filtersEncoded) params.set("filters", filtersEncoded);
      if (sort !== "relevance") params.set("sort", sort);
      if (sort !== "relevance" && order !== "desc") params.set("order", order);
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
        <div className="flex items-center gap-2 mb-2" role="group" aria-label="Search mode">
          {MODE_OPTIONS.map((opt) => (
            <Button
              key={opt.value}
              type="button"
              size="sm"
              variant={mode === opt.value ? "primary" : "secondary"}
              aria-pressed={mode === opt.value}
              onClick={() => setMode(opt.value)}
            >
              {opt.label}
            </Button>
          ))}
          <span className="text-xs text-fg-muted ml-2">{MODE_HINTS[mode]}</span>
        </div>
        <Input
          leftIcon={<SearchIcon />}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={
            mode === "glob"
              ? "Glob pattern (e.g. **/invoices/*.csv)"
              : mode === "regex"
                ? "Regex pattern (POSIX)"
                : "Search files…"
          }
          className="h-11 text-[15px]"
          autoFocus
        />
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3 mt-3">
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
        <div className="flex items-center gap-2 mt-3">
          <span className="text-xs text-fg-muted">Sort by</span>
          <Select
            value={sort}
            onChange={(e) => setSort(e.target.value as SortField)}
            options={SORT_OPTIONS}
            className="w-auto"
          />
          {sort !== "relevance" && (
            <button
              type="button"
              onClick={() => setOrder((o) => (o === "asc" ? "desc" : "asc"))}
              className="text-xs px-2 py-1 rounded border border-line bg-surface hover:bg-surface-muted text-fg"
              aria-pressed={order === "desc"}
              title={order === "desc" ? "Descending — click for ascending" : "Ascending — click for descending"}
            >
              {order === "desc" ? "↓ desc" : "↑ asc"}
            </button>
          )}
        </div>
      </Card>

      <FilterChips className="mb-3" />

      <ResultFacets
        className="mb-3"
        facetDistribution={searchQuery.data?.pages[0]?.facet_distribution}
        hideSource={Boolean(sourceId)}
      />

      <DomainMetadataFacets
        className="mb-3"
        facetDistribution={searchQuery.data?.pages[0]?.facet_distribution}
      />

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
          <div className="rounded-md border border-rose-200 dark:border-rose-700/40 bg-rose-50 dark:bg-rose-950/30 px-3 py-2">
            <p className="text-sm text-rose-800 dark:text-rose-200">
              {searchQuery.error instanceof Error
                ? searchQuery.error.message
                : "Search failed."}
            </p>
            {mode === "regex" && (
              <p className="text-xs text-fg-muted mt-1">
                If this is a regex syntax error, the API returns the parser position. Try escaping reserved characters with <code>\</code>.
              </p>
            )}
          </div>
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
                <SearchResultRow
                  key={file.id}
                  file={file}
                  isAdmin={isAdmin}
                  selected={selectedIds.has(file.id)}
                  sourceLabel={
                    file.source_id == null
                      ? null
                      : sourceMap.get(file.source_id) ?? file.source_id.slice(0, 8)
                  }
                  onOpen={openEntry}
                  onToggleSelected={toggleSelected}
                />
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

// Memoised so a parent re-render (filter chip change, infinite-scroll
// fetch, selection toggle) doesn't re-reconcile every visible row.
// Props are sliced to plain values + stable callbacks; the parent
// computes `selected` and `sourceLabel` so this row never reads
// shared state itself.
interface SearchResultRowProps {
  file: SearchResult;
  isAdmin: boolean;
  selected: boolean;
  sourceLabel: string | null;
  onOpen: (id: string) => void;
  onToggleSelected: (id: string) => void;
}

const SearchResultRow = memo(function SearchResultRow({
  file,
  isAdmin,
  selected,
  sourceLabel,
  onOpen,
  onToggleSelected,
}: SearchResultRowProps) {
  return (
    <li
      onClick={() => onOpen(file.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(file.id);
        }
      }}
      tabIndex={0}
      className="px-4 py-2.5 hover:bg-surface-muted/60 transition-colors cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-500"
    >
      <div className="flex items-baseline justify-between gap-4">
        {isAdmin && (
          <input
            type="checkbox"
            checked={selected}
            onChange={() => onToggleSelected(file.id)}
            onClick={(e) => e.stopPropagation()}
            className="mt-1 flex-shrink-0"
            aria-label={`Select ${file.filename}`}
          />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-medium text-fg truncate">{file.filename}</span>
            {file.extension && (
              <FilterableCell
                predicate={{ kind: "extension", value: file.extension }}
              >
                <Badge variant="neutral">.{file.extension}</Badge>
              </FilterableCell>
            )}
            {file.dup_count != null && file.dup_count > 0 && file.content_hash && (
              <Link
                to={`/duplicates?hash=${encodeURIComponent(file.content_hash)}`}
                onClick={(e) => e.stopPropagation()}
                title={`Open the duplicate group in /duplicates (${file.dup_count} other ${file.dup_count === 1 ? "copy" : "copies"})`}
                className="flex-shrink-0"
              >
                <Badge variant="info">+{file.dup_count} {file.dup_count === 1 ? "copy" : "copies"}</Badge>
              </Link>
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
            {sourceLabel === null ? (
              <span
                className="italic text-fg-subtle"
                title="The source this file was indexed from has been deleted. The entry survives but content fetch is no longer possible."
              >
                (deleted source)
              </span>
            ) : (
              <FilterableCell
                predicate={{ kind: "source", value: file.source_id ?? "" }}
              >
                {sourceLabel}
              </FilterableCell>
            )}
          </div>
        </div>
      </div>
    </li>
  );
});

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
        <>
          <span className="text-fg-muted">Select results to bulk-tag, or</span>
          <Button
            size="sm"
            variant="ghost"
            onClick={toggleAllVisible}
            disabled={results.length === 0}
          >
            Select all {results.length.toLocaleString()} visible
          </Button>
        </>
      ) : (
        <>
          <span className="font-medium text-fg">
            {selectedIds.size.toLocaleString()} selected
          </span>
          <Button
            size="sm"
            variant="ghost"
            onClick={toggleAllVisible}
            reserveLabel="Select all 9,999 visible"
            className="tabular-nums"
          >
            {allVisibleSelected ? "Deselect all visible" : `Select all ${results.length.toLocaleString()} visible`}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setSelectedIds(new Set())}
          >
            Clear
          </Button>
          <div className="flex-1" />
          <Button
            size="sm"
            onClick={onTagSelected}
            reserveLabel="Tag selected (9,999)"
            className="tabular-nums"
          >
            Tag selected ({selectedIds.size})
          </Button>
        </>
      )}
    </div>
  );
}
