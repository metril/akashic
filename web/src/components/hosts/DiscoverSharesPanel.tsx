import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "../ui";
import {
  useAddShares,
  useListShares,
  type ListSharesResult,
} from "../../hooks/useHosts";
import { useSources } from "../../hooks/useSources";
import type { Host } from "../../types";

interface Props {
  host: Host;
  /** Called after the batch-add finishes successfully. */
  onAdded?: () => void;
}

interface ShareRow {
  share: string;
  /** Source name to create. Defaults to the bare share name; the host
   *  group on the Sources page already labels the parent host so a
   *  prefix would be redundant. v0.5.5. */
  name: string;
  selected: boolean;
  /** True when the host already has a Source attached for this share. */
  alreadyAdded: boolean;
}

/**
 * Discover-and-add panel mounted under a host's detail drawer.
 *
 * Flow:
 *   1. On mount, calls /api/hosts/{id}/list-shares (one round trip).
 *   2. Renders the result as a checkbox list. Existing shares (already
 *      attached to this host) render checked + disabled with an
 *      "(already added)" note so the user sees the delta.
 *   3. User adjusts which to add and the source-row names, then submits.
 *   4. POST /api/hosts/{id}/add-shares creates N Source rows in one
 *      transaction (per-row, so a duplicate name skips that one and
 *      proceeds). Toast reports created/skipped counts.
 *
 * Hidden by HostDetail for local hosts (no shares concept).
 */
