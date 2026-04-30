import { cn } from "./cn";

export type BadgeVariant =
  | "online"
  | "offline"
  | "scanning"
  | "failed"
  | "neutral"
  | "info";

interface BadgeProps {
  variant?: BadgeVariant;
  className?: string;
  children: React.ReactNode;
}

// Status backgrounds get a dark-mode variant tuned for legibility on
// dark surfaces. The light shades become invisible on dark; we switch
// to a translucent tint + brighter text to keep the "status pill"
// affordance.
const variantMap: Record<BadgeVariant, string> = {
  online:
    "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-400/20",
  offline:
    "bg-surface-muted text-fg-muted ring-line",
  scanning:
    "bg-accent-50 text-accent-700 ring-accent-600/20 dark:bg-accent-500/15 dark:text-accent-200 dark:ring-accent-500/30",
  failed:
    "bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-500/10 dark:text-rose-300 dark:ring-rose-500/30",
  neutral:
    "bg-surface-muted text-fg ring-line",
  info:
    "bg-sky-50 text-sky-700 ring-sky-600/20 dark:bg-sky-500/10 dark:text-sky-300 dark:ring-sky-500/30",
};

export function Badge({ variant = "neutral", className, children }: BadgeProps) {
  const isPulsing = variant === "scanning";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full",
        "text-xs font-medium ring-1 ring-inset",
        variantMap[variant],
        className,
      )}
    >
      {isPulsing && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent-500 opacity-75"></span>
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-accent-500"></span>
        </span>
      )}
      {children}
    </span>
  );
}
