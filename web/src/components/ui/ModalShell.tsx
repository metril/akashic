import { useEffect } from "react";
import { cn } from "./cn";
import { Scrim } from "./Scrim";

type MaxWidth = "sm" | "md" | "lg" | "xl";

interface ModalShellProps {
  open: boolean;
  onClose: () => void;
  /** Max width of the inner card. Defaults to "md" (~28rem). */
  maxWidth?: MaxWidth;
  /** Disable both ESC and click-outside while busy. */
  blocking?: boolean;
  /** ARIA label or labelledby id for the dialog. */
  ariaLabelledBy?: string;
  ariaLabel?: string;
  children: React.ReactNode;
}

const MAX_WIDTH_CLASS: Record<MaxWidth, string> = {
  sm: "max-w-sm",
  md: "max-w-md",
  lg: "max-w-lg",
  xl: "max-w-xl",
};

/**
 * Shared overlay + centered card shell for modal dialogs.
 *
 * Centralises three behaviours every modal in the app should share:
 *   - Overlay styling (`bg-gray-900/55`, fixed inset, dim scrim).
 *   - ESC closes (when not `blocking`).
 *   - Click-outside closes (when not `blocking`).
 *
 * Used by `ConfirmDialog`, `DeleteSourceModal`, `RecoverOrphansModal`.
 * Keep this thin — header / body / footer composition is the caller's
 * job so each modal can carry its own typography and spacing.
 */
export function ModalShell({
  open,
  onClose,
  maxWidth = "md",
  blocking = false,
  ariaLabelledBy,
  ariaLabel,
  children,
}: ModalShellProps) {
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !blocking) onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, blocking, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={ariaLabelledBy}
      aria-label={ariaLabel}
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
    >
      <Scrim onClick={blocking ? undefined : onClose} />
      <div
        onClick={(e) => e.stopPropagation()}
        className={cn(
          "relative w-full rounded-xl bg-surface shadow-2xl",
          "border border-line/70",
          MAX_WIDTH_CLASS[maxWidth],
        )}
      >
        {children}
      </div>
    </div>
  );
}
