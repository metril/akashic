import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { DuplicateGroup, FileEntry } from "../types";
import {
  Card,
  CardHeader,
  StatCard,
  Badge,
  Spinner,
  EmptyState,
} from "../components/ui";
import { formatBytes, formatNumber } from "../lib/format";

const ChevronIcon = ({ open }: { open: boolean }) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 20 20"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={`h-4 w-4 transition-transform ${open ? "rotate-90" : ""}`}
  >
    <path d="M7 5l6 5-6 5" />
  </svg>
);

function DuplicateGroupRow({ group }: { group: DuplicateGroup }) {
  const [expanded, setExpanded] = useState(false);

  const filesQuery = useQuery<FileEntry[]>({
    queryKey: ["duplicates", group.content_hash, "files"],
    queryFn: () =>
      api.get<FileEntry[]>(`/duplicates/${group.content_hash}/files`),
    enabled: expanded,
  });

  return (
    <Card padding="none">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-4 px-5 py-4 text-left hover:bg-gray-50/60 transition-colors rounded-xl"
      >
        <ChevronIcon open={expanded} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <code className="text-xs font-mono text-accent-700 bg-accent-50 px-1.5 py-0.5 rounded">
              {group.content_hash.substring(0, 16)}…
            </code>
            <Badge variant="neutral">{group.count} copies</Badge>
          </div>
          <div className="text-xs text-gray-500">
            File size {formatBytes(group.file_size)} · {formatBytes(group.total_size)} stored total
          </div>
        </div>
        <div className="text-right flex-shrink-0">
          <div className="text-[11px] uppercase tracking-wide text-gray-400">
            Wasted
          </div>
          <div className="text-base font-semibold text-rose-600 tabular-nums">
            {formatBytes(group.wasted_bytes)}
          </div>
        </div>
      </button>
      {expanded && (
        <div className="border-t border-gray-100 px-5 py-3">
          {filesQuery.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-gray-400 py-2">
              <Spinner size="sm" /> Loading files…
            </div>
          ) : (filesQuery.data ?? []).length === 0 ? (
            <p className="text-sm text-gray-400 py-2">No files.</p>
          ) : (
            <ul className="divide-y divide-gray-100">
              {(filesQuery.data ?? []).map((file) => (
                <li key={file.id} className="py-2">
                  <div className="text-sm font-medium text-gray-700">
                    {file.filename}
                  </div>
                  <div className="text-xs text-gray-400 font-mono break-all">
                    {file.path}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  );
}

export default function Duplicates() {
  const {
    data: groups,
    isLoading,
    error,
  } = useQuery<DuplicateGroup[]>({
    queryKey: ["duplicates"],
    queryFn: () => api.get<DuplicateGroup[]>("/duplicates"),
  });

  const sorted = [...(groups ?? [])].sort(
    (a, b) => b.wasted_bytes - a.wasted_bytes,
  );
  const totalWasted = sorted.reduce((s, g) => s + g.wasted_bytes, 0);

  return (
    <div className="px-8 py-7 max-w-5xl">
      <div className="mb-7">
        <h1 className="text-2xl font-semibold text-gray-900 tracking-tight">
          Duplicates
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Files with identical content stored in multiple locations.
        </p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
        <StatCard
          label="Duplicate groups"
          value={formatNumber(sorted.length)}
          loading={isLoading}
        />
        <StatCard
          label="Wasted storage"
          value={formatBytes(totalWasted)}
          loading={isLoading}
        />
        <StatCard
          label="Extra copies"
          value={formatNumber(
            sorted.reduce((s, g) => s + (g.count - 1), 0),
          )}
          loading={isLoading}
        />
      </div>

      <Card padding="md">
        <CardHeader
          title="Groups"
          description="Sorted by wasted space."
        />
        {isLoading ? (
          <div className="flex justify-center py-8 text-gray-400">
            <Spinner />
          </div>
        ) : error ? (
          <p className="text-sm text-rose-600">
            {error instanceof Error
              ? error.message
              : "Failed to load duplicates"}
          </p>
        ) : sorted.length === 0 ? (
          <EmptyState
            title="No duplicates found"
            description="When two files share the same content hash they'll show up here."
          />
        ) : (
          <div className="space-y-3">
            {sorted.map((g) => (
              <DuplicateGroupRow key={g.content_hash} group={g} />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
