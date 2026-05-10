import { memo } from "react";
import { useReachabilitySummary } from "../../hooks/useSources";
import { formatRelative } from "../../lib/format";
import { ReachabilityDot, type ReachabilityState } from "../ui";

interface Props {
  sourceId: string;
  /** When true, renders only the dot + label (compact list view).
   *  When false, also renders the relative-time hint. */
  compact?: boolean;
}

/**
 * Reachability indicator.
 *
 * v0.28.0: cached source.is_reachable + last_reachable_at fields are
 * gone. Reachability is derived on read from the latest
 * reachability_results row across all scanners (or from the latest
 * successful scan, which is the strongest probe). No staleness gate —
 * once a source has reached, it stays reached until a fresh failure
 * contradicts it.
 *
 * States:
 *   - "Reachable" (green)        — latest probe ok OR scan succeeded
 *   - "Unreachable" (red)        — latest probe failed
 *   - "Not yet checked" (grey)   — no data
 */
export const ReachabilityBadge = memo(function ReachabilityBadge({
  sourceId,
  compact = false,
}: Props) {
  const summaryQ = useReachabilitySummary(sourceId);
  const summary = summaryQ.data;

  let state: ReachabilityState = "unchecked";
  let label = "Not yet checked";
  let detail: string | null = null;
  let tooltip = "No reachability data yet — click Test Scanners on the source.";

  if (summary?.ok === true) {
    state = "reachable";
    label = "Reachable";
    detail = summary.last_at ? `verified ${formatRelative(summary.last_at)}` : null;
    tooltip = summary.last_at
      ? `Last verified: ${new Date(summary.last_at).toLocaleString()}`
      : tooltip;
  } else if (summary?.ok === false) {
    state = "unreachable";
    label = "Unreachable";
    const reason = summary.last_step
      ? `${summary.last_step}: ${summary.last_error ?? "unknown"}`
      : (summary.last_error ?? "no detail");
    detail = reason;
    tooltip = summary.last_at
      ? `Last failed: ${new Date(summary.last_at).toLocaleString()} — ${reason}`
      : `Latest probe failed — ${reason}`;
  }

  return (
    <span
      data-state={state}
      className="inline-flex items-center gap-1.5 text-xs text-fg-muted"
      title={tooltip}
    >
      <ReachabilityDot state={state} />
      <span className="font-medium text-fg">{label}</span>
      {!compact && detail && <span>· {detail}</span>}
    </span>
  );
});
