import { memo } from "react";
import { cn } from "./cn";

export type ReachabilityState =
  | "reachable"
  | "stale"
  | "unreachable"
  | "unchecked"
  | "stale_unchecked";

interface Props {
  state: ReachabilityState;
  /** Tailwind size override; default `h-2 w-2`. */
  size?: string;
  className?: string;
}

const STATE_CLASS: Record<ReachabilityState, string> = {
  reachable:        "bg-emerald-500",
  stale:            "bg-amber-500",
  unreachable:      "bg-rose-500",
  unchecked:        "bg-fg-subtle",
  stale_unchecked:  "bg-amber-500",
};

/**
 * Coloured status dot used by reachability surfaces (source cards,
 * host rows, eligibility checklists). Pure presentation — callers
 * derive the state from the source/host data.
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
