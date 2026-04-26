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

const variantMap: Record<BadgeVariant, string> = {
  online: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  offline: "bg-gray-100 text-gray-600 ring-gray-500/20",
  scanning: "bg-accent-50 text-accent-700 ring-accent-600/20",
  failed: "bg-rose-50 text-rose-700 ring-rose-600/20",
  neutral: "bg-gray-100 text-gray-700 ring-gray-500/15",
  info: "bg-sky-50 text-sky-700 ring-sky-600/20",
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
