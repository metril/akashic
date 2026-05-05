import { useEffect, useRef, useState } from "react";
import { cn } from "./cn";
import { Scrim } from "./Scrim";

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

  // v0.4.13 Phase 2 — apply `transition-transform` to the aside ONLY
  // during the open/close animation window. Permanently-applied
  // transitions keep the compositor layer hot, contributing to the
  // hover stutter on buttons inside the drawer. We toggle the class
  // on when `open` flips and back off when the transform transition
  // ends (filtered by propertyName so the overlay's opacity
  // transitionend doesn't clear it prematurely).
  const [transitioning, setTransitioning] = useState(false);
  const prevOpenRef = useRef(open);
  useEffect(() => {
    if (prevOpenRef.current !== open) {
      setTransitioning(true);
      prevOpenRef.current = open;
    }
  }, [open]);

  return (
    <div
      aria-hidden={!open}
      className={cn(
        "fixed inset-0 z-50 pointer-events-none",
        open && "pointer-events-auto",
      )}
    >
      {/* v0.4.13 Phase 1 — dropped `backdrop-blur-[2px]` from the
          overlay. backdrop-filter creates a permanent GPU compositor
          layer for the full open duration; that layer is what was
          producing the hover stutter on buttons inside the drawer
          (cursor input gets gated on the GPU's blur work during
          hover-induced micro-repaints). The blur compounded when
          nested — the live log panel uses its own <Drawer>, stacking
          two blur layers. Alpha bumped 30 -> 45 so the scrim still
          reads as "modal active" without relying on the blur for
          visual contrast. */}
      <Scrim
        onClick={onClose}
        className={cn(
          "transition-opacity duration-200",
          open ? "opacity-100" : "opacity-0",
        )}
      />
      <aside
        role="dialog"
        aria-modal="true"
        onTransitionEnd={(e) => {
          if (e.propertyName === "transform") setTransitioning(false);
        }}
        className={cn(
          "absolute right-0 top-0 h-full w-full bg-surface shadow-2xl",
          "border-l border-line flex flex-col",
          transitioning && "transition-transform duration-200 ease-out",
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
