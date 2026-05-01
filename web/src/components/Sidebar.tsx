import { NavLink } from "react-router-dom";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { cn } from "./ui/cn";
import { Icon, type IconName } from "./ui";
import { BrandMark } from "./BrandMark";

interface NavItem {
  to: string;
  label: string;
  iconName: IconName;
  end?: boolean;
}

interface NavSection {
  label: string;
  adminOnly?: boolean;
  items: NavItem[];
}

const sections: NavSection[] = [
  {
    label: "Overview",
    items: [{ to: "/dashboard", label: "Dashboard", iconName: "dashboard" }],
  },
  {
    label: "Index",
    items: [
      { to: "/browse",     label: "Browse",     iconName: "folder" },
      { to: "/search",     label: "Search",     iconName: "search" },
      { to: "/duplicates", label: "Duplicates", iconName: "duplicates" },
      { to: "/analytics",  label: "Analytics",  iconName: "analytics" },
    ],
  },
  {
    label: "Setup",
    items: [
      { to: "/sources",  label: "Sources",  iconName: "sources" },
      { to: "/settings", label: "Settings", iconName: "settings", end: true },
    ],
  },
  {
    label: "Admin",
    adminOnly: true,
    items: [
      { to: "/admin/access", label: "Access", iconName: "shield" },
      { to: "/admin/audit", label: "Audit log", iconName: "audit-log" },
    ],
  },
];

const COLLAPSED_KEY = "sidebar-collapsed";

function readStoredCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

interface SidebarProps {
  /** Off-canvas state for the mobile sheet. Ignored on md+ viewports. */
  mobileOpen: boolean;
  onMobileClose: () => void;
}

export function Sidebar({ mobileOpen, onMobileClose }: SidebarProps) {
  const [collapsed, setCollapsed] = useState<boolean>(() => readStoredCollapsed());

  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
    } catch {
      // Storage may be blocked — preference will reset next session.
    }
  }, [collapsed]);

  const me = useQuery<{ role: string }>({
    queryKey: ["me"],
    queryFn: () => api.get<{ role: string }>("/users/me"),
  });
  const isAdmin = me.data?.role === "admin";
  const visibleSections = sections.filter((s) => !s.adminOnly || isAdmin);

  // Inner panel — same content used both for the static md+ sidebar and
  // the off-canvas mobile sheet. Collapsed mode hides labels; off-canvas
  // mode is always full-width regardless of collapsed.
  const Panel = ({ forceFullWidth = false }: { forceFullWidth?: boolean }) => {
    const isCollapsed = collapsed && !forceFullWidth;
    return (
      <div
        className={cn(
          "h-full flex flex-col bg-surface border-r border-line",
          isCollapsed ? "w-14" : "w-60",
          "transition-[width] duration-200",
        )}
      >
        <div className={cn("py-5 border-b border-line-subtle", isCollapsed ? "px-3" : "px-5")}>
          <BrandMark showWordmark={!isCollapsed} />
        </div>

        <nav className={cn("flex-1 overflow-y-auto py-4 space-y-5", isCollapsed ? "px-2" : "px-3")}>
          {visibleSections.map((section) => (
            <div key={section.label}>
              {!isCollapsed && (
                <h3 className="px-3 mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">
                  {section.label}
                </h3>
              )}
              <div className="space-y-0.5">
                {section.items.map(({ to, label, iconName, end }) => (
                  <NavLink
                    key={to}
                    to={to}
                    end={end ?? false}
                    onClick={forceFullWidth ? onMobileClose : undefined}
                    title={isCollapsed ? label : undefined}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-3 rounded-md text-sm font-medium",
                        "transition-colors duration-100",
                        isCollapsed ? "px-2 py-2 justify-center" : "px-3 py-2",
                        "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500",
                        isActive
                          ? "bg-accent-50 text-accent-700"
                          : "text-fg-muted hover:bg-surface-muted hover:text-fg",
                      )
                    }
                  >
                    <Icon name={iconName} className="h-[18px] w-[18px]" />
                    {!isCollapsed && <span className="truncate">{label}</span>}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>

        {/* Collapse toggle — only on md+ viewports, hidden inside the mobile sheet */}
        {!forceFullWidth && (
          <div className={cn("py-3 border-t border-line-subtle", isCollapsed ? "px-2" : "px-3")}>
            <button
              type="button"
              onClick={() => setCollapsed((v) => !v)}
              aria-label={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              title={isCollapsed ? "Expand" : "Collapse"}
              className={cn(
                "w-full flex items-center gap-3 rounded-md text-sm font-medium text-fg-muted",
                "hover:bg-surface-muted hover:text-fg transition-colors",
                isCollapsed ? "px-2 py-2 justify-center" : "px-3 py-2",
                "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500",
              )}
            >
              <Icon
                path={
                  isCollapsed
                    ? "M9 5l7 7-7 7"
                    : "M15 19l-7-7 7-7"
                }
                className="h-[18px] w-[18px]"
              />
              {!isCollapsed && <span>Collapse</span>}
            </button>
          </div>
        )}
      </div>
    );
  };

  return (
    <>
      {/* Static sidebar — md+ viewports */}
      <aside className="hidden md:block flex-shrink-0">
        <Panel />
      </aside>

      {/* Off-canvas sheet — < md */}
      <div
        aria-hidden={!mobileOpen}
        className={cn(
          "md:hidden fixed inset-0 z-40",
          mobileOpen ? "pointer-events-auto" : "pointer-events-none",
        )}
      >
        <div
          className={cn(
            "absolute inset-0 bg-gray-900/40 transition-opacity duration-200",
            mobileOpen ? "opacity-100" : "opacity-0",
          )}
          onClick={onMobileClose}
        />
        <aside
          className={cn(
            "absolute left-0 top-0 h-full",
            "transition-transform duration-200 ease-out",
            mobileOpen ? "translate-x-0" : "-translate-x-full",
          )}
        >
          <Panel forceFullWidth />
        </aside>
      </div>
    </>
  );
}
