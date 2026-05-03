import { memo } from "react";
import type { Source } from "../../types";
import { formatRelative } from "../../lib/format";

interface Props {
  source: Source;
  /** When true, renders only the dot + label (compact list view).
   *  When false, also renders the relative-time hint. */
  compact?: boolean;
}

/**
 * Reachability indicator for sources flagged `is_removable`.
 * Returns null for fixed sources — there's no "is the disk plugged in"
 * question to answer.
 *
 *  - is_reachable=true   → green dot, "Reachable, last seen Xm ago"
 *  - is_reachable=false  → amber dot, "Unmounted, last seen Xh ago"
 *  - is_reachable=null   → grey dot,  "Not yet checked"
 */
export const ReachabilityBadge = memo(function ReachabilityBadge({
  source,
  compact = false,
}: Props) {
  if (!source.is_removable) return null;

  let dotClass: string;
  let label: string;
  let detail: string | null;

  if (source.is_reachable === true) {
    dotClass = "bg-emerald-500";
    label = "Reachable";
    detail = source.last_reachable_at
      ? `last seen ${formatRelative(source.last_reachable_at)}`
      : null;
  } else if (source.is_reachable === false) {
    dotClass = "bg-amber-500";
    label = "Unmounted";
    detail = source.last_reachable_at
      ? `last seen ${formatRelative(source.last_reachable_at)}`
      : "never reached";
  } else {
    dotClass = "bg-fg-subtle";
    label = "Not yet checked";
    detail = null;
  }

  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs text-fg-muted"
      title={
        source.last_reachability_check_at
          ? `Last check: ${new Date(source.last_reachability_check_at).toLocaleString()}`
          : "No reachability check has been run yet."
      }
    >
      <span className={`inline-block h-2 w-2 rounded-full ${dotClass}`} />
      <span className="font-medium text-fg">{label}</span>
      {!compact && detail && <span>· {detail}</span>}
    </span>
  );
});
