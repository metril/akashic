import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { clearToken } from "../api/client";
import { cn } from "./ui/cn";
import { BrandMark } from "./BrandMark";

interface NavItem {
  to: string;
  label: string;
  icon: React.ReactNode;
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

const navItems: NavItem[] = [
  {
    to: "/dashboard",
    label: "Dashboard",
    icon: <Icon d="M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z" />,
  },
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
    icon: (
      <Icon d="M21 21l-4.35-4.35M11 19a8 8 0 100-16 8 8 0 000 16z" />
    ),
  },
  {
    to: "/sources",
    label: "Sources",
    icon: (
      <Icon d="M3 7h18M3 12h18M3 17h18" />
    ),
  },
  {
    to: "/duplicates",
    label: "Duplicates",
    icon: (
      <Icon d="M9 9h10v10H9zM5 5h10v10" />
    ),
  },
  {
    to: "/analytics",
    label: "Analytics",
    icon: (
      <Icon d="M3 21h18M5 21V10m4 11V4m4 17v-7m4 7V8m4 13v-3" />
    ),
  },
];

export default function Layout() {
  const navigate = useNavigate();

  function handleLogout() {
    clearToken();
    navigate("/login");
  }

  return (
    <div className="flex min-h-screen bg-gray-50">
      <aside className="w-60 bg-white border-r border-gray-200 flex flex-col flex-shrink-0">
        <div className="px-5 py-5 border-b border-gray-100">
          <BrandMark showWordmark />
        </div>

        <nav className="flex-1 px-3 py-4 space-y-1">
          {navItems.map(({ to, label, icon }) => (
            <NavLink
              key={to}
              to={to}
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
