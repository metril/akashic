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
    <Card padding="md" className={cn("flex items-start gap-4", className)}>
      {icon && (
        <div className="h-9 w-9 rounded-lg bg-accent-50 text-accent-600 flex items-center justify-center flex-shrink-0">
          {icon}
        </div>
      )}
      <div className="min-w-0 flex-1">
        <div className="text-xs font-medium text-gray-500 uppercase tracking-wide">
          {label}
        </div>
        {loading ? (
          <Skeleton className="h-7 w-20 mt-2" />
        ) : (
          <div className="text-2xl font-semibold text-gray-900 mt-1 tabular-nums">
            {value}
          </div>
        )}
        {subtext && (
          <div className="text-xs text-gray-500 mt-1">{subtext}</div>
        )}
      </div>
    </Card>
  );
}
