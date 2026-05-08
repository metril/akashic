import { useEffect, useRef } from "react";
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
  const dialogRef = useRef<HTMLDivElement>(null);

  // Focus management (review W-I3): on open, save the previously-
  // focused element, focus the first focusable inside the dialog,
  // and trap Tab/Shift-Tab inside the dialog. On close, restore
  // focus to the saved element so keyboard users return to where
  // they were.
  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;

    function focusables(): HTMLElement[] {
      if (!dialog) return [];
      return Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((el) => !el.hasAttribute("hidden"));
    }
    const list = focusables();
    if (list[0]) {
      list[0].focus();
    } else if (dialog) {
      dialog.tabIndex = -1;
      dialog.focus();
    }

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !blocking) {
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const items = focusables();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey) {
        if (active === first || !dialog?.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (active === last || !dialog?.contains(active)) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      // Restore focus to the trigger so keyboard navigation continues
      // from where it was.
      if (previouslyFocused && document.body.contains(previouslyFocused)) {
        previouslyFocused.focus();
      }
    };
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
        ref={dialogRef}
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
