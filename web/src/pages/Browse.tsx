import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useSearchParams, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { BrowseChild, BrowseResponse, Source } from "../types";
import {
  Card,
  Select,
  Breadcrumb,
  EmptyState,
  Spinner,
  Badge,
  Input,
  Page,
  Button,
  FilterableCell,
  FilterChips,
} from "../components/ui";
import type { BreadcrumbSegment } from "../components/ui";
import { formatBytes, formatDate } from "../lib/format";
import { formatMode, iconPathForKind } from "../lib/perms";
import { downloadEntryContent } from "../lib/downloadEntry";
import { Icon } from "../components/ui";
import { useAuth } from "../hooks/useAuth";
import { useEntryDetail } from "../hooks/useEntryDetail";
import { useFilterUrlState } from "../hooks/useFilterUrlState";
import { serialize as serializeFilters } from "../lib/filterGrammar";

interface EffectiveCounts {
  visible: number;
  hidden: number;
  enforced: boolean;
}

function pathSegments(path: string): string[] {
  if (path === "/") return [];
  return path.split("/").filter(Boolean);
}

// Sort fields exposed on column headers. Modified-time is the most
// frequently-asked-for ordering on big media libraries; size is useful
// for hunting space hogs; name is the default since alphabetical is
// what users start with.
type SortField = "name" | "size" | "modified";
type SortDir = "asc" | "desc";

interface SortState {
  field: SortField;
  dir: SortDir;
}

const DEFAULT_SORT: SortState = { field: "name", dir: "asc" };

// Directories always come before files regardless of sort field. That
// matches every desktop file manager and means a "size DESC" sort
// doesn't bury folders under a wall of giant videos.
function compareEntries(a: BrowseChild, b: BrowseChild, sort: SortState): number {
  if (a.kind !== b.kind) {
    return a.kind === "directory" ? -1 : 1;
  }
  let cmp = 0;
  switch (sort.field) {
    case "name":
      cmp = a.name.localeCompare(b.name, undefined, { sensitivity: "base", numeric: true });
      break;
    case "size": {
      const aSize = a.kind === "directory" ? -1 : (a.size_bytes ?? 0);
      const bSize = b.kind === "directory" ? -1 : (b.size_bytes ?? 0);
      cmp = aSize - bSize;
      break;
    }
    case "modified": {
      const aTs = a.fs_modified_at ? Date.parse(a.fs_modified_at) : 0;
      const bTs = b.fs_modified_at ? Date.parse(b.fs_modified_at) : 0;
      cmp = aTs - bTs;
      break;
    }
  }
  return sort.dir === "asc" ? cmp : -cmp;
}

function SortIndicator({ active, dir }: { active: boolean; dir: SortDir }) {
  if (!active) return <span className="text-fg-subtle ml-1">↕</span>;
  return (
    <span className="text-fg ml-1" aria-hidden>
      {dir === "asc" ? "▲" : "▼"}
    </span>
  );
}

