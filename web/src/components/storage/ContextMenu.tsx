/**
 * Right-click context menu for treemap rectangles. The menu is purely
 * presentational — the page passes in items it wants rendered. Click
 * outside / Escape closes.
 */
import { useEffect, useRef } from "react";

export interface ContextMenuItem {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}

interface ContextMenuProps {
  x: number;
  y: number;
  items: ContextMenuItem[];
  onClose: () => void;
  /** Width/height of the parent positioning context, so the menu can
   *  flip into view if it would overflow the container. */
  containerWidth: number;
  containerHeight: number;
}

const MENU_WIDTH = 240;

export function ContextMenu({
  x,
  y,
  items,
  onClose,
  containerWidth,
  containerHeight,
}: ContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    // Defer the click listener so the synthetic right-click that
    // opened us doesn't immediately close it.
    const t = setTimeout(() => {
      document.addEventListener("click", onDocClick);
    }, 0);
    document.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(t);
      document.removeEventListener("click", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  // Estimated menu height — items are ~32 px each. Used only for
  // edge-flip; over-estimating clips a tiny bit at the bottom but
  // never spills.
  const menuHeight = items.length * 32 + 8;
  const left = Math.min(x, Math.max(0, containerWidth - MENU_WIDTH - 4));
  const top = Math.min(y, Math.max(0, containerHeight - menuHeight - 4));

  return (
    <div
      ref={menuRef}
      role="menu"
      style={{
        position: "absolute",
        left,
        top,
        width: MENU_WIDTH,
        zIndex: 50,
      }}
      className="rounded-md border border-line bg-surface shadow-lg py-1 text-sm"
    >
      {items.map((item, i) => (
        <button
          key={i}
          type="button"
          role="menuitem"
          onClick={(e) => {
            e.stopPropagation();
            if (item.disabled) return;
            item.onClick();
            onClose();
          }}
          disabled={item.disabled}
          className="w-full text-left px-3 py-1.5 text-fg hover:bg-surface-muted disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
