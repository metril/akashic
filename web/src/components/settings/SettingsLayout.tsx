import { useState } from "react";
import { Outlet } from "react-router-dom";
import { Icon } from "../ui";
import { cn } from "../ui/cn";
import { SettingsSidebar } from "./SettingsSidebar";

/**
 * Two-column shell for `/settings/*`. The left rail renders the
 * grouped settings nav; the right pane renders the active sub-route
 * via `<Outlet/>`. Sub-pages keep their own `<Page title=…>` wrapper
 * — the layout is purely structural so headings don't double up.
 *
 * On `<md` viewports the sidebar collapses behind a button that opens
 * an off-canvas sheet (mirrors the top-level Layout sidebar pattern).
 */
export default function SettingsLayout() {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="flex md:items-stretch">
      {/* Static sidebar — md+ */}
      <aside className="hidden md:block flex-shrink-0 w-56 border-r border-line bg-surface">
        <SettingsSidebar />
      </aside>

      {/* Mobile trigger + off-canvas sheet — < md */}
      <div className="md:hidden">
        <button
          type="button"
          onClick={() => setMobileOpen(true)}
          className={cn(
            "m-3 flex items-center gap-2 rounded-md border border-line",
            "bg-surface px-3 py-1.5 text-sm font-medium text-fg-muted",
            "hover:bg-surface-muted hover:text-fg",
            "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500",
          )}
          aria-label="Open settings menu"
          aria-expanded={mobileOpen}
        >
          <Icon
            path="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5"
            className="h-4 w-4"
          />
          Menu
        </button>

        <div
          aria-hidden={!mobileOpen}
          className={cn(
            "fixed inset-0 z-40",
            mobileOpen ? "pointer-events-auto" : "pointer-events-none",
          )}
        >
          <div
            className={cn(
              "absolute inset-0 bg-gray-900/40 transition-opacity duration-200",
              mobileOpen ? "opacity-100" : "opacity-0",
            )}
            onClick={() => setMobileOpen(false)}
          />
          <aside
            className={cn(
              "absolute left-0 top-0 h-full w-64 bg-surface border-r border-line",
              "transition-transform duration-200 ease-out",
              mobileOpen ? "translate-x-0" : "-translate-x-full",
            )}
          >
            <SettingsSidebar onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      </div>

      {/* Right pane: each sub-page renders its own <Page title=…> */}
      <div className="flex-1 min-w-0">
        <Outlet />
      </div>
    </div>
  );
}
