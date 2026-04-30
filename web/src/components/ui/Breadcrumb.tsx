import { Fragment } from "react";
import { cn } from "./cn";

export interface BreadcrumbSegment {
  label: string;
  onClick?: () => void;
}

interface BreadcrumbProps {
  segments: BreadcrumbSegment[];
  className?: string;
}

const Separator = () => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 20 20"
    fill="currentColor"
    className="h-3.5 w-3.5 text-fg-subtle flex-shrink-0"
    aria-hidden="true"
  >
    <path
      fillRule="evenodd"
      d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z"
      clipRule="evenodd"
    />
  </svg>
);

export function Breadcrumb({ segments, className }: BreadcrumbProps) {
  return (
    <nav
      aria-label="Breadcrumb"
      className={cn(
        "flex items-center gap-1.5 text-sm text-fg-muted min-w-0",
        className,
      )}
    >
      {segments.map((seg, i) => {
        const isLast = i === segments.length - 1;
        const clickable = !isLast && seg.onClick !== undefined;
        return (
          <Fragment key={i}>
            {clickable ? (
              <button
                type="button"
                onClick={seg.onClick}
                className="text-fg-muted hover:text-accent-700 hover:underline truncate"
              >
                {seg.label}
              </button>
            ) : (
              <span
                className={cn(
                  "truncate",
                  isLast ? "text-fg font-medium" : "text-fg-muted",
                )}
              >
                {seg.label}
              </span>
            )}
            {!isLast && <Separator />}
          </Fragment>
        );
      })}
    </nav>
  );
}
