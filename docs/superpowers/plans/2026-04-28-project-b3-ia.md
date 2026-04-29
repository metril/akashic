# Phase B3 — IA pass: sectioned sidebar + Settings landing

> **For agentic workers:** Frontend-only changes. No new backend routes, no new tests required beyond a web build.

**Goal:** Group the current 7-item flat sidebar into four logical sections, add a Settings landing page that lists sub-areas as tiles, and make the user's mental model match the project structure.

**Out of scope:** Renaming pages, adding new ones (Tags / Schedules tiles point at TBD anchors as placeholders for B4 polish), reskinning section headers (visual round 2 = B4).

---

## Today's sidebar

```
[brand]
Dashboard
Browse
Search
Sources
Duplicates
Analytics
Settings           ← links to /settings/identities directly
Audit log          ← admin only
[Sign out]
```

## After B3

```
[brand]
OVERVIEW
  Dashboard
INDEX
  Browse
  Search
  Duplicates
  Analytics
SETUP
  Sources
  Settings           ← links to /settings landing
ADMIN  (admin only)
  Audit log
[Sign out]
```

Section headers are uppercase 11px-tracked-wider gray text (existing pattern from EntryDetail's `Section` header) — not clickable, no icon.

`/settings` becomes a tiled landing card-grid that links to:
- **Identities** (`/settings/identities`) — already exists.
- **Tags** (`/settings/tags`) — placeholder, route TBD; renders an "Empty" tile with "Coming soon" badge.
- **Schedules** (`/settings/schedules`) — same.

The two placeholder tiles render but their links are inert (no actual route). Not a bug — they're surfacing the IA shape for B4 to fill in.

---

## File structure

**Create**
- `web/src/pages/Settings.tsx` — landing tile-grid.

**Edit**
- `web/src/components/Layout.tsx` — replace the flat NavLink list with sectioned rendering.
- `web/src/App.tsx` — add `<Route path="settings" element={<Settings />} />` (the index route under `/settings`).

**No deletes.** The existing `/settings/identities` sub-route stays.

---

## Task 1 — Layout sections

**File:** `web/src/components/Layout.tsx`

Restructure `navItems` from a flat array to a `sections` array:

```tsx
interface NavSection {
  label: string;          // e.g., "Index"
  adminOnly?: boolean;    // hides the whole section for non-admins
  items: NavItem[];
}

const sections: NavSection[] = [
  { label: "Overview", items: [{ to: "/dashboard", label: "Dashboard", icon: … }] },
  { label: "Index",    items: [
    { to: "/browse",     label: "Browse",     icon: … },
    { to: "/search",     label: "Search",     icon: … },
    { to: "/duplicates", label: "Duplicates", icon: … },
    { to: "/analytics",  label: "Analytics",  icon: … },
  ]},
  { label: "Setup", items: [
    { to: "/sources",  label: "Sources",  icon: … },
    { to: "/settings", label: "Settings", icon: … },  // ← landing, not /settings/identities
  ]},
  { label: "Admin", adminOnly: true, items: [
    { to: "/admin/audit", label: "Audit log", icon: … },
  ]},
];
```

The `<nav>` body iterates sections, rendering each with a label and the items underneath:

```tsx
<nav className="flex-1 px-3 py-4 space-y-5">
  {sections
    .filter((s) => !s.adminOnly || isAdmin)
    .map((section) => (
      <div key={section.label}>
        <h3 className="px-3 mb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
          {section.label}
        </h3>
        <div className="space-y-0.5">
          {section.items.map((item) => (
            <NavLink … />
          ))}
        </div>
      </div>
    ))}
</nav>
```

`isAdmin` is already computed on line 85. The `space-y-1` between siblings becomes `space-y-5` between sections, with the items themselves at `space-y-0.5` for tight grouping.

The hardcoded admin Audit Log block (lines 118-134) is removed — it's now part of the Admin section.

---

## Task 2 — Settings landing page

**File (new):** `web/src/pages/Settings.tsx`

```tsx
import { Link } from "react-router-dom";
import { Card, CardHeader, Badge } from "../components/ui";

interface Tile {
  to: string | null;
  label: string;
  description: string;
  comingSoon?: boolean;
}

const tiles: Tile[] = [
  {
    to: "/settings/identities",
    label: "Identities",
    description: "Cross-source identity sets and per-source bindings.",
  },
  {
    to: null,
    label: "Tags",
    description: "Custom labels applied to entries for filter and search.",
    comingSoon: true,
  },
  {
    to: null,
    label: "Schedules",
    description: "Source scan cadences and one-off triggers.",
    comingSoon: true,
  },
];

export default function Settings() {
  return (
    <div className="px-8 py-7 max-w-5xl">
      <div className="mb-7">
        <h1 className="text-2xl font-semibold text-gray-900 tracking-tight">
          Settings
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Configure how akashic behaves across sources.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {tiles.map((tile) =>
          tile.to ? (
            <Link key={tile.label} to={tile.to} className="block">
              <Card padding="md" className="h-full hover:shadow-md transition-shadow">
                <CardHeader title={tile.label} description={tile.description} />
              </Card>
            </Link>
          ) : (
            <Card key={tile.label} padding="md" className="h-full opacity-60 cursor-not-allowed">
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-base font-semibold text-gray-900">{tile.label}</h3>
                <Badge variant="neutral">Coming soon</Badge>
              </div>
              <p className="text-sm text-gray-500">{tile.description}</p>
            </Card>
          ),
        )}
      </div>
    </div>
  );
}
```

---

## Task 3 — Wire route

**File:** `web/src/App.tsx`

Insert `<Route path="settings" element={<Settings />} />` immediately above the existing `<Route path="settings/identities" …>` so the index route works.

---

## Verification

1. `cd web && npm run build` — type-check + bundle pass.
2. Open `/dashboard` → sidebar shows four sections (Overview / Index / Setup / Admin if admin).
3. Click **Settings** → lands on `/settings` with three tiles, two marked "Coming soon".
4. Click **Identities** tile → goes to `/settings/identities` (existing page).
5. Active-link highlight still works on each section's items.
6. Admin section visible only when `me.role === "admin"`.

---

## Out of scope

- Tags and Schedules pages — placeholder tiles only.
- Breadcrumb section prefix from the spec doc — punt to B4 polish; the existing breadcrumbs read fine without it.
- New icons — keep current SVG path strings.