export function DiscoverSharesPanel({ host, onAdded }: Props) {
  const listShares = useListShares();
  const addShares = useAddShares();
  const sourcesQuery = useSources();

  const [discovered, setDiscovered] = useState<ListSharesResult | null>(null);
  const [rows, setRows] = useState<ShareRow[]>([]);

  const existingShareValues = useMemo(() => {
    if (!sourcesQuery.data) return new Set<string>();
    const key = host.type === "smb" ? "share"
      : host.type === "nfs" ? "export_path"
      : host.type === "s3" ? "bucket"
      : "";
    if (!key) return new Set<string>();
    const out = new Set<string>();
    for (const s of sourcesQuery.data) {
      if (s.host_id !== host.id) continue;
      const v = (s.connection_config as Record<string, unknown> | undefined)?.[key];
      if (typeof v === "string") out.add(v);
    }
    return out;
  }, [sourcesQuery.data, host.id, host.type]);

  // Fire enumeration once when the host changes.
  useEffect(() => {
    setDiscovered(null);
    setRows([]);
    listShares.mutate(host.id, {
      onSuccess: (res) => {
        setDiscovered(res);
        const next: ShareRow[] = (res.shares ?? []).map((share) => {
          const already = existingShareValues.has(share);
          return {
            share,
            // v0.5.5: was `${host.name}/${share}` — the host header on
            // the Sources page already labels the parent, so the prefix
            // was redundant noise.
            name: share,
            selected: !already,
            alreadyAdded: already,
          };
        });
        setRows(next);
      },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [host.id]);

  const selectableCount = rows.filter((r) => !r.alreadyAdded).length;
  const selectedCount = rows.filter((r) => r.selected && !r.alreadyAdded).length;

  function toggleAll(checked: boolean) {
    setRows((prev) =>
      prev.map((r) => (r.alreadyAdded ? r : { ...r, selected: checked })),
    );
  }

  function setRowSelected(idx: number, checked: boolean) {
    setRows((prev) =>
      prev.map((r, i) => (i === idx ? { ...r, selected: checked } : r)),
    );
  }

  function setRowName(idx: number, name: string) {
    setRows((prev) => prev.map((r, i) => (i === idx ? { ...r, name } : r)));
  }

  async function handleAdd() {
    const toAdd = rows
      .filter((r) => r.selected && !r.alreadyAdded)
      .map((r) => ({ name: r.name.trim(), share: r.share }));
    if (toAdd.length === 0) {
      toast.error("Select at least one share to add.");
      return;
    }
    const blank = toAdd.find((r) => r.name === "");
    if (blank) {
      toast.error(`Source name for "${blank.share}" can't be blank.`);
      return;
    }
    const p = addShares.mutateAsync({ hostId: host.id, body: { shares: toAdd } });
    toast.promise(p, {
      loading: `Adding ${toAdd.length} source${toAdd.length === 1 ? "" : "s"}…`,
      success: (r) =>
        r.skipped > 0
          ? `Added ${r.created}; skipped ${r.skipped} (name conflict).`
          : `Added ${r.created} source${r.created === 1 ? "" : "s"}.`,
      error: (e: unknown) =>
        `Couldn't add shares: ${e instanceof Error ? e.message : "unknown error"}.`,
    });
    try {
      await p;
      onAdded?.();
    } catch {
      // toast already surfaced
    }
  }

  // Probe state.
  if (listShares.isPending && !discovered) {
    return (
      <p className="text-sm text-fg-muted">
        Probing {host.name} for shares…
      </p>
    );
  }
  if (discovered?.step) {
    return (
      <div className="rounded-md p-2 text-xs bg-rose-50 text-rose-900 dark:bg-rose-500/15 dark:text-rose-100">
        Discovery failed at <strong>{discovered.step}</strong>:{" "}
        {discovered.error ?? "unknown error"}
        <p className="mt-1 opacity-80">
          Check the host's credentials on the <em>Edit</em> tab and try again.
        </p>
      </div>
    );
  }
  if (!discovered) return null;

  if (rows.length === 0) {
    return (
      <div className="rounded-md p-2 text-xs bg-app border border-line-subtle text-fg-muted">
        <p className="font-medium text-fg">No discoverable shares.</p>
        <p className="mt-1">
          The host responded but didn't advertise any usable shares.
          Possible causes:
        </p>
        <ul className="list-disc pl-5 mt-1 space-y-0.5">
          <li>Credentials lack list permission on the share namespace.</li>
          {host.type === "smb" && (
            <li>Only administrative shares (IPC$, ADMIN$) are exposed; those are filtered out.</li>
          )}
          {host.type === "nfs" && (
            <li>The MOUNT3 export list is empty or restricted by the server.</li>
          )}
          {host.type === "s3" && (
            <li>The IAM principal can authenticate but lacks <code className="font-mono">s3:ListAllMyBuckets</code>.</li>
          )}
        </ul>
      </div>
    );
  }

  const allSelectableChecked =
    selectableCount > 0 && selectedCount === selectableCount;
  const partiallyChecked =
    selectableCount > 0 && selectedCount > 0 && selectedCount < selectableCount;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-fg-muted">
          {rows.length} share{rows.length === 1 ? "" : "s"} discovered ·{" "}
          {selectedCount} selected
        </p>
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <IndeterminateCheckbox
            checked={allSelectableChecked}
            indeterminate={partiallyChecked}
            disabled={selectableCount === 0}
            onChange={(e) => toggleAll(e.target.checked)}
          />
          Select all
        </label>
      </div>

      <ul className="space-y-2 max-h-80 overflow-y-auto pr-1">
        {rows.map((r, idx) => (
          <li
            key={r.share}
            className={`flex items-start gap-2 p-2 rounded-md border ${
              r.alreadyAdded
                ? "bg-app border-line-subtle"
                : "border-line"
            }`}
          >
            <input
              type="checkbox"
              checked={r.selected}
              disabled={r.alreadyAdded}
              onChange={(e) => setRowSelected(idx, e.target.checked)}
              className="mt-1.5 h-4 w-4 rounded border-line text-accent-600 focus:ring-accent-400"
            />
            <div className="flex-1 min-w-0 space-y-1">
              <div className="flex items-center gap-2">
                <code className="text-xs text-fg font-mono truncate">
                  {r.share}
                </code>
                {r.alreadyAdded && (
                  <span className="text-[11px] text-fg-muted italic">
                    (already added)
                  </span>
                )}
              </div>
              {!r.alreadyAdded && (
                <input
                  type="text"
                  value={r.name}
                  onChange={(e) => setRowName(idx, e.target.value)}
                  placeholder="Source name"
                  className="w-full rounded-md border border-line px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-accent-400 focus:border-accent-400"
                />
              )}
            </div>
          </li>
        ))}
      </ul>

      <div className="sticky bottom-0 -mx-2 px-2 pt-2 pb-1 bg-surface/95 backdrop-blur-sm border-t border-line-subtle space-y-1.5">
        <p className="text-[11px] text-fg-muted leading-snug">
          Source names must be globally unique — duplicates skip and you can
          rename in place.
        </p>
        <div className="flex justify-end">
          <Button
            size="sm"
            onClick={handleAdd}
            loading={addShares.isPending}
            disabled={selectedCount === 0}
            reserveLabel="Add 999 sources"
            className="tabular-nums"
          >
            Add{selectedCount > 0 ? ` ${selectedCount}` : ""}{" "}
            source{selectedCount === 1 ? "" : "s"}
          </Button>
        </div>
      </div>
    </div>
  );
}

/**
 * Native HTML checkboxes can't be set to "indeterminate" via a JSX
 * prop — only via the DOM property. This thin wrapper sets it on
 * mount / update so a partially-selected list shows the dash icon
 * instead of false-checked.
 */
function IndeterminateCheckbox({
  checked,
  indeterminate,
  disabled,
  onChange,
}: {
  checked: boolean;
  indeterminate: boolean;
  disabled?: boolean;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <input
      ref={ref}
      type="checkbox"
      checked={checked}
      disabled={disabled}
      onChange={onChange}
      className="h-4 w-4 rounded border-line text-accent-600 focus:ring-accent-400"
    />
  );
}
