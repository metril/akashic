import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  useScannerSourceReachability,
  useUpdateScannerAllowedSources,
  type SourceReachabilityRow,
} from "../../hooks/useHosts";
import {
  Button,
  ModalShell,
  ReachabilityDot,
  Spinner,
  type ReachabilityState,
} from "../ui";
import { formatRelative } from "../../lib/format";

interface Props {
  open: boolean;
  scannerId: string | null;
  scannerName: string;
  onClose: () => void;
}

const STALE_THRESHOLD_MS = 15 * 60 * 1000;

function deriveState(row: SourceReachabilityRow): {
  state: ReachabilityState;
  label: string;
  detail: string;
  recommended: boolean;
} {
  if (row.last_probed_at == null) {
    return {
      state: "unchecked",
      label: "Not yet probed from this scanner",
      detail: "no reachability data yet",
      recommended: false,
    };
  }
  const stale = Date.now() - Date.parse(row.last_probed_at) > STALE_THRESHOLD_MS;
  if (row.ok === true) {
    return {
      state: stale ? "stale" : "reachable",
      label: stale ? "Stale" : "Reachable from this scanner",
      detail: `probed ${formatRelative(row.last_probed_at)}`,
      recommended: !stale,
    };
  }
  if (row.ok === false) {
    const stepReason = row.step ? `${row.step}: ${row.error ?? "unknown"}` : (row.error ?? "unknown");
    return {
      state: "unreachable",
      label: "Path not found from this scanner — won't work if enabled",
      detail: stepReason,
      recommended: false,
    };
  }
  return {
    state: "unchecked",
    label: "No probe data",
    detail: `probed ${formatRelative(row.last_probed_at)}`,
    recommended: false,
  };
}

/**
 * Per-scanner eligibility editor. The inverse of AllowedScannersPanel:
 * one scanner across all sources, each row carrying THIS scanner's
 * latest probe outcome against THAT source. Saves directly via PATCH
 * /api/scanners/{id} (allowed_source_ids).
 */
export function AllowedSourcesModal({
  open, scannerId, scannerName, onClose,
}: Props) {
  const reachQ = useScannerSourceReachability(open ? scannerId : null);
  const update = useUpdateScannerAllowedSources();

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [original, setOriginal] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!reachQ.data) return;
    const allowed = new Set(
      reachQ.data.filter((r) => r.currently_allowed).map((r) => r.source_id),
    );
    setSelected(allowed);
    setOriginal(allowed);
  }, [reachQ.data]);

  const dirty = useMemo(() => {
    if (selected.size !== original.size) return true;
    for (const id of selected) if (!original.has(id)) return true;
    return false;
  }, [selected, original]);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function autoFillRecommended() {
    if (!reachQ.data) return;
    const next = new Set<string>();
    for (const r of reachQ.data) {
      if (deriveState(r).recommended) next.add(r.source_id);
    }
    setSelected(next);
  }

  async function handleSave() {
    if (!scannerId || !reachQ.data) return;
    const allRowIds = reachQ.data.map((r) => r.source_id);
    const all = selected.size === allRowIds.length && allRowIds.every((id) => selected.has(id));
    try {
      // If user selected EVERY source, send null = "unrestricted" (same
      // semantics in the server). Otherwise send the explicit list.
      await update.mutateAsync({
        scannerId,
        sourceIds: all ? null : Array.from(selected),
      });
      toast.success("Allowed sources updated.");
      onClose();
    } catch (e) {
      toast.error(
        `Update failed: ${e instanceof Error ? e.message : "unknown error"}`,
      );
    }
  }

  if (!open) return null;

  return (
    <ModalShell
      open={open}
      onClose={onClose}
      blocking={update.isPending}
      maxWidth="xl"
      ariaLabelledBy="allowed-sources-title"
    >
      <div className="px-5 py-3 border-b border-line">
        <h2 id="allowed-sources-title" className="text-base font-semibold text-fg">
          Sources scanner "{scannerName}" can scan
        </h2>
        <p className="text-xs text-fg-muted mt-1">
          Each row shows this scanner's most recent reachability probe
          against that source. Allowing a "won't work" source still
          queues scans — just expect them to fail at connect time.
        </p>
      </div>

      <div className="p-5 space-y-3">
        {reachQ.isLoading && (
          <div className="flex items-center gap-2 text-xs text-fg-muted">
            <Spinner /> Loading…
          </div>
        )}
        {reachQ.isError && (
          <p className="text-xs text-rose-600">
            {reachQ.error instanceof Error
              ? reachQ.error.message
              : "Failed to load source eligibility"}
          </p>
        )}
        {reachQ.data && (
          <>
            <div className="flex items-center justify-between">
              <p className="text-xs text-fg-muted">
                {selected.size} of {reachQ.data.length} source{reachQ.data.length === 1 ? "" : "s"} allowed
              </p>
              <Button
                size="sm"
                variant="ghost"
                onClick={autoFillRecommended}
                disabled={update.isPending}
              >
                Auto-fill recommended
              </Button>
            </div>
            <ul className="space-y-1.5 max-h-96 overflow-y-auto pr-1">
              {reachQ.data.map((r) => {
                const s = deriveState(r);
                return (
                  <li
                    key={r.source_id}
                    className="flex items-start gap-2 p-2 rounded-md border border-line"
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(r.source_id)}
                      onChange={() => toggle(r.source_id)}
                      className="mt-1 h-4 w-4 rounded border-line text-blue-600 focus:ring-blue-400"
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="text-sm font-medium text-fg truncate">
                          {r.source_name}
                        </span>
                        <span className="text-[11px] text-fg-muted">
                          {r.source_type}
                          {r.host_name ? ` on ${r.host_name}` : ""}
                        </span>
                        {s.recommended && (
                          <span className="text-[10px] uppercase tracking-wide text-emerald-700 dark:text-emerald-300 font-semibold">
                            ★ Recommended
                          </span>
                        )}
                      </div>
                      <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-fg-muted">
                        <ReachabilityDot state={s.state} />
                        <span className="font-medium text-fg">{s.label}</span>
                        <span>· {s.detail}</span>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </div>

      <div className="px-5 py-3 border-t border-line flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose} disabled={update.isPending}>
          Cancel
        </Button>
        <Button
          onClick={handleSave}
          loading={update.isPending}
          disabled={!dirty}
        >
          Save
        </Button>
      </div>
    </ModalShell>
  );
}
