import { memo } from "react";
import { cn } from "./cn";

export type ReachabilityState = "reachable" | "unreachable" | "unchecked";

interface Props {
  state: ReachabilityState;
  /** Tailwind size override; default `h-2 w-2`. */
  size?: string;
  className?: string;
}

const STATE_CLASS: Record<ReachabilityState, string> = {
  reachable:   "bg-emerald-500",
  unreachable: "bg-rose-500",
  unchecked:   "bg-fg-subtle",
};

/**
 * Coloured status dot used by reachability surfaces (source cards,
 * host rows, eligibility checklists). Pure presentation — callers
 * derive the state from the source/host data.
 *
 * v0.28.0 dropped the `stale` / `stale_unchecked` states. With on-
 * demand probing, "stale" no longer means anything — the latest
 * probe is the latest probe; the per-row history disclosure on the
 * eligibility panels surfaces age + trend.
 */
export const ReachabilityDot = memo(function ReachabilityDot({
  state, size = "h-2 w-2", className,
}: Props) {
  return (
    <span
      className={cn(
        "inline-block rounded-full",
        size,
        STATE_CLASS[state],
        className,
      )}
      aria-hidden="true"
    />
  );
});
