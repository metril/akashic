import { cn } from "./cn";
import { Skeleton } from "./Skeleton";
import { EmptyState } from "./EmptyState";

export interface Column<T> {
  key: string;
  header: React.ReactNode;
  render: (row: T) => React.ReactNode;
  className?: string;
  headerClassName?: string;
}

interface TableProps<T> {
  columns: Column<T>[];
  data: T[];
  rowKey: (row: T) => string;
  loading?: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
  className?: string;
}

export function Table<T>({
  columns,
  data,
  rowKey,
  loading,
  emptyTitle = "No data",
  emptyDescription,
  className,
}: TableProps<T>) {
  if (loading) {
    return (
      <div className={cn("space-y-2", className)}>
        <Skeleton className="h-9" />
        <Skeleton className="h-9" />
        <Skeleton className="h-9" />
      </div>
    );
  }

  if (data.length === 0) {
    return (
      <EmptyState title={emptyTitle} description={emptyDescription} />
    );
  }

  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line">
            {columns.map((col) => (
              <th
                key={col.key}
                className={cn(
                  "text-left text-xs font-semibold text-fg-muted uppercase tracking-wide py-2.5 px-3",
                  col.headerClassName,
                )}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line-subtle">
          {data.map((row) => (
            <tr
              key={rowKey(row)}
              className="hover:bg-surface-muted/60 transition-colors"
            >
              {columns.map((col) => (
                <td
                  key={col.key}
                  className={cn("py-3 px-3 text-fg", col.className)}
                >
                  {col.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
