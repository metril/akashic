import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  useScannerSourceReachability,
  useTestScannerSources,
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

/**
 * v0.28.0 — On-demand mirror of AllowedScannersPanel from the scanner
 * side. Same rules: no staleness, no auto-recommendation. The user
 * explicitly probes; the user explicitly decides.
 */

function deriveState(row: SourceReachabilityRow): {
  state: ReachabilityState;
  label: string;
  detail: string;
} {
  if (row.last_probed_at == null || row.ok == null) {
    return {
      state: "unchecked",
      label: "Not tested",
      detail: "click Test to probe",
    };
  }
  if (row.ok) {
    return {
      state: "reachable",
      label: "Reachable",
      detail: `tested ${formatRelative(row.last_probed_at)}`,
    };
  }
  const reason = row.step ? `${row.step}: ${row.error ?? "unknown"}` : (row.error ?? "unknown");
  return {
    state: "unreachable",
    label: "Failed",
    detail: reason,
  };
}

export function AllowedSourcesModal({
  open, scannerId, scannerName, onClose,
}: Props) {
  const reachQ = useScannerSourceReachability(open ? scannerId : null);
  const update = useUpdateScannerAllowedSources();
  const test = useTestScannerSources();

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

  async function handleSave() {
    if (!scannerId || !reachQ.data) return;
    const allRowIds = reachQ.data.map((r) => r.source_id);
    const all = selected.size === allRowIds.length && allRowIds.every((id) => selected.has(id));
    try {
      await update.mutateAsync({
        scannerId,
        sourceIds: all ? null : Array.from(selected),
      });
      toast.success("Allowed sources updated.");
      onClose();
    } catch (e) {
      toast.error(
        `Couldn't save allowed sources: ${e instanceof Error ? e.message : "unknown error"}.`,
      );
    }
  }

  async function handleTestAll() {
    if (!scannerId) return;
    try {
      const res = await test.mutateAsync({ scannerId });
      const pending = res.results.filter((r) => r.pending).length;
      const failed = res.results.filter((r) => r.ok === false).length;
      const ok = res.results.filter((r) => r.ok === true).length;
      const summary =
        pending > 0
          ? `${ok} ok, ${failed} failed, ${pending} still pending`
          : `${ok} ok, ${failed} failed`;
      toast.success(`Reachability tested — ${summary}.`);
    } catch (e) {
      toast.error(
        `Test failed: ${e instanceof Error ? e.message : "unknown error"}.`,
      );
    }
  }

  async function handleTestOne(sourceId: string) {
    if (!scannerId) return;
    try {
      await test.mutateAsync({ scannerId, sourceIds: [sourceId] });
    } catch (e) {
      toast.error(
        `Test failed: ${e instanceof Error ? e.message : "unknown error"}.`,
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
          against that source. Reachability is on-demand — click Test to
          probe. Allowing a "failed" source still queues scans; expect
          them to fail at connect time.
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
                onClick={handleTestAll}
                loading={test.isPending}
                title="Run a reachability probe against every source."
              >
                Test all
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
                      className="mt-1 h-4 w-4 rounded border-line text-accent-600 focus:ring-accent-400"
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
                      </div>
                      <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-fg-muted">
                        <ReachabilityDot state={s.state} />
                        <span className="font-medium text-fg">{s.label}</span>
                        <span>· {s.detail}</span>
                      </div>
                    </div>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleTestOne(r.source_id)}
                      loading={test.isPending}
                    >
                      Test
                    </Button>
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