export default function Browse() {
  const [params, setParams] = useSearchParams();
  const routerNav = useNavigate();
  const { isAdmin } = useAuth();
  // Phase-5: admin opt-out for the per-user ACL trim. Off by default;
  // toggling it sticks to localStorage so an admin debugging "what does
  // user X see?" doesn't have to flip it on every navigation.
  const [showAll, setShowAll] = useState<boolean>(() => {
    try {
      return localStorage.getItem("browse-show-all") === "1";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("browse-show-all", showAll ? "1" : "0");
    } catch {
      // storage may be blocked — preference resets next session.
    }
  }, [showAll]);

  const sourcesQuery = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: () => api.get<Source[]>("/sources"),
  });
  const sources = sourcesQuery.data ?? [];

  const sourceId = params.get("source") ?? "";
  const path = params.get("path") ?? "/";
  // Phase-6 lifted-to-Layout drawer. Browse no longer owns the
  // <Drawer/> directly; it dispatches openEntry() and Layout renders.
  const { openEntry, openEntryId: selectedEntryId } = useEntryDetail();
  const { filters } = useFilterUrlState();

  // Sort + filter live in component state, not the URL. Persisting them
  // would break the "click breadcrumb to navigate up" flow (the new path
  // would either inherit the previous folder's sort, or carry stale
  // filter text into a folder where it matches nothing). Per-folder
  // ergonomic state is fine to reset on navigation.
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);
  const [filter, setFilter] = useState("");

  // Reset on folder change so a filter typed in one directory doesn't
  // silently hide everything in the next one.
  useEffect(() => {
    setFilter("");
  }, [sourceId, path]);

  function toggleSort(field: SortField) {
    setSort((prev) => {
      if (prev.field !== field) {
        // Default direction depends on which column makes sense first:
        // names start ascending (A→Z), size and modified-time start
        // descending (largest/newest first — the more useful question
        // when you're hunting).
        return {
          field,
          dir: field === "name" ? "asc" : "desc",
        };
      }
      return { field, dir: prev.dir === "asc" ? "desc" : "asc" };
    });
  }

  // When sources load, default to the first one if none selected.
  useEffect(() => {
    if (!sourceId && sources.length > 0) {
      setParams({ source: sources[0].id, path: "/" }, { replace: true });
    }
  }, [sourceId, sources, setParams]);

  const sourceOptions = useMemo(
    () =>
      sources.map((s) => ({
        value: s.id,
        label: s.name,
      })),
    [sources],
  );

  const showAllParam = isAdmin && showAll ? "&show_all=1" : "";
  const filtersParam = filters.length > 0 ? `&filters=${serializeFilters(filters)}` : "";

  const browseQuery = useQuery<BrowseResponse>({
    queryKey: ["browse", sourceId, path, showAllParam, filtersParam],
    queryFn: () =>
      api.get<BrowseResponse>(
        `/browse?source_id=${sourceId}&path=${encodeURIComponent(path)}${showAllParam}${filtersParam}`,
      ),
    enabled: !!sourceId,
  });

  // Counts the user can/can't see. Drives the "X items hidden" footer.
  // Cheap (two indexed COUNT(*)s) so we run it alongside the browse
  // query rather than threading through the main response.
  const countsQuery = useQuery<EffectiveCounts>({
    queryKey: ["browse-counts", sourceId, path, showAllParam],
    queryFn: () =>
      api.get<EffectiveCounts>(
        `/browse/effective-counts?source_id=${sourceId}&path=${encodeURIComponent(path)}${showAllParam}`,
      ),
    enabled: !!sourceId,
  });

  const navigate = (newPath: string) => {
    setParams({ source: sourceId, path: newPath });
    openEntry(null);
  };

  const handleSourceChange = (id: string) => {
    setParams({ source: id, path: "/" });
    openEntry(null);
  };

  const segments: BreadcrumbSegment[] = useMemo(() => {
    const sourceName =
      sources.find((s) => s.id === sourceId)?.name ?? "Source";
    const segs: BreadcrumbSegment[] = [
      { label: sourceName, onClick: () => navigate("/") },
    ];
    let acc = "";
    for (const part of pathSegments(path)) {
      acc = `${acc}/${part}`;
      const target = acc;
      segs.push({ label: part, onClick: () => navigate(target) });
    }
    return segs;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sources, sourceId, path]);

  const handleRowClick = (child: BrowseChild) => {
    if (child.kind === "directory") {
      navigate(child.path);
    } else {
      openEntry(child.id);
    }
  };

  const goUp = () => {
    if (path === "/") return;
    const idx = path.lastIndexOf("/");
    navigate(idx <= 0 ? "/" : path.slice(0, idx));
  };

  // Filtered + sorted view of the current folder. The full entry list
  // stays in browseQuery.data; we only re-render this derived array on
  // state change. case-insensitive substring match keeps it cheap and
  // matches user expectation (typing "001" finds "S01E01.mkv").
  const visibleEntries = useMemo(() => {
    const all = browseQuery.data?.entries ?? [];
    const needle = filter.trim().toLowerCase();
    const filtered = needle
      ? all.filter((e) => e.name.toLowerCase().includes(needle))
      : all;
    return [...filtered].sort((a, b) => compareEntries(a, b, sort));
  }, [browseQuery.data, filter, sort]);
  const totalEntries = browseQuery.data?.entries.length ?? 0;

  return (
    <Page
      title="Browse"
      description="Walk the indexed file tree and inspect per-entry permissions."
      width="full"
    >
      <Card padding="md" className="mb-4">
        <div className="flex flex-col md:flex-row md:items-center gap-3">
          <div className="md:w-64">
            <Select
              label="Source"
              value={sourceId}
              onChange={(e) => handleSourceChange(e.target.value)}
              options={
                sources.length === 0
                  ? [{ value: "", label: "No sources" }]
                  : sourceOptions
              }
              disabled={sources.length === 0}
            />
          </div>
          <div className="flex-1 min-w-0 md:pt-5">
            <Breadcrumb segments={segments} />
          </div>
          <div className="flex gap-2 md:pt-5">
            {isAdmin && countsQuery.data?.enforced && (
              // Admin-only opt-out for the per-user ACL trim. Visible
              // only when filtering is actually being applied — no
              // point offering the toggle when there's nothing to bypass.
              <label
                title="Show entries even if your bindings don't grant read"
                className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md border border-line text-sm text-fg-muted hover:bg-surface-muted cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={showAll}
                  onChange={(e) => setShowAll(e.target.checked)}
                  className="size-3.5"
                />
                Show all (admin)
              </label>
            )}
            <button
              type="button"
              onClick={goUp}
              disabled={path === "/"}
              className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md border border-line text-sm text-fg hover:bg-surface-muted disabled:opacity-50 disabled:cursor-not-allowed"
              title="Up one directory"
            >
              <Icon name="arrow-left" className="size-4" />
              Up
            </button>
          </div>
        </div>
        {/* Filter row. Empty string means "show everything"; matched
            entry count beneath gives instant feedback on whether the
            substring narrowed too far. */}
        {sources.length > 0 && (
          <div className="mt-3 flex items-center gap-3">
            <div className="flex-1 max-w-md">
              <Input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter this folder…"
                aria-label="Filter entries by name"
              />
            </div>
            {filter && (
              <span className="text-xs text-fg-muted tabular-nums">
                {visibleEntries.length.toLocaleString()} of{" "}
                {totalEntries.toLocaleString()} match
              </span>
            )}
          </div>
        )}
      </Card>

      <FilterChips showSwitchToSearch className="mb-3" />

      <Card padding="none">
        {browseQuery.isLoading || sourcesQuery.isLoading ? (
          <div className="flex justify-center items-center h-40 text-fg-subtle">
            <Spinner />
          </div>
        ) : sources.length === 0 ? (
          <EmptyState
            title="No sources yet"
            description="Add a source on the Sources page to start browsing."
            action={
              <Button size="sm" onClick={() => routerNav("/sources")}>
                Add a source
              </Button>
            }
          />
        ) : browseQuery.isError ? (
          <div className="p-6">
            <EmptyState
              title="Couldn't load this folder"
              description={
                browseQuery.error instanceof Error
                  ? browseQuery.error.message
                  : "Unknown error"
              }
            />
          </div>
        ) : !browseQuery.data || browseQuery.data.entries.length === 0 ? (
          <EmptyState
            title="Empty"
            description={
              path === "/"
                ? "This source has no indexed entries yet. Trigger a scan from the Sources page."
                : "No entries in this folder."
            }
            action={
              path === "/" ? (
                <Button
                  size="sm"
                  onClick={() => routerNav("/sources")}
                >
                  Open Sources
                </Button>
              ) : undefined
            }
          />
        ) : (
          <BrowseList
            entries={visibleEntries}
            filterActive={!!filter}
            filterText={filter}
            sort={sort}
            toggleSort={toggleSort}
            selectedEntryId={selectedEntryId}
            onRowClick={handleRowClick}
          />
        )}
        {/* Hidden-count footer. Only renders when the per-user ACL
            trim is on AND actually hid something — we deliberately
            stay quiet for users with no bindings (they keep seeing
            everything) and for admins with show_all toggled. */}
        {countsQuery.data?.enforced &&
          countsQuery.data.hidden > 0 && (
            <div className="px-4 py-2.5 border-t border-line-subtle bg-surface-muted/40 text-xs text-fg-muted flex items-center gap-2">
              <Icon name="shield" className="size-3.5 text-fg-subtle" />
              <span>
                {countsQuery.data.hidden.toLocaleString()} of{" "}
                {(countsQuery.data.visible + countsQuery.data.hidden).toLocaleString()}{" "}
                items hidden by your access permissions.
              </span>
              {isAdmin && !showAll && (
                <button
                  type="button"
                  onClick={() => setShowAll(true)}
                  className="ml-auto text-accent-700 hover:underline"
                >
                  Show all
                </button>
              )}
            </div>
          )}
      </Card>

    </Page>
  );
}

