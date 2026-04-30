import { cn } from "./cn";

type PageWidth = "compact" | "default" | "wide" | "full";

interface PageProps {
  title?: string;
  description?: string;
  action?: React.ReactNode;
  width?: PageWidth;
  className?: string;
  children: React.ReactNode;
}

// Width caps per page type. `full` lets table-heavy views use the full
// viewport (Browse, AdminAudit) — capping at max-w-6xl forced the Browse
// table into a 6-col-jam-into-1152px squish on common laptop screens.
const widthMap: Record<PageWidth, string> = {
  compact: "max-w-3xl",
  default: "max-w-5xl",
  wide: "max-w-7xl",
  full: "",
};

export function Page({
  title,
  description,
  action,
  width = "wide",
  className,
  children,
}: PageProps) {
  return (
    <div className={cn("px-6 py-7", widthMap[width], className)}>
      {(title || action) && (
        <div className="mb-6 flex items-end justify-between gap-4">
          <div className="min-w-0">
            {title && (
              <h1 className="text-2xl font-semibold text-fg tracking-tight">
                {title}
              </h1>
            )}
            {description && (
              <p className="text-sm text-fg-muted mt-1">{description}</p>
            )}
          </div>
          {action && <div className="flex-shrink-0">{action}</div>}
        </div>
      )}
      {children}
    </div>
  );
}
