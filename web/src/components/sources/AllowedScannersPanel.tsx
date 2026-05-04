import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  useSourceScannerReachability,
  useUpdateSourceAllowedScanners,
  type ScannerReachabilityRow,
} from "../../hooks/useSources";
import {
  Button,
  ConfirmDialog,
  ReachabilityDot,
  Spinner,
  type ReachabilityState,
} from "../ui";
import { formatRelative } from "../../lib/format";

interface Props {
  sourceId: string;
}

const STALE_THRESHOLD_MS = 15 * 60 * 1000;

function deriveRowState(row: ScannerReachabilityRow): {
  state: ReachabilityState;
  label: string;
  detail: string;
  recommended: boolean;
} {
  if (!row.online) {
    return {
      state: "unchecked",
      label: "Offline",
      detail: row.last_probed_at
        ? `last probe ${formatRelative(row.last_probed_at)}`
        : "scanner has never reported",
      recommended: false,
    };
  }
  if (row.last_probed_at == null) {
    return {
      state: "unchecked",
      label: "Not yet probed",
      detail: "no reachability data",
      recommended: false,
    };
  }
  const probedAt = Date.parse(row.last_probed_at);
  const stale = Date.now() - probedAt > STALE_THRESHOLD_MS;
  if (row.ok === true) {
    return {
      state: stale ? "stale" : "reachable",
      label: stale ? "Stale" : "Reaches this source",
      detail: `probed ${formatRelative(row.last_probed_at)}`,
      recommended: !stale,
    };
  }
  if (row.ok === false) {
    const stepReason = row.step ? `${row.step}: ${row.error ?? "unknown"}` : (row.error ?? "unknown");
    return {
      state: "unreachable",
      label: "Cannot reach",
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
 * Per-source eligibility editor. Each row shows the scanner's name,
 * pool, online state, and the latest probe outcome AGAINST THIS
 * SOURCE. Lets the user check or uncheck which scanners may claim
 * scans for this source. Pre-fill matches the api's current state.
 *
 * "Auto-fill recommended" pre-checks every 🟢 row (recent
 * result_ok=true) and unchecks every 🔴 row. The user can override
 * either way; saving a 🔴 selection prompts a confirm because
 * allowing a scanner that's been proven unable to reach the source
 * just queues work that fails. v0.5.7.
 */
export function AllowedScannersPanel({ sourceId }: Props) {
  const reachQ = useSourceScannerReachability(sourceId);
  const update = useUpdateSourceAllowedScanners();

  // Local checkbox state — initialised from the server's view of
  // currently_allowed once the data lands. Diff against the original
  // set to know if there's anything to save.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [original, setOriginal] = useState<Set<string>>(new Set());
  const [confirmingDoomed, setConfirmingDoomed] = useState(false);
  const [doomed, setDoomed] = useState<ScannerReachabilityRow[]>([]);

  useEffect(() => {
    if (!reachQ.data) return;
    const allowed = new Set(
      reachQ.data.filter((r) => r.currently_allowed).map((r) => r.scanner_id),
    );
    setSelected(allowed);
    setOriginal(allowed);
  }, [reachQ.data]);

  const dirty = useMemo(() => {
    if (selected.size !== original.size) return true;
    for (const id of selected) if (!original.has(id)) return true;
    return false;
  }, [selected, original]);

  function toggle(scannerId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(scannerId)) next.delete(scannerId);
      else next.add(scannerId);
      return next;
    });
  }

  function autoFillRecommended() {
    if (!reachQ.data) return;
    const next = new Set<string>();
    for (const r of reachQ.data) {
      const state = deriveRowState(r);
      if (state.recommended) next.add(r.scanner_id);
    }
    setSelected(next);
  }

  async function performSave(scannerIds: string[]) {
    try {
      await update.mutateAsync({ sourceId, scannerIds });
      toast.success("Allowed scanners updated.");
    } catch (e) {
      toast.error(
        `Update failed: ${e instanceof Error ? e.message : "unknown error"}`,
      );
    }
  }

  async function handleSave() {
    if (!reachQ.data) return;
    const ids = Array.from(selected);
    // Doomed = scanners we're enabling that were proven unable to reach.
    const doomedRows = reachQ.data.filter(
      (r) => selected.has(r.scanner_id) && r.ok === false,
    );
    if (doomedRows.length > 0) {
      setDoomed(doomedRows);
      setConfirmingDoomed(true);
      return;
    }
    await performSave(ids);
  }

  if (reachQ.isLoading) {
    return (
      <div className="flex items-center gap-2 text-xs text-fg-muted">
        <Spinner /> Loading scanner eligibility…
      </div>
    );
  }
  if (reachQ.isError) {
    return (
      <p className="text-xs text-rose-600">
        {reachQ.error instanceof Error
          ? reachQ.error.message
          : "Failed to load scanner eligibility"}
      </p>
    );
  }
  const rows = reachQ.data ?? [];
  if (rows.length === 0) {
    return (
      <p className="text-xs text-fg-muted italic">
        No scanners registered yet. Install one via Settings → Scanners.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-[11px] text-fg-muted">
        <em>Online</em> means the scanner agent is checking in.
        The colored dot shows whether that scanner has reached this source.
      </p>
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-fg-muted">
          {selected.size} of {rows.length} scanner{rows.length === 1 ? "" : "s"} allowed
        </p>
        <Button
          size="sm"
          variant="ghost"
          onClick={autoFillRecommended}
          disabled={update.isPending}
          title="Pre-check every scanner that's recently proven able to reach this source; uncheck the ones that have been proven unable."
        >
          Auto-fill recommended
        </Button>
      </div>

      <ul className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
        {rows.map((r) => {
          const s = deriveRowState(r);
          return (
            <li
              key={r.scanner_id}
              className="flex items-start gap-2 p-2 rounded-md border border-line"
            >
              <input
                type="checkbox"
                checked={selected.has(r.scanner_id)}
                onChange={() => toggle(r.scanner_id)}
                className="mt-1 h-4 w-4 rounded border-line text-blue-600 focus:ring-blue-400"
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className="text-sm font-medium text-fg truncate">
                    {r.name}
                  </span>
                  {r.pool && (
                    <span className="text-[11px] text-fg-muted">
                      pool={r.pool}
                    </span>
                  )}
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

      <div className="flex items-center justify-end gap-2 pt-1">
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setSelected(original)}
          disabled={!dirty || update.isPending}
        >
          Reset
        </Button>
        <Button
          size="sm"
          onClick={handleSave}
          loading={update.isPending}
          disabled={!dirty}
        >
          Save
        </Button>
      </div>

      <ConfirmDialog
        open={confirmingDoomed}
        title={`Allow ${doomed.length} scanner${doomed.length === 1 ? "" : "s"} that can't reach this source?`}
        description={
          <div className="space-y-2">
            <p>
              The following scanners have a recent failed probe against this
              source. Allowing them queues work that will fail at scan time.
            </p>
            <ul className="text-xs font-mono space-y-0.5">
              {doomed.map((r) => (
                <li key={r.scanner_id}>
                  {r.name} — {r.step ?? "unknown"}: {r.error ?? "no detail"}
                </li>
              ))}
            </ul>
          </div>
        }
        confirmLabel="Allow anyway"
        loading={update.isPending}
        onConfirm={async () => {
          setConfirmingDoomed(false);
          await performSave(Array.from(selected));
        }}
        onCancel={() => !update.isPending && setConfirmingDoomed(false)}
      />
    </div>
  );
}