// Estimated row height drives the virtualizer's initial layout. Real
// rows measure in via `measureElement`; the estimate just controls the
// pre-measurement scrollbar size.
const ROW_HEIGHT = 44;

// Grid template that mirrors the legacy table's `hidden md:table-cell`
// and `hidden lg:table-cell` semantics. Defined here so header and row
// share one source of truth.
const GRID_BASE = "minmax(0,1fr) 36px";
const GRID_MD = "md:[grid-template-columns:minmax(0,1fr)_96px_140px_36px]";
const GRID_LG =
  "lg:[grid-template-columns:minmax(0,1fr)_96px_120px_88px_140px_36px]";

interface BrowseListProps {
  entries: BrowseChild[];
  filterActive: boolean;
  filterText: string;
  sort: SortState;
  toggleSort: (field: SortField) => void;
  selectedEntryId: string | null;
  onRowClick: (child: BrowseChild) => void;
}

function BrowseList({
  entries,
  filterActive,
  filterText,
  sort,
  toggleSort,
  selectedEntryId,
  onRowClick,
}: BrowseListProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const rowVirtualizer = useVirtualizer({
    count: entries.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 8,
  });

  if (entries.length === 0 && filterActive) {
    return (
      <>
        <div className={`grid ${GRID_BASE} ${GRID_MD} ${GRID_LG} px-4 py-2.5 border-b border-line text-[11px] uppercase tracking-wide text-fg-muted font-semibold`}>
          <span>Name</span>
          <span className="hidden md:block">Size</span>
          <span className="hidden lg:block">Owner</span>
          <span className="hidden lg:block">Mode</span>
          <span className="hidden md:block">Modified</span>
          <span />
        </div>
        <div className="py-12 px-6 text-center text-sm text-fg-muted">
          No entries match{" "}
          <code className="font-mono text-xs">{filterText}</code> in this folder.
        </div>
      </>
    );
  }

  return (
    <>
      {/* Sticky-ish header (sticky inside the scroll container). The
          column widths must match the per-row template exactly. */}
      <div
        className={`grid ${GRID_BASE} ${GRID_MD} ${GRID_LG} gap-x-3 px-4 py-2.5 border-b border-line text-[11px] uppercase tracking-wide text-fg-muted font-semibold`}
      >
        <button
          type="button"
          onClick={() => toggleSort("name")}
          className="text-left inline-flex items-center hover:text-fg"
        >
          Name
          <SortIndicator active={sort.field === "name"} dir={sort.dir} />
        </button>
        <button
          type="button"
          onClick={() => toggleSort("size")}
          className="hidden md:inline-flex items-center text-left hover:text-fg"
        >
          Size
          <SortIndicator active={sort.field === "size"} dir={sort.dir} />
        </button>
        <span className="hidden lg:block">Owner</span>
        <span className="hidden lg:block">Mode</span>
        <button
          type="button"
          onClick={() => toggleSort("modified")}
          className="hidden md:inline-flex items-center text-left hover:text-fg"
        >
          Modified
          <SortIndicator active={sort.field === "modified"} dir={sort.dir} />
        </button>
        <span />
      </div>

      {/* Virtualized scroll region. max-height clamps to viewport so
          very large folders get a fixed pane; below max, the container
          fits the inner positioned div's height. Do NOT add `contain:
          strict` here — it strips intrinsic sizing and the scrollbox
          collapses to 0 px (rows then never appear). */}
      <div
        ref={scrollRef}
        className="max-h-[calc(100vh-280px)] overflow-y-auto"
      >
        <div
          style={{
            height: rowVirtualizer.getTotalSize(),
            position: "relative",
          }}
        >
          {rowVirtualizer.getVirtualItems().map((vi) => {
            const child = entries[vi.index];
            const selected = selectedEntryId === child.id;
            return (
              <div
                key={child.id}
                tabIndex={0}
                onClick={() => onRowClick(child)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onRowClick(child);
                  }
                }}
                className={`grid ${GRID_BASE} ${GRID_MD} ${GRID_LG} gap-x-3 items-center px-4 border-b border-line-subtle cursor-pointer transition-colors outline-none hover:bg-accent-50/40 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-500 ${
                  selected ? "bg-accent-50/60" : ""
                }`}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  right: 0,
                  height: vi.size,
                  transform: `translateY(${vi.start}px)`,
                }}
              >
                {/* Name (always visible, truncates) */}
                <div className="flex items-center gap-2.5 min-w-0 text-sm">
                  <Icon
                    path={iconPathForKind(child.kind, child.extension)}
                    className={`size-4 flex-shrink-0 ${
                      child.kind === "directory"
                        ? "text-accent-600"
                        : "text-fg-subtle"
                    }`}
                  />
                  <span className="truncate text-fg font-medium">
                    {child.name}
                  </span>
                  {child.kind === "directory" &&
                    child.child_count != null && (
                      <Badge variant="neutral" className="flex-shrink-0">
                        {child.child_count}
                      </Badge>
                    )}
                </div>

                {/* Size (md+) */}
                <div className="hidden md:block text-sm text-fg-muted tabular-nums whitespace-nowrap">
                  {child.kind === "directory"
                    ? "—"
                    : formatBytes(child.size_bytes)}
                </div>

                {/* Owner (lg+) — click to filter to entries owned by
                    this name in the current folder; ⌘-click to take
                    the filter to Search across sources. */}
                <div className="hidden lg:block text-sm text-fg-muted whitespace-nowrap truncate">
                  {child.owner_name ? (
                    <FilterableCell
                      predicate={{ kind: "owner", value: child.owner_name }}
                    >
                      {child.owner_name}
                      {child.group_name && (
                        <span className="text-fg-subtle">:{child.group_name}</span>
                      )}
                    </FilterableCell>
                  ) : (
                    <>—</>
                  )}
                </div>

                {/* Mode (lg+) */}
                <div className="hidden lg:block whitespace-nowrap">
                  <code className="font-mono text-xs text-fg-muted">
                    {formatMode(child.mode)}
                  </code>
                </div>

                {/* Modified (md+) */}
                <div className="hidden md:block text-sm text-fg-muted whitespace-nowrap">
                  {formatDate(child.fs_modified_at)}
                </div>

                {/* Action — download for files only */}
                <div className="text-right">
                  {child.kind === "file" && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        downloadEntryContent(child.id, child.name).catch(
                          (err) => console.error("Download failed:", err),
                        );
                      }}
                      aria-label={`Download ${child.name}`}
                      title="Download"
                      className="p-1.5 rounded text-fg-subtle hover:text-fg hover:bg-surface-muted transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
                    >
                      <Icon name="download" className="size-4" />
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}
