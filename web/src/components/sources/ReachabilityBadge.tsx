import { memo } from "react";
import type { Source } from "../../types";
import { formatRelative } from "../../lib/format";
import { ReachabilityDot, type ReachabilityState } from "../ui";

interface Props {
  source: Source;
  /** When true, renders only the dot + label (compact list view).
   *  When false, also renders the relative-time hint. */
  compact?: boolean;
}

// v0.5.6 — match `2 × reachability_check_interval_seconds` (default
// 5 min × 2 = 10 min). Server-side configuration; the badge falls
// back to this constant if the operator hasn't exposed the setting.
const STALENESS_THRESHOLD_MS = 10 * 60 * 1000;

function isStale(checkedAt: string | null): boolean {
  if (!checkedAt) return false;
  return Date.now() - Date.parse(checkedAt) > STALENESS_THRESHOLD_MS;
}

function deriveState(source: Source): {
  state: ReachabilityState;
  label: string;
  detail: string | null;
} {
  const stale = isStale(source.last_reachability_check_at);

  if (source.is_reachable === true) {
    if (stale) {
      return {
        state: "stale",
        label: "Stale (was reachable)",
        detail: source.last_reachable_at
          ? `last seen ${formatRelative(source.last_reachable_at)}`
          : null,
      };
    }
    return {
      state: "reachable",
      label: "Reachable",
      detail: source.last_reachable_at
        ? `last seen ${formatRelative(source.last_reachable_at)}`
        : null,
    };
  }

  if (source.is_reachable === false) {
    return {
      state: "unreachable",
      label: "Unreachable",
      detail: source.last_reachable_at
        ? `last reached ${formatRelative(source.last_reachable_at)}`
        : "never reached",
    };
  }

  // is_reachable === null
  if (source.last_reachability_check_at && stale) {
    // The misconfigured-pool case: a check was attempted but no
    // probe ever returned, so we never learned reachability.
    return {
      state: "stale_unchecked",
      label: "Stale (no recent probe)",
      detail: "no scanner has reported",
    };
  }
  return {
    state: "unchecked",
    label: "Not yet checked",
    detail: null,
  };
}

/**
 * Reachability indicator. v0.5.6 renders for every source — prior
 * versions showed it only when ``is_removable`` was true, which hid
 * the data the api was already collecting on every scan completion.
 *
 * Five states:
 *   - "Reachable" (green)              — fresh ok=true
 *   - "Stale" (yellow)                 — was reachable but no probe
 *                                         in 10 min
 *   - "Unreachable" (red)              — fresh ok=false
 *   - "Not yet checked" (grey)         — never probed
 *   - "Stale" (yellow, "no scanner")   — probed but never returned;
 *                                         signals a misconfigured pool
 */
export const ReachabilityBadge = memo(function ReachabilityBadge({
  source,
  compact = false,
}: Props) {
  const { state, label, detail } = deriveState(source);
  const tooltip = source.last_reachability_check_at
    ? `Last check: ${new Date(source.last_reachability_check_at).toLocaleString()}`
    : "No reachability check has been run yet.";

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
