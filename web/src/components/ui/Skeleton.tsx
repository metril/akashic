import { cn } from "./cn";

interface SkeletonProps {
  className?: string;
  count?: number;
}

export function Skeleton({ className, count = 1 }: SkeletonProps) {
  if (count === 1) {
    return (
      <div
        className={cn("animate-pulse bg-gray-200/70 rounded", className)}
      />
    );
  }
  return (
    <div className="space-y-2">
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className={cn("animate-pulse bg-gray-200/70 rounded h-4", className)}
        />
      ))}
    </div>
  );
}
