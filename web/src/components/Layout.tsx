import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { clearToken, api } from "../api/client";
import { cn } from "./ui/cn";
import { BrandMark } from "./BrandMark";

interface NavItem {
  to: string;
  label: string;
  icon: React.ReactNode;
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

const Icon = ({ d }: { d: string }) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.75"
    strokeLinecap="round"
    strokeLinejoin="round"
    className="h-[18px] w-[18px] flex-shrink-0"
  >
    <path d={d} />
  </svg>
);

const sections: NavSection[] = [
  {
    label: "Overview",
    items: [
      {
        to: "/dashboard",
        label: "Dashboard",
        icon: <Icon d="M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z" />,
      },
    ],
  },
  {
    label: "Index",
    items: [
      {
        to: "/browse",
        label: "Browse",
        icon: (
          <Icon d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
        ),
      },
      {
        to: "/search",
        label: "Search",
        icon: <Icon d="M21 21l-4.35-4.35M11 19a8 8 0 100-16 8 8 0 000 16z" />,
      },
      {
        to: "/duplicates",
        label: "Duplicates",
        icon: <Icon d="M9 9h10v10H9zM5 5h10v10" />,
      },
      {
        to: "/analytics",
        label: "Analytics",
        icon: (
          <Icon d="M3 21h18M5 21V10m4 11V4m4 17v-7m4 7V8m4 13v-3" />
        ),
      },
    ],
  },
  {
    label: "Setup",
    items: [
      {
        to: "/sources",
        label: "Sources",
        icon: <Icon d="M3 7h18M3 12h18M3 17h18" />,
      },
      {
        to: "/settings",
        label: "Settings",
        end: true,
        icon: (
          <Icon d="M12 1.5a2.5 2.5 0 011.95 4.06l1.04 1.81a8 8 0 011.97 0l1.04-1.81a2.5 2.5 0 11-1.95 4.06l-1.04 1.81a8 8 0 010 1.96l1.04 1.81a2.5 2.5 0 11-4.06 1.95l-1.81-1.04a8 8 0 01-1.96 0l-1.81 1.04a2.5 2.5 0 11-1.95-4.06l-1.04-1.81a8 8 0 010-1.96L4.42 7.62A2.5 2.5 0 116.37 3.56l1.81 1.04a8 8 0 011.96 0L11.18 2.79A2.5 2.5 0 0112 1.5z" />
        ),
      },
    ],
  },
  {
    label: "Admin",
    adminOnly: true,
    items: [
      {
        to: "/admin/audit",
        label: "Audit log",
        icon: <Icon d="M9 12l2 2 4-4M21 12c0 4.97-4.03 9-9 9s-9-4.03-9-9 4.03-9 9-9 9 4.03 9 9z" />,
      },
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
                {section.items.map(({ to, label, icon, end }) => (
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
                    {icon}
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
            <Icon d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9" />
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
