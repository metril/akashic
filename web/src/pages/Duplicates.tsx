import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../api/client";
import type { DuplicateGroup, FileEntry } from "../types";
import {
  Card,
  CardHeader,
  StatCard,
  Badge,
  Spinner,
  EmptyState,
  Page,
  Button,
  ConfirmDialog,
} from "../components/ui";
import { useAuth } from "../hooks/useAuth";
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

// Selection state for one duplicate group's file list. Exactly one
// "keep"; zero or more "delete". Anything not in the map is unselected
// (will not be touched).
type Selection = Map<string, "keep" | "delete">;

// Default keep candidate: oldest fs_modified_at, fallback to oldest
// first_seen_at. Surfaces "the original" so users don't accidentally
// nuke the canonical copy.
function pickDefaultKeep(files: FileEntry[]): string | null {
  if (files.length === 0) return null;
  const ts = (f: FileEntry) =>
    Date.parse(f.fs_modified_at ?? f.first_seen_at ?? "") || Number.MAX_SAFE_INTEGER;
  return files.slice().sort((a, b) => ts(a) - ts(b))[0].id;
}

interface DuplicateGroupRowProps {
  group: DuplicateGroup;
  isAdmin: boolean;
}

function DuplicateGroupRow({ group, isAdmin }: DuplicateGroupRowProps) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [selection, setSelection] = useState<Selection>(new Map());
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failedById, setFailedById] = useState<Map<string, string>>(new Map());

  const filesQuery = useQuery<FileEntry[]>({
    queryKey: ["duplicates", group.content_hash, "files"],
    queryFn: () =>
      api.get<FileEntry[]>(`/duplicates/${group.content_hash}/files`),
    enabled: expanded,
  });
  const files = filesQuery.data ?? [];

  // Initialize default keep on first successful load. Done as a derived
  // useMemo gate rather than a useEffect so toggling the panel closed
  // and re-open doesn't re-overwrite a user choice.
  const haveDefaulted = selection.size > 0;
  if (!haveDefaulted && files.length > 0) {
    const id = pickDefaultKeep(files);
    if (id) {
      // Set inline; React batches this with the render. Using set state
      // outside of an effect is generally a code smell, but here the
      // alternative (useEffect) re-runs on filesQuery refetch and would
      // fight the user's selection — choosing the lesser evil.
      const m = new Map<string, "keep" | "delete">();
      m.set(id, "keep");
      setSelection(m);
    }
  }

  const keepId = useMemo(() => {
    for (const [id, v] of selection) if (v === "keep") return id;
    return null;
  }, [selection]);

  const deleteIds = useMemo(() => {
    const out: string[] = [];
    for (const [id, v] of selection) if (v === "delete") out.push(id);
    return out;
  }, [selection]);

  const keepFile = files.find((f) => f.id === keepId) ?? null;
  const canSubmit = !!keepId && deleteIds.length > 0;

  function setKeep(id: string) {
    setSelection((prev) => {
      const next = new Map(prev);
      // Demote any existing keep to "off" — the keep slot is exclusive.
      for (const [k, v] of next) if (v === "keep") next.delete(k);
      next.set(id, "keep");
      // If this id was previously marked delete, the new "keep" wins.
      return next;
    });
  }

  function toggleDelete(id: string) {
    setSelection((prev) => {
      const next = new Map(prev);
      const cur = next.get(id);
      if (cur === "delete") next.delete(id);
      else next.set(id, "delete");
      // If we're marking the current keep as delete, clear keep so the
      // user has to pick a new keep before submit.
      if (cur === "keep") {
        next.set(id, "delete");
      }
      return next;
    });
  }

  function selectAllButKeep() {
    if (!keepId) return;
    setSelection(() => {
      const next = new Map<string, "keep" | "delete">();
      next.set(keepId, "keep");
      for (const f of files) {
        if (f.id !== keepId) next.set(f.id, "delete");
      }
      return next;
    });
  }

  async function performDelete() {
    if (!keepId) return;
    setBusy(true);
    setFailedById(new Map());
    const promise = api.deleteDuplicateCopies(group.content_hash, keepId, deleteIds);
    toast.promise(promise, {
      loading: `Deleting ${deleteIds.length} ${deleteIds.length === 1 ? "copy" : "copies"}…`,
      success: (res) => {
        const ok = res.deleted.length;
        const fail = res.failed.length;
        if (fail === 0) return `Deleted ${ok} ${ok === 1 ? "copy" : "copies"}.`;
        if (ok === 0) return `${fail} ${fail === 1 ? "copy" : "copies"} couldn't be deleted.`;
        return `Deleted ${ok}, ${fail} failed.`;
      },
      error: (e: unknown) =>
        `Bulk-delete failed: ${e instanceof Error ? e.message : "unknown error"}`,
    });
    try {
      const res = await promise;
      // Reset selection — keep id is gone since the row will re-render.
      setSelection(new Map());
      // Surface failures inline next to the surviving rows.
      const m = new Map<string, string>();
      for (const f of res.failed) {
        m.set(f.entry_id, `${f.step}: ${f.message}`);
      }
      setFailedById(m);
      // Refetch so the Card list and the per-group file list both
      // reflect the new reality.
      queryClient.invalidateQueries({ queryKey: ["duplicates"] });
      queryClient.invalidateQueries({
        queryKey: ["duplicates", group.content_hash, "files"],
      });
    } catch {
      // toast already surfaced.
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <Card padding="none">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-4 px-5 py-4 text-left hover:bg-surface-muted/60 transition-colors rounded-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-1"
      >
        <ChevronIcon open={expanded} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <code className="text-xs font-mono text-accent-700 bg-accent-50 px-1.5 py-0.5 rounded">
              {group.content_hash.substring(0, 16)}…
            </code>
            <Badge variant="neutral">{group.count} copies</Badge>
          </div>
          <div className="text-xs text-fg-muted">
            File size {formatBytes(group.file_size)} · {formatBytes(group.total_size)} stored total
          </div>
        </div>
        <div className="text-right flex-shrink-0">
          <div className="text-[11px] uppercase tracking-wide text-fg-subtle">
            Wasted
          </div>
          <div className="text-base font-semibold text-rose-600 tabular-nums">
            {formatBytes(group.wasted_bytes)}
          </div>
        </div>
      </button>
      {expanded && (
        <div className="border-t border-line-subtle">
          {filesQuery.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-fg-subtle py-3 px-5">
              <Spinner size="sm" /> Loading files…
            </div>
          ) : files.length === 0 ? (
            <p className="text-sm text-fg-subtle py-3 px-5">No files.</p>
          ) : (
            <>
              {isAdmin && (
                <div className="px-5 pt-3 pb-2 flex items-center justify-between gap-3 border-b border-line-subtle">
                  <p className="text-xs text-fg-muted">
                    Pick one copy to keep; check the others to delete from disk.
                  </p>
                  <button
                    type="button"
                    onClick={selectAllButKeep}
                    disabled={!keepId}
                    className="text-xs text-accent-700 hover:text-accent-600 font-medium disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    Select all but keeper
                  </button>
                </div>
              )}
              <ul className="divide-y divide-line-subtle">
                {files.map((file) => {
                  const state = selection.get(file.id);
                  const isKeep = state === "keep";
                  const isDelete = state === "delete";
                  const failure = failedById.get(file.id);
                  return (
                    <li
                      key={file.id}
                      className={`py-2 px-5 flex items-start gap-3 ${
                        isKeep ? "bg-emerald-50/40 dark:bg-emerald-500/5" : ""
                      } ${isDelete ? "bg-rose-50/40 dark:bg-rose-500/5" : ""}`}
                    >
                      {isAdmin && (
                        <div className="flex flex-col items-center gap-1 pt-0.5">
                          <label
                            className="flex flex-col items-center gap-0.5 cursor-pointer"
                            title="Keep this copy"
                          >
                            <input
                              type="radio"
                              name={`keep-${group.content_hash}`}
                              checked={isKeep}
                              onChange={() => setKeep(file.id)}
                              className="accent-emerald-600"
                            />
                            <span className="text-[10px] uppercase tracking-wide text-fg-subtle">
                              keep
                            </span>
                          </label>
                          <label
                            className="flex flex-col items-center gap-0.5 cursor-pointer"
                            title="Delete this copy"
                          >
                            <input
                              type="checkbox"
                              checked={isDelete}
                              onChange={() => toggleDelete(file.id)}
                              className="accent-rose-600"
                            />
                            <span className="text-[10px] uppercase tracking-wide text-fg-subtle">
                              del
                            </span>
                          </label>
                        </div>
                      )}
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-fg truncate">
                          {file.filename}
                        </div>
                        <div className="text-xs text-fg-subtle font-mono break-all">
                          {file.path}
                        </div>
                        {failure && (
                          <div className="text-xs text-rose-600 dark:text-rose-300 mt-1">
                            {failure}
                          </div>
                        )}
                      </div>
                      <div className="text-xs text-fg-subtle font-mono whitespace-nowrap pt-0.5">
                        {file.fs_modified_at
                          ? new Date(file.fs_modified_at).toLocaleDateString()
                          : "—"}
                      </div>
                    </li>
                  );
                })}
              </ul>
              {isAdmin && deleteIds.length > 0 && (
                <div className="sticky bottom-0 z-10 bg-surface border-t border-line px-5 py-3 flex flex-wrap items-center justify-between gap-3 shadow-card">
                  <div className="text-sm text-fg-muted min-w-0">
                    Keep{" "}
                    <span className="font-medium text-fg truncate">
                      {keepFile?.filename ?? "(pick one)"}
                    </span>{" "}
                    · delete{" "}
                    <span className="font-medium text-rose-600">
                      {deleteIds.length}{" "}
                      {deleteIds.length === 1 ? "copy" : "copies"}
                    </span>{" "}
                    ({formatBytes(deleteIds.length * group.file_size)} freed)
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setSelection(new Map())}
                      disabled={busy}
                    >
                      Clear
                    </Button>
                    <Button
                      size="sm"
                      variant="danger"
                      onClick={() => setConfirming(true)}
                      disabled={!canSubmit || busy}
                      loading={busy}
                    >
                      Delete {deleteIds.length}{" "}
                      {deleteIds.length === 1 ? "copy" : "copies"}
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}

      <ConfirmDialog
        open={confirming}
        title={`Delete ${deleteIds.length} ${deleteIds.length === 1 ? "copy" : "copies"} of ${keepFile?.filename ?? "this file"}?`}
        description={
          <div className="space-y-2">
            <p>
              Files will be removed from each source's filesystem and from
              the index. This can't be undone.
            </p>
            <p className="text-fg-subtle text-xs">
              Keeping:{" "}
              <code className="font-mono">{keepFile?.path ?? "—"}</code>
            </p>
          </div>
        }
        confirmLabel={`Delete ${deleteIds.length}`}
        destructive
        loading={busy}
        onConfirm={performDelete}
        onCancel={() => !busy && setConfirming(false)}
      />
    </Card>
  );
}

export default function Duplicates() {
  const { isAdmin } = useAuth();
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
    <Page
      title="Duplicates"
      description="Files with identical content stored in multiple locations."
      width="wide"
    >
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
          description={
            isAdmin
              ? "Sorted by wasted space. Expand a group to keep one copy and delete the rest from disk."
              : "Sorted by wasted space. Admin permission is required to delete copies."
          }
        />
        {isLoading ? (
          <div className="flex justify-center py-8 text-fg-subtle">
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
              <DuplicateGroupRow
                key={g.content_hash}
                group={g}
                isAdmin={isAdmin}
              />
            ))}
          </div>
        )}
      </Card>
    </Page>
  );
}
