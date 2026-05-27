import {
  useSourceReachabilityHistory,
  type PerScannerHistory,
  type ReachabilityOutcome,
} from "../../hooks/useSources";
import { formatDateTime } from "../../lib/format";

interface ReachabilityHistoryTabProps {
  sourceId: string;
  visible: boolean;
}

/**
 * v0.41.0 — per-scanner probe-outcome history for one source.
 *
 * Renders one section per scanner: a "Latest:" header line plus a
 * chronological table of outcomes (newest first). No colored-dot
 * sparkline — the badge already shows the latest result; this view
 * exists for diagnosing flap patterns, which a textual log does
 * better than a row of identical dots.
 *
 * The probe history is the same `reachability_results` table that
 * feeds the badge and the AllowedScannersPanel; this tab just shows
 * a deeper slice (up to 20 outcomes per scanner) and groups them
 * for easy per-scanner scanning.
 */
export function ReachabilityHistoryTab({
  sourceId,
  visible,
}: ReachabilityHistoryTabProps) {
  const { data, isLoading, error } = useSourceReachabilityHistory(
    sourceId,
    visible,
  );

  if (!visible) return null;
  if (isLoading) {
    return (
      <p className="text-sm text-fg-muted">Loading reachability history…</p>
    );
  }
  if (error) {
    return (
      <p className="text-sm text-rose-600">
        {error instanceof Error
          ? error.message
          : "Failed to load reachability history"}
      </p>
    );
  }
  const groups = data?.per_scanner ?? [];
  if (groups.length === 0) {
    return (
      <p className="text-sm text-fg-muted">
        No probes recorded yet. The Test button on the source detail
        panel writes a row each time it runs, and successful scan
        completions also count as proof of reachability.
      </p>
    );
  }

  return (
    <div className="space-y-6">
      {groups.map((g) => (
        <ScannerHistorySection key={g.scanner_id ?? "inline"} group={g} />
      ))}
    </div>
  );
}

function ScannerHistorySection({ group }: { group: PerScannerHistory }) {
  const latest = group.outcomes[0];
  const scannerLabel =
    group.scanner_name ?? "Inline (probed by API directly)";
  return (
    <section>
      <header className="mb-2 border-b border-line pb-1">
        <div className="flex items-baseline gap-3">
          <h3 className="text-sm font-medium text-fg">{scannerLabel}</h3>
          {latest != null && (
            <span className="text-xs text-fg-muted">
              Latest:{" "}
              <StatusWord ok={latest.ok} />
              {" "}({formatDateTime(latest.completed_at)})
            </span>
          )}
        </div>
      </header>
      <table className="w-full text-xs">
        <thead className="text-fg-muted">
          <tr>
            <th className="text-left font-normal py-1 pr-3 w-44">When</th>
            <th className="text-left font-normal py-1 pr-3 w-28">Status</th>
            <th className="text-left font-normal py-1 pr-3 w-20">Step</th>
            <th className="text-left font-normal py-1">Error</th>
          </tr>
        </thead>
        <tbody>
          {group.outcomes.map((o, i) => (
            <OutcomeRow key={`${o.completed_at}-${i}`} outcome={o} />
          ))}
        </tbody>
      </table>
    </section>
  );
}

function OutcomeRow({ outcome }: { outcome: ReachabilityOutcome }) {
  return (
    <tr className="border-t border-line/60">
      <td className="py-1 pr-3 text-fg-muted whitespace-nowrap">
        {formatDateTime(outcome.completed_at)}
      </td>
      <td className="py-1 pr-3">
        <StatusWord ok={outcome.ok} />
      </td>
      <td className="py-1 pr-3 text-fg-muted">
        {outcome.step ?? "—"}
      </td>
      <td className="py-1 text-fg-muted break-words">
        {outcome.error ?? ""}
      </td>
    </tr>
  );
}

function StatusWord({ ok }: { ok: boolean }) {
  return ok ? (
    <span className="text-emerald-700 dark:text-emerald-400">reachable</span>
  ) : (
    <span className="text-rose-700 dark:text-rose-400">unreachable</span>
  );
}
