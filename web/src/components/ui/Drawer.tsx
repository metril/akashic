import { useEffect } from "react";
import { cn } from "./cn";

interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactNode;
  width?: "sm" | "md" | "lg" | "xl";
}

const widthMap = {
  sm: "max-w-md",
  md: "max-w-lg",
  lg: "max-w-2xl",
  // Wider for long, code-like content (live scan log paths). At 896px
  // a typical SMB path no longer wraps mid-segment on a 1080p screen.
  xl: "max-w-4xl",
};

export function Drawer({
  open,
  onClose,
  title,
  description,
  children,
  width = "md",
}: DrawerProps) {
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  return (
    <div
      aria-hidden={!open}
      className={cn(
        "fixed inset-0 z-50 pointer-events-none",
        open && "pointer-events-auto",
      )}
    >
      <div
        className={cn(
          "absolute inset-0 bg-gray-900/30 backdrop-blur-[2px] transition-opacity duration-200",
          open ? "opacity-100" : "opacity-0",
        )}
        onClick={onClose}
      />
      <aside
        role="dialog"
        aria-modal="true"
        className={cn(
          "absolute right-0 top-0 h-full w-full bg-surface shadow-2xl",
          "border-l border-line flex flex-col",
          "transition-transform duration-200 ease-out",
          widthMap[width],
          open ? "translate-x-0" : "translate-x-full",
        )}
      >
        {(title || description) && (
          <header className="flex items-start justify-between gap-4 px-6 py-4 border-b border-line-subtle">
            <div className="min-w-0">
              {title && (
                <h2 className="text-base font-semibold text-fg truncate">
                  {title}
                </h2>
              )}
              {description && (
                <p className="text-xs text-fg-muted mt-0.5 truncate">
                  {description}
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="flex-shrink-0 p-1.5 rounded-md text-fg-subtle hover:text-fg hover:bg-surface-muted transition-colors"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 20 20"
                fill="currentColor"
                className="h-4 w-4"
              >
                <path
                  fillRule="evenodd"
                  d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                  clipRule="evenodd"
                />
              </svg>
            </button>
          </header>
        )}
        <div className="flex-1 overflow-y-auto">{children}</div>
      </aside>
    </div>
  );
}
