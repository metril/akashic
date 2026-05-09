import { NavLink } from "react-router-dom";
import { cn } from "../ui/cn";

interface SettingsLeaf {
  to: string;
  label: string;
  description: string;
}

interface SettingsGroup {
  label: string;
  items: SettingsLeaf[];
}

const groups: SettingsGroup[] = [
  {
    label: "Connections",
    items: [
      {
        to: "/settings/credentials",
        label: "Credentials",
        description:
          "Reusable credential profiles — define once, attach to many hosts and shares.",
      },
      {
        to: "/settings/oauth",
        label: "OAuth providers",
        description:
          "OAuth client apps for Google Drive, OneDrive, and Dropbox.",
      },
    ],
  },
  {
    label: "Operations",
    items: [
      {
        to: "/settings/scanners",
        label: "Scanners",
        description:
          "Registered scanner agents. Mint keypairs, set pools, see online status.",
      },
      {
        to: "/settings/schedules",
        label: "Schedules",
        description:
          "Source scan cadences. One row per source with editable cron strings.",
      },
    ],
  },
  {
    label: "Identity & Access",
    items: [
      {
        to: "/settings/identities",
        label: "Identities",
        description:
          "Cross-source identity sets and per-source bindings. Used for ACL-aware search.",
      },
    ],
  },
  {
    label: "Data Model",
    items: [
      {
        to: "/settings/tags",
        label: "Tags",
        description: "Custom labels applied to entries for filter and search.",
      },
    ],
  },
];

export function SettingsSidebar({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="space-y-5 px-3 py-4" aria-label="Settings">
      {groups.map((group) => (
        <div key={group.label}>
          <h3 className="px-3 mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">
            {group.label}
          </h3>
          <div className="space-y-0.5">
            {group.items.map(({ to, label, description }) => (
              <NavLink
                key={to}
                to={to}
                onClick={onNavigate}
                title={description}
                className={({ isActive }) =>
                  cn(
                    "flex items-center rounded-md px-3 py-2 text-sm font-medium",
                    "transition-colors duration-100",
                    "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500",
                    isActive
                      ? "bg-accent-50 text-accent-700"
                      : "text-fg-muted hover:bg-surface-muted hover:text-fg",
                  )
                }
              >
                <span className="truncate">{label}</span>
              </NavLink>
            ))}
          </div>
        </div>
      ))}
    </nav>
  );
}
