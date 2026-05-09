import { NavLink, Outlet } from "react-router-dom";

import { Icon, Page, type IconName } from "../components/ui";
import { cn } from "../components/ui/cn";

interface SettingsTab {
  to: string;
  label: string;
  iconName: IconName;
  /** Tooltip on the tab; describes what the section is for. */
  hint: string;
}

/**
 * Settings is a single full-width page with a top-tab strip — same
 * structural shape as every other page in the app (Sources, Browse,
 * Search …) instead of the v0.25/v0.26 nested-sidebar pattern that
 * felt out of place. Each tab is a real route so deep links keep
 * working; the active tab content renders inline below.
 */
const TABS: SettingsTab[] = [
  {
    to: "/settings/credentials",
    label: "Credentials",
    iconName: "shield",
    hint: "Reusable credential profiles for SMB / NFS / S3 hosts and shares.",
  },
  {
    to: "/settings/oauth",
    label: "OAuth",
    iconName: "box",
    hint: "OAuth client apps for Google Drive, OneDrive, and Dropbox.",
  },
  {
    to: "/settings/scanners",
    label: "Scanners",
    iconName: "database",
    hint: "Registered scanner agents, join tokens, and auto-discovery.",
  },
  {
    to: "/settings/schedules",
    label: "Schedules",
    iconName: "clock",
    hint: "Source scan cadences (cron) — one row per source.",
  },
  {
    to: "/settings/identities",
    label: "Identities",
    iconName: "user",
    hint: "Cross-source identity bindings used for ACL-aware search.",
  },
  {
    to: "/settings/tags",
    label: "Tags",
    iconName: "tag",
    hint: "Custom labels applied to entries for filter and search.",
  },
];

export default function Settings() {
  return (
    <Page
      title="Settings"
      description="Configure how Akashic indexes, scans, and surfaces your data."
      width="default"
    >
      {/* Tab strip — underline-style nav. Negative horizontal margin
          + matching padding pulls the bottom border edge-to-edge of
          the Page's content column so the underline reads as a clean
          baseline rather than an isolated card border. Same idiom
          GitHub / Vercel / Stripe use for top-level section nav. */}
      <div className="border-b border-line mb-6 -mx-6 px-6">
        <nav
          className="flex gap-1 -mb-px overflow-x-auto"
          aria-label="Settings sections"
        >
          {TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              title={tab.hint}
              className={({ isActive }) =>
                cn(
                  "inline-flex items-center gap-2 px-3 py-2.5",
                  "text-sm font-medium whitespace-nowrap",
                  "border-b-2 transition-colors duration-100",
                  "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:rounded-sm",
                  isActive
                    ? "border-accent-600 text-fg"
                    : "border-transparent text-fg-muted hover:text-fg hover:border-line",
                )
              }
            >
              <Icon name={tab.iconName} className="h-4 w-4" />
              {tab.label}
            </NavLink>
          ))}
        </nav>
      </div>

      <Outlet />
    </Page>
  );
}
