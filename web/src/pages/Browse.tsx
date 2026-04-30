import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { BrowseChild, BrowseResponse, Source } from "../types";
import {
  Card,
  Select,
  Breadcrumb,
  EmptyState,
  Spinner,
  Drawer,
  Badge,
  Input,
} from "../components/ui";
import type { BreadcrumbSegment } from "../components/ui";
import { formatBytes, formatDate } from "../lib/format";
import { formatMode, iconPathForKind } from "../lib/perms";
import { EntryDetail } from "../components/EntryDetail";
import { downloadEntryContent } from "../lib/downloadEntry";
import { Icon } from "../components/ui";

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
  if (!active) return <span className="text-gray-300 ml-1">↕</span>;
  return (
    <span className="text-gray-700 ml-1" aria-hidden>
      {dir === "asc" ? "▲" : "▼"}
    </span>
  );
}

export default function Browse() {
  const [params, setParams] = useSearchParams();

  const sourcesQuery = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: () => api.get<Source[]>("/sources"),
  });
  const sources = sourcesQuery.data ?? [];

  const sourceId = params.get("source") ?? "";
  const path = params.get("path") ?? "/";
  const [selectedEntryId, setSelectedEntryId] = useState<string | null>(null);

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

  const browseQuery = useQuery<BrowseResponse>({
    queryKey: ["browse", sourceId, path],
    queryFn: () =>
      api.get<BrowseResponse>(
        `/browse?source_id=${sourceId}&path=${encodeURIComponent(path)}`,
      ),
    enabled: !!sourceId,
  });

  const navigate = (newPath: string) => {
    setParams({ source: sourceId, path: newPath });
    setSelectedEntryId(null);
  };

  const handleSourceChange = (id: string) => {
    setParams({ source: id, path: "/" });
    setSelectedEntryId(null);
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
      setSelectedEntryId(child.id);
    }
  };

  const goUp = () => {
    if (path === "/") return;
    const idx = path.lastIndexOf("/");
    navigate(idx <= 0 ? "/" : path.slice(0, idx));
  };

  const selectedEntry = browseQuery.data?.entries.find(
    (e) => e.id === selectedEntryId,
  );

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
    <div className="px-8 py-7 max-w-6xl">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-gray-900 tracking-tight">
          Browse
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Walk the indexed file tree and inspect per-entry permissions.
        </p>
      </div>

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
            <button
              type="button"
              onClick={goUp}
              disabled={path === "/"}
              className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md border border-gray-300 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
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
              <span className="text-xs text-gray-500 tabular-nums">
                {visibleEntries.length.toLocaleString()} of{" "}
                {totalEntries.toLocaleString()} match
              </span>
            )}
          </div>
        )}
      </Card>

      <Card padding="none">
        {browseQuery.isLoading || sourcesQuery.isLoading ? (
          <div className="flex justify-center items-center h-40 text-gray-400">
            <Spinner />
          </div>
        ) : sources.length === 0 ? (
          <EmptyState
            title="No sources yet"
            description="Add a source on the Sources page to start browsing."
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
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 text-[11px] uppercase tracking-wide text-gray-500">
                  <th className="text-left font-semibold py-2.5 px-4">
                    <button
                      type="button"
                      onClick={() => toggleSort("name")}
                      className="inline-flex items-center hover:text-gray-700"
                    >
                      Name
                      <SortIndicator active={sort.field === "name"} dir={sort.dir} />
                    </button>
                  </th>
                  <th className="text-left font-semibold py-2.5 px-4 hidden md:table-cell">
                    <button
                      type="button"
                      onClick={() => toggleSort("size")}
                      className="inline-flex items-center hover:text-gray-700"
                    >
                      Size
                      <SortIndicator active={sort.field === "size"} dir={sort.dir} />
                    </button>
                  </th>
                  <th className="text-left font-semibold py-2.5 px-4 hidden lg:table-cell">
                    Owner
                  </th>
                  <th className="text-left font-semibold py-2.5 px-4 hidden lg:table-cell">
                    Mode
                  </th>
                  <th className="text-left font-semibold py-2.5 px-4 hidden md:table-cell">
                    <button
                      type="button"
                      onClick={() => toggleSort("modified")}
                      className="inline-flex items-center hover:text-gray-700"
                    >
                      Modified
                      <SortIndicator active={sort.field === "modified"} dir={sort.dir} />
                    </button>
                  </th>
                  <th className="text-right font-semibold py-2.5 px-4 w-12">
                    {/* actions column */}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {visibleEntries.length === 0 && filter && (
                  <tr>
                    <td colSpan={6} className="py-8 text-center text-sm text-gray-500">
                      No entries match{" "}
                      <code className="font-mono text-xs">{filter}</code> in this folder.
                    </td>
                  </tr>
                )}
                {visibleEntries.map((child) => (
                  <tr
                    key={child.id}
                    onClick={() => handleRowClick(child)}
                    className={`hover:bg-accent-50/40 cursor-pointer transition-colors ${
                      selectedEntryId === child.id ? "bg-accent-50/60" : ""
                    }`}
                  >
                    <td className="py-2.5 px-4">
                      <div className="flex items-center gap-2.5 min-w-0">
                        <Icon
                          path={iconPathForKind(child.kind, child.extension)}
                          className={`size-4 ${
                            child.kind === "directory"
                              ? "text-accent-600"
                              : "text-gray-400"
                          }`}
                        />
                        <span className="truncate text-gray-900 font-medium">
                          {child.name}
                        </span>
                        {child.kind === "directory" &&
                          child.child_count != null && (
                            <Badge variant="neutral">
                              {child.child_count}
                            </Badge>
                          )}
                      </div>
                    </td>
                    <td className="py-2.5 px-4 text-gray-600 tabular-nums hidden md:table-cell">
                      {child.kind === "directory"
                        ? "—"
                        : formatBytes(child.size_bytes)}
                    </td>
                    <td className="py-2.5 px-4 text-gray-600 hidden lg:table-cell">
                      {child.owner_name ?? "—"}
                      {child.group_name && (
                        <span className="text-gray-400">
                          :{child.group_name}
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 px-4 hidden lg:table-cell">
                      <code className="font-mono text-xs text-gray-600">
                        {formatMode(child.mode)}
                      </code>
                    </td>
                    <td className="py-2.5 px-4 text-gray-500 hidden md:table-cell">
                      {formatDate(child.fs_modified_at)}
                    </td>
                    <td className="py-2.5 px-4 text-right">
                      {child.kind === "file" && (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            downloadEntryContent(child.id, child.name).catch(
                              (err) =>
                                console.error("Download failed:", err),
                            );
                          }}
                          aria-label={`Download ${child.name}`}
                          title="Download"
                          className="p-1.5 rounded text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
                        >
                          <Icon name="download" className="size-4" />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Drawer
        open={!!selectedEntryId}
        onClose={() => setSelectedEntryId(null)}
        title={selectedEntry?.name}
        description={selectedEntry?.path}
        width="lg"
      >
        <EntryDetail entryId={selectedEntryId} />
      </Drawer>
    </div>
  );
}
