# Phase B4 — Visual Round 2: loading + empty + error consistency

> **For agentic workers:** Frontend-only. The scope is intentionally tight — fix concrete inconsistencies that exist today, don't refactor everything.

**Goal:** Make loading states, empty states, and error messages render consistently across pages. Most pages already use the `Spinner` / `Skeleton` / `EmptyState` primitives correctly; two pages (`AdminAudit` and `SettingsIdentities`) ship inline ad-hoc treatments that should match the rest.

**Out of scope (deliberate):**
- Color palette overhaul (chip/badge variants are already in place; reskinning is a separate effort).
- Icon library swap (each page has inline SVG path strings; promoting them to a shared icon component is a meaningful refactor and not pulling its weight in B4).
- Spacing audit beyond the specific inconsistencies caught in the survey.

---

## Survey findings (today's state)

**Pages using primitives consistently:**
- `Dashboard.tsx` — `EmptyState` for no-data, StatCard `loading` prop for stat cards.
- `Browse.tsx` — `Spinner` for loading, `EmptyState` for empty/error.
- `Search.tsx` — same.
- `Sources.tsx` — `Skeleton` blocks for loading, `EmptyState` for empty.
- `Duplicates.tsx` — `Spinner` + `EmptyState`.
- `Analytics.tsx` — `Skeleton` charts, `EmptyState`.

**Pages with ad-hoc treatments:**
- `AdminAudit.tsx`:
  - `<div className="text-sm text-gray-400">Loading…</div>` — should be `Spinner` or `Skeleton`.
  - Empty state is an inline `<tr><td colSpan={5}>No events.</td></tr>` — should use `EmptyState`.
  - Error uses `bg-red-50 text-red-700` — other pages use `bg-rose-50 text-rose-600`.
- `SettingsIdentities.tsx`:
  - `<div className="text-sm text-gray-400">Loading…</div>` — same inconsistency.
  - No empty state when there are zero identities — silent.

---

## Tasks

### Task 1 — AdminAudit loading + empty + error consistency

**File:** `web/src/pages/AdminAudit.tsx`

- Replace the inline `Loading…` div with a `Spinner` placed where the table would be (not above the table). When loading, hide the table entirely and show centered `Spinner`.
- Replace the inline `<tr>` empty state with `<EmptyState title="No events" description="Audit events will appear here as users act." />`. Render outside the `<table>` when `items.length === 0 && !audit.isLoading`.
- Switch error styling to `bg-rose-50 text-rose-600` to match other pages.

### Task 2 — SettingsIdentities loading + empty state

**File:** `web/src/pages/SettingsIdentities.tsx`

- Replace inline Loading div with `Spinner`.
- Add `EmptyState` when `personsQ.data` exists but is empty: "No identities yet. Add one below to filter search by what you can read."

### Task 3 — Card hover/transition unification

**File:** `web/src/pages/Settings.tsx` (B3 just shipped this) plus `Sources.tsx` source cards.

The Settings landing page tiles use `hover:shadow-md transition-shadow` on hover. The source cards on `Sources.tsx` don't use any hover affordance — they're effectively static cards even though the user clicks Scan/Delete inside them. Adding hover treatment to source cards is fine, but given those cards have multiple action buttons inside, a hover shadow on the whole card would conflict visually. Skip this — it's a meaningful design call, not a polish fix.

Net result: Task 3 is a no-op after consideration; Task 1 + Task 2 are the real diff.

---

## Verification

1. `cd web && npm run build` clean.
2. Visit `/admin/audit` with no events — see EmptyState card, not blank table.
3. Visit `/admin/audit` while loading — see Spinner, not text.
4. Trigger a 5xx on the audit endpoint — see rose-styled error matching other pages.
5. Visit `/settings/identities` with no identities — see EmptyState.
6. Same page while loading — see Spinner.

---

## Out of scope (tracked but not addressed in B4)

- Inline SVG path strings → shared Icon component. Each page declares its own `<Icon>` helper that takes a `d` path. Promoting to a shared component would touch ~10 files and introduce icon-name registry. Defer to a dedicated cleanup PR.
- Tags / Schedules placeholder pages from B3. Those need their own backend.
- Bundle size: `index-Cw9atQ6K.js: 691.57 kB` is over Vite's 500 KB warning threshold. Code-splitting via `React.lazy` for the per-page routes would shed ~200 KB from the initial bundle. Worth doing but separate effort.
