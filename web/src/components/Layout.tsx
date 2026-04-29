import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { clearToken, api } from "../api/client";
import { cn } from "./ui/cn";
import { Icon, type IconName } from "./ui";
import { BrandMark } from "./BrandMark";

interface NavItem {
  to: string;
  label: string;
  iconName: IconName;
  /** When true, the active state only matches the exact path — sub-routes
   * don't keep this item highlighted. Used by /settings so /settings/identities
   * doesn't drag the sidebar Settings entry into the active state. */
  end?: boolean;
}

interface NavSection {
  label: string;
  adminOnly?: boolean;
  items: NavItem[];
}

const NAV_ICON_CLASS = "h-[18px] w-[18px]";

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
      { to: "/admin/audit", label: "Audit log", iconName: "audit-log" },
    ],
  },
];

export default function Layout() {
  const navigate = useNavigate();

  const me = useQuery<{ role: string }>({
    queryKey: ["me"],
    queryFn: () => api.get<{ role: string }>("/users/me"),
  });
  const isAdmin = me.data?.role === "admin";

  function handleLogout() {
    clearToken();
    navigate("/login");
  }

  const visibleSections = sections.filter((s) => !s.adminOnly || isAdmin);

  return (
    <div className="flex min-h-screen bg-gray-50">
      <aside className="w-60 bg-white border-r border-gray-200 flex flex-col flex-shrink-0">
        <div className="px-5 py-5 border-b border-gray-100">
          <BrandMark showWordmark />
        </div>

        <nav className="flex-1 px-3 py-4 space-y-5">
          {visibleSections.map((section) => (
            <div key={section.label}>
              <h3 className="px-3 mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
                {section.label}
              </h3>
              <div className="space-y-0.5">
                {section.items.map(({ to, label, iconName, end }) => (
                  <NavLink
                    key={to}
                    to={to}
                    end={end ?? false}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium",
                        "transition-colors duration-100",
                        isActive
                          ? "bg-accent-50 text-accent-700"
                          : "text-gray-600 hover:bg-gray-50 hover:text-gray-900",
                      )
                    }
                  >
                    <Icon name={iconName} className={NAV_ICON_CLASS} />
                    <span>{label}</span>
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>

        <div className="px-3 py-4 border-t border-gray-100">
          <button
            onClick={handleLogout}
            className="w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium text-gray-600 hover:bg-gray-50 hover:text-gray-900 transition-colors"
          >
            <Icon name="sign-out" className={NAV_ICON_CLASS} />
            <span>Sign out</span>
          </button>
        </div>
      </aside>

      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}
