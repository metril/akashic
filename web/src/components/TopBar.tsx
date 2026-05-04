import { BrandMark } from "./BrandMark";
import { ThemeToggle, Icon } from "./ui";
import { UserMenu } from "./UserMenu";
import { usePalette } from "../hooks/usePalette";
import { cn } from "./ui/cn";

interface TopBarProps {
  /** Open the mobile sidebar sheet. md+ viewports get a static sidebar
   * and don't need this. */
  onMobileNavOpen: () => void;
}

// Detect Mac for the keyboard hint. SSR-safe via the `typeof navigator`
// check; defaults to "Ctrl" on the server.
function platformShortcut(): string {
  if (typeof navigator === "undefined") return "Ctrl K";
  return /Mac|iPhone|iPad/i.test(navigator.userAgent) ? "⌘ K" : "Ctrl K";
}

export function TopBar({ onMobileNavOpen }: TopBarProps) {
  const palette = usePalette();
  const openPalette = () => palette.setOpen(true);

  return (
    <header
      className={cn(
        "h-14 flex-shrink-0 flex items-center gap-3 px-4 md:px-5",
        "bg-surface border-b border-line",
      )}
    >
      {/* Hamburger only on mobile — sidebar handles itself at md+. */}
      <button
        type="button"
        onClick={onMobileNavOpen}
        aria-label="Open navigation"
        className="md:hidden -ml-1 inline-flex items-center justify-center h-9 w-9 rounded-md text-fg-muted hover:text-fg hover:bg-surface-muted focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
      >
        <Icon path="M4 6h16M4 12h16M4 18h16" className="h-5 w-5" />
      </button>

      {/* Brand only shows on mobile (sidebar carries it on md+). */}
      <div className="md:hidden">
        <BrandMark showWordmark />
      </div>

      <div className="flex-1" />

      <button
        type="button"
        onClick={openPalette}
        className={cn(
          "hidden sm:inline-flex items-center gap-2 h-9 px-3 rounded-lg font-medium",
          "bg-surface text-fg border border-line hover:bg-surface-muted active:bg-surface-muted",
          "transition-colors text-sm",
          "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2",
        )}
        title="Quick search — files, pages, actions"
        aria-label="Open quick search palette"
      >
        <Icon name="search" className="h-4 w-4 text-fg-muted" />
        <span>Search…</span>
        <kbd className="ml-2 hidden md:inline-flex items-center px-1.5 h-5 rounded border border-line bg-app text-[11px] font-mono text-fg-muted">
          {platformShortcut()}
        </kbd>
      </button>

      <ThemeToggle />
      <UserMenu />
    </header>
  );
}
