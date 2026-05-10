import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  useSourceScannerReachability,
  useTestSourceScanners,
  useUpdateSourceAllowedScanners,
  type ScannerReachabilityHistoryEntry,
  type ScannerReachabilityRow,
} from "../../hooks/useSources";
import {
  Button,
  ReachabilityDot,
  Spinner,
  type ReachabilityState,
} from "../ui";
import { formatRelative } from "../../lib/format";

interface Props {
  sourceId: string;
}

/**
 * v0.28.0 — On-demand reachability eligibility editor.
 *
 * Replaces the v0.5.7 stale / "★ Recommended" / "Auto-fill recommended"
 * UX. Continuous polling is gone, so freshness is a function of when
 * the user last clicked Test. The panel:
 *
 *   - shows every scanner with a checkbox for allow / disallow
 *   - shows the most recent probe outcome per scanner (no staleness
 *     gate — the latest result is the latest result)
 *   - exposes a per-row "Test" button + bulk "Test all"
 *   - discloses the last few results inline as coloured dots so
 *     trends are visible without leaving the panel
 *
 * No nanny prompt: the user explicitly chose what to allow, and the
 * scan failure path will surface real problems clearly enough.
 */

function deriveRowState(row: ScannerReachabilityRow): {
  state: ReachabilityState;
  label: string;
  detail: string;
} {
  if (row.last_probed_at == null || row.ok == null) {
    return {
      state: "unchecked",
      label: "Not tested",
      detail: row.online
        ? "click Test to probe"
        : "scanner offline",
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

function HistoryDots({ history }: { history: ScannerReachabilityHistoryEntry[] }) {
  if (!history || history.length === 0) return null;
  return (
    <span className="inline-flex items-center gap-1 ml-2" aria-label="Recent probe history">
      {history.slice(0, 5).map((h, i) => (
        <span
          key={i}
          title={
            (h.completed_at ? `${formatRelative(h.completed_at)} — ` : "") +
            (h.ok ? "ok" : `failed${h.step ? ` (${h.step})` : ""}`)
          }
          className={`inline-block h-1.5 w-1.5 rounded-full ${
            h.ok ? "bg-emerald-500" : "bg-rose-500"
          }`}
        />
      ))}
    </span>
  );
}

export function AllowedScannersPanel({ sourceId }: Props) {
  const reachQ = useSourceScannerReachability(sourceId);
  const update = useUpdateSourceAllowedScanners();
  const test = useTestSourceScanners();

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [original, setOriginal] = useState<Set<string>>(new Set());

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

  async function handleSave() {
    const ids = Array.from(selected);
    try {
      await update.mutateAsync({ sourceId, scannerIds: ids });
      toast.success("Allowed scanners updated.");
    } catch (e) {
      toast.error(
        `Couldn't save allowed scanners: ${e instanceof Error ? e.message : "unknown error"}.`,
      );
    }
  }

  async function handleTestAll() {
    try {
      const res = await test.mutateAsync({ sourceId });
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

  async function handleTestOne(scannerId: string) {
    try {
      await test.mutateAsync({ sourceId, scannerIds: [scannerId] });
    } catch (e) {
      toast.error(
        `Test failed: ${e instanceof Error ? e.message : "unknown error"}.`,
      );
    }
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
        Reachability is on-demand — click Test to probe.
      </p>
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-fg-muted">
          {selected.size} of {rows.length} scanner{rows.length === 1 ? "" : "s"} allowed
        </p>
        <Button
          size="sm"
          variant="ghost"
          onClick={handleTestAll}
          loading={test.isPending}
          title="Run a reachability probe against every scanner."
        >
          Test all
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
                className="mt-1 h-4 w-4 rounded border-line text-accent-600 focus:ring-accent-400"
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
                  {!r.online && (
                    <span className="text-[10px] uppercase tracking-wide text-fg-subtle">
                      offline
                    </span>
                  )}
                </div>
                <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-fg-muted">
                  <ReachabilityDot state={s.state} />
                  <span className="font-medium text-fg">{s.label}</span>
                  <span>· {s.detail}</span>
                  <HistoryDots history={r.history} />
                </div>
              </div>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => handleTestOne(r.scanner_id)}
                loading={test.isPending}
                disabled={!r.online}
                title={r.online ? "Probe this scanner against this source." : "Scanner is offline."}
              >
                Test
              </Button>
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
    </div>
  );
}
