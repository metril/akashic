import { cn } from "./cn";
import { Card } from "./Card";
import { Skeleton } from "./Skeleton";

interface StatCardProps {
  label: string;
  value: string | number;
  subtext?: string;
  icon?: React.ReactNode;
  loading?: boolean;
  className?: string;
}

export function StatCard({
  label,
  value,
  subtext,
  icon,
  loading,
  className,
}: StatCardProps) {
  return (
    <Card padding="sm" className={cn("flex items-start gap-3", className)}>
      {icon && (
        <div className="h-8 w-8 rounded-lg bg-accent-50 text-accent-600 flex items-center justify-center flex-shrink-0">
          {icon}
        </div>
      )}
      <div className="min-w-0 flex-1">
        <div className="text-[11px] font-medium text-fg-muted uppercase tracking-wide">
          {label}
        </div>
        {loading ? (
          <Skeleton className="h-7 w-20 mt-1.5" />
        ) : (
          <div className="text-2xl font-semibold text-fg mt-0.5 tabular-nums">
            {value}
          </div>
        )}
        {subtext && (
          <div className="text-xs text-fg-muted mt-0.5">{subtext}</div>
        )}
      </div>
    </Card>
  );
}
