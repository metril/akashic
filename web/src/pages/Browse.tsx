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
} from "../components/ui";
import type { BreadcrumbSegment } from "../components/ui";
import { formatBytes, formatDate } from "../lib/format";
import { formatMode, iconPathForKind } from "../lib/perms";
import { EntryDetail } from "../components/EntryDetail";
import { downloadEntryContent } from "../lib/downloadEntry";

const Icon = ({ d, className = "h-4 w-4" }: { d: string; className?: string }) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.5"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden="true"
  >
    <path d={d} />
  </svg>
);

function pathSegments(path: string): string[] {
  if (path === "/") return [];
  return path.split("/").filter(Boolean);
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
              <Icon d="M19 12H5M12 19l-7-7 7-7" />
              Up
            </button>
          </div>
        </div>
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
                  <th className="text-left font-semibold py-2.5 px-4">Name</th>
                  <th className="text-left font-semibold py-2.5 px-4 hidden md:table-cell">
                    Size
                  </th>
                  <th className="text-left font-semibold py-2.5 px-4 hidden lg:table-cell">
                    Owner
                  </th>
                  <th className="text-left font-semibold py-2.5 px-4 hidden lg:table-cell">
                    Mode
                  </th>
                  <th className="text-left font-semibold py-2.5 px-4 hidden md:table-cell">
                    Modified
                  </th>
                  <th className="text-right font-semibold py-2.5 px-4 w-12">
                    {/* actions column */}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {browseQuery.data.entries.map((child) => (
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
                          d={iconPathForKind(child.kind, child.extension)}
                          className={`h-4 w-4 flex-shrink-0 ${
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
                          <Icon d="M12 3v12m0 0l-4-4m4 4l4-4M5 21h14" />
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
