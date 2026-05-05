# Changelog

User-visible changes by release. Format follows
[keep-a-changelog](https://keepachangelog.com/) — the first
bullet under each version is the *why*, not the implementation
detail.

## v0.5.12 — 2026-05-05

- **Cmd+K file results no longer break.** Picking a file from the
  command palette navigated to `/browse?path=<file path>`, which
  Browse interpreted as a *directory* path — the api 404'd or
  returned an empty listing, leaving the user staring at "no
  entries". The palette now opens the file detail drawer (via the
  global `useEntryDetail` context that's already wired through
  Layout) overlaid on whatever page the user was on, matching how
  Search results behave.
- **Button affordance audit, round 3.** A fresh sweep with the
  Explore agent found 18 buttons styled as plain text or thin links
  for actions that deserve real button chrome. Promoted to the
  standard `Button` component:
  - **High severity** (deletions / mutations / major navigation):
    Settings → Identities → Delete identity / Resolve / Remove
    binding; Duplicates → Select all but keeper; Storage Explorer
    → Open Sources empty-state CTA; Storage Explorer → Up button
    (now matches Browse's Up).
  - **Medium severity** (selection toolbars in
    [Browse](web/src/pages/Browse.tsx) and
    [Search](web/src/pages/Search.tsx)): "Select all visible",
    "Deselect all visible", "Clear" → ghost-Button variants;
    Browse's "Show all" admin reveal → secondary Button.
  - **Cross-page navigation**: FilterChips' "Switch to Search"
    link, HoverSidebar's "Open in Browse" / "Filter Search to here"
    links → button-styled Links (same chrome as
    `Button variant="secondary"`); ScanLogPanel's "Stop scan" /
    "Resume tail" → Button danger / ghost; EntryTags' inline
    apply / cancel → Button.
  - **Skipped**: tiny disclosure toggles (`▾ Search as…`,
    AdminAudit row chevron) and Dashboard's "Manage →" / "Open →"
    list-rows where the full-row hover background already reads as
    a button.

## v0.5.11 — 2026-05-05

### Search modes + palette → Search transfer

- **Cmd+K palette query now transfers to /search.** Pre-fix, clicking
  "Show all results" navigated to `/search?q=foo` but
  [Search.tsx](web/src/pages/Search.tsx) ignored the URL param — the
  input arrived empty, the user re-typed, and the result set differed
  because the new query wasn't identical to the palette's top-8.
  [Search.tsx](web/src/pages/Search.tsx) now reads `?q=` and `?mode=`
  via `useSearchParams` (same pattern as
  [useFilterUrlState.ts](web/src/hooks/useFilterUrlState.ts) for
  `?filters=`), and writes them back on every keystroke (debounced)
  so refresh / back-button / copy-paste all preserve state.
- **`Cmd+Enter` shortcut** in the command palette ([CommandPalette.tsx](web/src/components/CommandPalette.tsx))
  jumps straight to `/search?q=<current input>` and closes the palette.
  Linear / Raycast pattern — escape from the preview surface to the
  full search experience without clicking the footer row. The footer
  row now reads `↵ open · ⌘↵ see all in Search · Fuzzy preview · Search page supports glob/regex`.
- **Glob and regex search modes.** New `mode` query param on
  `GET /api/search` accepts `fuzzy` (default, unchanged), `glob`, or
  `regex`. Glob translates `*`/`**`/`?` to SQL `LIKE` patterns
  ([services/search.glob_to_sql_like](api/akashic/services/search.py))
  and matches against `entries.path` when the pattern contains `/`,
  otherwise against `entries.name`. Regex validates with `re.compile`
  up-front (HTTP 400 on syntax error with parser position) and applies
  postgres POSIX `~` on `entries.path`. Both modes force the SQL path
  because Meilisearch can't express exact pattern matching. The
  Search page surfaces a `[ Fuzzy | Glob | Regex ]` segment toggle
  with a per-mode hint.

### Walker observability

- **Inaccessible-counts surfaced per scan.** Pre-fix, the Go walker
  silently swallowed `os.ReadDir` permission errors, mid-scan ENOENT
  on dir entries, and metadata read failures — scans completed
  "successfully" with subtrees missing and no record of the skip.
  [walker.WalkStats](scanner/internal/walker/walker.go) now tracks
  `InaccessibleDirs` / `InaccessibleFiles`; the connector interface
  returns them; [scanner.Result](scanner/internal/scanner/scanner.go)
  ships them in the `IsFinal=true` batch envelope. The api accumulates
  on the `scans` row across batches (so the parallel-agent path with
  one final-per-unit sums correctly across units). SourceDetail's
  "Last scanned" row now shows e.g. `3 inaccessible items skipped (1 dir, 2 files)`
  in amber when the most recent completed scan touched anything it
  couldn't enter. Migration `0027_scan_inaccessible` adds the columns
  with default 0 — legacy scans / legacy scanners read clean zero.

### UI consolidation

- **Modal scrim consolidation.** [ModalShell](web/src/components/ui/ModalShell.tsx),
  [Drawer](web/src/components/ui/Drawer.tsx), and
  [CommandPalette](web/src/components/CommandPalette.tsx) used to each
  hard-code their own `bg-gray-900/55` (or `/45` on Drawer) overlay.
  Replaced with a single `<Scrim />` primitive
  ([components/ui/Scrim.tsx](web/src/components/ui/Scrim.tsx)) backed
  by a `bg-scrim` token in tailwind.config.js. Edit one token to
  retheme every overlay.
- **Treemap palette extracted.** The 10-color treemap palette + age
  / risk semantic colors lived inline in three files
  ([Treemap.tsx](web/src/components/storage/Treemap.tsx),
  [sunburstLayout.ts](web/src/components/storage/sunburstLayout.ts),
  [branchAccent.ts](web/src/components/storage/branchAccent.ts)).
  Now centralized in
  [categoryPalette.ts](web/src/components/storage/categoryPalette.ts)
  and mirrored in tailwind.config.js as
  `colors.category.{1..10}` / `colors.heat.*` / `colors.risk.*` so
  HTML chrome can use class names while the WebGL canvas consumes
  the same hex strings.
- **Typography scale tokens.** Tailwind's `theme.extend.fontSize`
  gains semantic composite tokens: `text-meta`, `text-label`,
  `text-body`, `text-body-strong`, `text-h4`, `text-h3`, `text-h2`,
  `text-h1`. Sweep replaces the most common
  `text-[11px] uppercase tracking-wider text-fg-subtle` combo with
  `text-meta uppercase text-fg-subtle` across CommandPalette, audit
  pages, and BucketSecurityCard. Existing Tailwind size classes keep
  working — adoption migrates incrementally.
- **Accent token sweep.** Form chrome that used `text-blue-600 focus:ring-blue-400`
  inline (checkboxes, focus rings, link styles) on AddSourceForm,
  AllowedScannersPanel, AllowedSourcesModal, DiscoverSharesPanel,
  HostAllowedScannersPanel, HostDetail, HostHeader, Hosts, SourceDetail
  now reference `text-accent-600 focus:ring-accent-400`. Semantic
  blue stays where it carries meaning (scanning progress, ACL diff
  colors, audit event badges, modal "selected" highlights, the
  scanner spinner).

### UI/UX audit polish (round 2)

- **AdminAudit dark mode + a11y.** Error box gained dark variants;
  filter form refactored from raw `<select>`/`<input>` to the
  standard `Select` + `Input` components; pagination buttons gained
  `aria-label`s and a "Page X of Y" indicator with `aria-live`;
  expand-row toggle gained `aria-label`/`aria-expanded` and a
  scroll-clipping `max-h-96` on the JSON payload pane.
- **Search.tsx error styling** uses the styled rose-on-rose card
  pattern with dark variants and an inline regex-syntax hint when
  `mode=regex` and the request errored.
- **Browse.tsx mobile toolbar** moved from `flex-col md:flex-row`
  (which compressed the Source dropdown awkwardly at 600-960px) to
  a 3-column responsive grid with the breadcrumb spanning two cols
  at `sm`. The empty-state when ACL filtering hides matches now
  appends `(N items hidden by your access permissions — your filter may match one of them.)`.
- **SettingsCredentials edit modal** — the create / edit forms gained
  a `Settings → Credentials` breadcrumb above the title. Error path
  uses the same dark-mode rose card.
- **Toast voice sweep** — verb-first, sentence case, trailing
  period, subject-when-relevant remediation hints. Updated in
  HostDetail, SourceDetail, AllowedScannersPanel, AllowedSourcesModal,
  HostAllowedScannersPanel, DiscoverSharesPanel, SettingsCredentials.
  Examples: `Save failed: …` → `Couldn't save host: …`;
  `"Source updated."` → `Saved "MyShare".`; `Apply failed` →
  `Couldn't apply scanner changes`.
- **Latent search router bug fixed.** Pre-fix, `_ForceSqlFallback`
  was raised *outside* the try/except in
  [routers/search.py](api/akashic/routers/search.py), so any query
  with a `path:` predicate (or now non-fuzzy mode) would have 500'd
  rather than falling through to SQL. The path-predicate path was
  unexercised by tests, so this hadn't surfaced. Restructured to
  raise inside the try block where the fallback handler can catch.

## v0.5.10 — 2026-05-04

- **Search returned 500 (or appeared empty) when any orphaned doc was
  in the result set.** [SearchHit.source_id](api/akashic/schemas/search.py)
  was non-nullable, but Meilisearch can hold docs with
  `source_id=NULL` (left over after a source delete with
  `purge_entries=False` — the entry rows survive in postgres for
  later recovery, the Meili docs are updated to null). The Pydantic
  validation crashed the response. Fixed two ways: schema is now
  lenient (nullable), and the search handler explicitly filters
  `source_id IS NOT NULL` in both the Meili and SQL fallback paths so
  unreachable orphans stay out of search until they're reattached.
- **Inline credential fields no longer show when a profile is
  selected.** AddHostForm renders the ProfilePicker first; if the
  user picks a saved profile, the username/password/key inputs in
  HostFields disappear (host-shape fields like host/port/known_hosts
  stay). `validateHostConfig` gained an `omitCredentials` flag so
  the form doesn't fail validation for fields it deliberately hid.
- **Source credentials editable post-create.** SourceDetail's edit
  form now mounts the ProfilePicker so a credential profile can be
  attached, swapped, or detached without deleting and recreating the
  source. PATCH `/api/sources/{id}` already accepted
  `credential_profile_id`; this surfaces it in the UI.

## v0.5.9 — 2026-05-04

- **Credential profiles.** New first-class entity: a named, type-discriminated
  bundle of credential fields any number of hosts and shares can reference.
  Define an SSH key, SMB password, or S3 access key once in
  Settings → Credentials and attach it to many hosts/sources without
  copy-pasting secrets. Inline values keep working as overrides — the layered
  resolver in [services/source_config.merge_host_and_source](api/akashic/services/source_config.py)
  applies layers in order, last write wins:

      host_profile < host_inline < source_profile < source_inline.

  Most-specific wins: a `username` typed onto a share overrides everything;
  a profile is the default. Schema: new `credential_profiles` table +
  nullable `credential_profile_id` FKs on `hosts` and `sources`
  (alembic 0026). Endpoints under `/api/credential-profiles`, with
  reference-protected delete (409 if any host or share still references
  the profile).
- **"Last Scanned" pill replaces the "Online" badge.** The source card
  status badge now reads "Last scanned 2h ago" for idle sources (with an
  absolute-timestamp tooltip), "Never scanned" for sources that haven't
  run yet, and the existing `Scanning` / `Queued` / `Failed` for active
  states. Drops the redundant "Last scan" row from the card body — the
  pill carries it. The legacy `online`/`offline` source.status values
  still exist server-side; they just no longer surface as confusing user
  copy.
- **UI/UX audit fixes.** A pass driven by a three-agent UX review:
  - Button affordance — `Rotate keys` / `Disable` on `/settings/scanners`
    promoted from `ghost` to `secondary` so they read as bordered chips,
    not background. Bare-text "View live log" / "Stop scan" links on
    SourceCard promoted to real Buttons. The `/sources` group-by toggle
    moved off hard-coded `bg-blue-600` to the accent token + proper
    `aria-pressed` for keyboard a11y.
  - Dark mode coverage — `Skeleton` no longer paints a light-gray block
    on dark; SourceDetail tab border uses a semantic token; Dashboard
    extension-growth and slope colors plus Hosts card hover gain dark
    variants.
  - Modal a11y — `KeyIssuedModal` and `RotateConfirm` now use the
    shared `ModalShell` / `ConfirmDialog` primitives, gaining
    `role="dialog"`, `aria-labelledby`, ESC handling, and click-outside
    in one consistent shell.
  - Form clarity — `Input` renders an asterisk after labels for
    `required` fields; pre-filled credential fields show an explicit
    "Existing value preserved" helper; `text-fg-subtle` hint contrast
    bumped to `text-fg-muted` for WCAG AA.
  - Search trigger — TopBar's "Search files…" trigger restyled as a
    proper button (no longer a fake input) and relabelled to "Search…"
    so the click→modal flow stops being a surprise. The CommandPalette
    Files group now always renders explicit empty / loading / error
    states (instead of silently disappearing on 0 hits) and offers a
    "Show all results in Search →" escape link.
  - Copy — ReachabilityBadge's two yellow "Stale" states disambiguated:
    "Stale (was reachable)" vs "Stale (no recent probe)". Source-create
    label is now "Source name" with a usage hint.
- **Known follow-ups.** Treemap palette extraction to tailwind tokens,
  named typography scale, modal scrim consolidation, settings child-page
  breadcrumbs, Browse mobile toolbar, ManualKeyForm mobile grid, and a
  full toast-voice sweep are tracked for v0.5.10.

## v0.5.8 — 2026-05-04

- **Host eligibility panel no longer 500s.** v0.5.7's
  `GET /api/hosts/{id}/scanner-reachability-summary` had an off-by-one
  (`r[8]` on a 0-7 result tuple) that raised `IndexError` on every
  call, surfacing as "Failed to load scanner summary" on HostDetail.
  Fixed and covered by a regression test that exercises both empty-
  attachment and probed shapes.
- **Scanner row alignment.** On `/settings/scanners` the badge row
  used `flex-wrap`, so conditional `disabled` / `types` badges and
  variable name lengths shifted the *sources: N* badge horizontally
  row-to-row. The top row now uses a flat layout with *sources: N*
  pinned to the right via `ml-auto`; `disabled` and `types: …` move
  to the muted second metadata line.
- **Online vs Reachable copy.** Adds an inline legend above the
  scanner list and subtitle lines on each of the three eligibility
  panels (per-source, per-scanner, per-host) explaining that
  *Online* tracks scanner agent liveness (90s heartbeat) while
  *Reachable* tracks per-(scanner, source) probe outcomes — they're
  orthogonal. Tooltip on the online dot in ScannerRow restates the
  90-second window.

## v0.5.7 — 2026-05-04

- **Scanner agent runs reachability probes.** v0.5.6 shipped the api-
  side enqueue + self-worker; this release ships the matching agent
  loop. `internal/agent/reachability.go` polls
  `/api/scanners/{id}/reachability/poll` every 15s (jittered),
  runs an in-process probe via the new `internal/probe` package,
  and reports back via `/reachability/{id}/report`. Independent of
  the scan-lease cadence so a long scan doesn't starve reachability
  data. Local-source probes now work out of the box for any host
  with a scanner installed — the api self-worker can't see remote
  paths but the agent that lives next to the data can.
- **Bidirectional eligibility-management UI.** Three views, one
  underlying field (`Scanner.allowed_source_ids`):
  - **Source view:** new "Allowed scanners" panel on SourceDetail.
    Multi-select checklist of every scanner with each scanner's
    most recent probe outcome against THIS source inline (green
    "Reaches", yellow "Stale", red "Cannot reach", grey "Not yet
    probed"). "Auto-fill recommended" pre-checks every 🟢 row;
    saving a 🔴 row prompts a confirm because allowing a proven-
    unable scanner just queues failures.
  - **Scanner view:** SettingsScanners' "sources: N" badge is now
    clickable — opens an `AllowedSourcesModal` with the inverse
    listing (every source × this scanner's probe outcome). Saves
    via `PATCH /api/scanners/{id}` (existing field, no schema
    change).
  - **Host view:** new "Allowed scanners" section on HostDetail
    aggregates per scanner across attached shares ("Reaches 3 of
    5"). "Apply to N attached sources" bulk-writes the selection
    to every share in one transaction.
- **Backend:** new endpoints
  `GET /api/sources/{id}/scanner-reachability`,
  `PATCH /api/sources/{id}/allowed-scanners`,
  `GET /api/hosts/{id}/scanner-reachability-summary`,
  `PATCH /api/hosts/{id}/allowed-scanners`. All idempotent. Audit
  events: `source_allowed_scanners_updated`,
  `host_allowed_scanners_applied`. The host-side endpoint is the
  bulk-fan-out path: it loops across attached sources and applies
  the same diff in a single transaction.
- **Probe extraction:** test_connection logic moved to a reusable
  `scanner/internal/probe/probe.go` package so the agent can probe
  in-process without a subprocess. The CLI subcommand
  `akashic-scanner test-connection` keeps the same step:reason
  contract for the api's pre-flight test path.

## v0.5.6 — 2026-05-04

- **Reachability is now continuous.** Previously the api only knew if
  a source was reachable when a scan completed or a user manually
  clicked *Check now*, so a NAS that dropped at 9am stayed
  "Reachable" in the UI until the next scan. v0.5.6 introduces
  reachability **work items**: a `reachability_checks` table mirrors
  the scan-work-unit pattern, the scheduler enqueues one row per
  source whose last check is older than 5 minutes (configurable via
  `REACHABILITY_CHECK_INTERVAL_SECONDS`), and either an api self-
  worker or a scanner agent claims it via SELECT ... FOR UPDATE SKIP
  LOCKED, runs the same `test-connection` probe the *Check now*
  button uses, and reports back. Sources roll up into the parent
  Host's reachability via a new helper, so the Hosts page now shows
  a green/yellow/red dot per host without anyone having to click.
- **Reachability badge for every source.** The badge used to only
  render for `is_removable=true` sources, hiding probe data the api
  was already collecting on every scan completion. Now it renders
  for every source with five distinct states: Reachable (green),
  Stale (yellow — was reachable but no probe in 10 min), Unreachable
  (red), Not yet checked (grey), and Stale (yellow, "no scanner
  reported" — the misconfigured-pool case after the staleness
  window). A new shared `ReachabilityDot` component drives the same
  dot on the Hosts page.
- **Bulk scan triggers.** `/sources` gains a *Scan all* button next
  to the Group-by toggle that fires an incremental scan for every
  visible source via the existing dedup-aware `/scans/trigger`
  endpoint (concurrency capped at 8 in-flight requests). Per-host:
  HostDetail's drawer gains a *Scan all attached* button alongside
  *Discover shares*. Both confirm via `ConfirmDialog` ("Trigger
  scans for N sources?") and surface the result with a toast that
  splits "triggered / already running / failed" counts. A new
  `created: bool` field on the `/scans/trigger` response lets the
  bulk hook distinguish fresh inserts from dedup hits without
  timestamp-fuzzing.
- **Black "Reachable" toast fixed.** Sonner's `<Toaster>` was missing
  `richColors`, so success/error/warning toasts inherited a neutral
  palette that rendered as dark text on a dark background in dark
  mode. Adding `richColors` paints success green, error rose,
  warning amber, info blue.
- **Probe-as-eligibility for `type=local`.** A scanner is now
  excluded from claiming a `type=local` scan when it has a recent
  failed reachability probe against that source — so a wrongly-
  pooled scanner that can't reach the path no longer produces silent
  zero-files "ghost success" scans. Successful probes are evidence
  of reachability; failed probes are evidence of inability; the
  absence of a probe leaves the door open as before. The pool /
  `allowed_source_ids` filters remain as optional pinning
  primitives.
- **Backend:** new `Host.is_reachable / last_reachable_at /
  last_reachability_check_at` columns; `POST
  /api/hosts/{id}/test-connection` now persists those instead of
  discarding the result. Two new scanner endpoints:
  `POST /api/scanners/{id}/reachability/poll` (atomic claim) and
  `POST /api/scanners/{id}/reachability/{check_id}/report` (commit a
  probe result). `GET /api/scanners/{id}/source-reachability`
  returns each source's latest probe state for the upcoming
  scanner-side eligibility modal. Three new settings:
  `REACHABILITY_CHECK_ENABLED` (default true),
  `REACHABILITY_CHECK_INTERVAL_SECONDS` (300),
  `REACHABILITY_CHECK_MAX_CONCURRENCY` (4).
- **Deferred to v0.5.7:** the Go scanner agent's reachability poll
  loop (the api self-worker covers all non-local sources today; type=
  local sources without an api-reachable bind-mount keep the existing
  post-scan reachability path until the agent ships) and the
  bidirectional eligibility-management UI (per-source / per-scanner /
  per-host scanner-allow-list editors). Both are scoped and ready in
  the plan; held back to keep this release reviewable.

## v0.5.5 — 2026-05-04

- **UI:** retire native browser `confirm()` popups. Deleting a host,
  scanner, or tag now opens an in-app `ConfirmDialog` styled to
  match the rest of the app (dim scrim, focus on the destructive
  button, ESC to cancel, busy state during the mutation). The
  popup-blocked, dark-mode-unaware browser dialog is gone.
- **UI:** fix low-contrast text on selected radio options in the
  source-delete and recover-orphans modals. The blue / rose-50
  highlight kept the description in `text-fg-muted`, which washed
  out to ~3:1 on the bright tint. Selected rows now switch to a
  high-contrast colour pair (`text-blue-900` / `text-rose-900`,
  with dark-mode equivalents) that clears WCAG AA. Audit-event
  badges (`source_created`, `source_updated`, `source_deleted`)
  also bumped from -100/-800 to -200/-900 for the same reason.
- **UI:** discover-shares panel polish. The default Source name is
  now the bare share name instead of `${host.name}/${share}` — the
  host header on the Sources page already labels the parent, so
  the prefix was redundant. The *Select all* checkbox correctly
  shows an indeterminate state when only some rows are checked.
  The Add button moves to a sticky footer with a one-line note
  about source-name uniqueness. Empty-state copy is cause-aware
  per host type.
- **UI:** group-by toggle on `/sources` no longer collapses cards on
  top of each other when switched to *None*. The TanStack
  virtualizer was caching measured row sizes by index — toggling
  the grouping reshuffled the rows array and the cached
  header-height (36px) leaked into card slots. Virtualizer now
  tracks measurements by stable row key (`card:<id>` /
  `header:<id>`) so toggling preserves per-row identity.
- **UI:** all modals now share a tiny `ModalShell` primitive
  (overlay, centered card, ESC handling). `ConfirmDialog`,
  `DeleteSourceModal`, and `RecoverOrphansModal` use the same
  shell — open/close behaviour and focus handling stay
  consistent everywhere.

## v0.5.4 — 2026-05-04

- **Hosts:** discover-and-batch-add. A new *Discover shares* button on
  every SMB / NFS / S3 host in `/hosts` enumerates the shares the
  saved credentials can see (SMB `NetShareEnumAll` over the IPC$
  srvsvc pipe; NFS `MOUNT3 EXPORT`; S3 `ListBuckets`) and presents
  them as a checkbox list — pick N, hit *Add N sources*, and the
  matching `Source` rows are created in one transaction. Already-
  attached shares render checked + disabled with an "(already
  added)" tag so the user sees the delta. `AddSourceForm` gains a
  one-line affordance "Or discover all shares on this host →" that
  deep-links to the Hosts page with the discovery panel pre-expanded.
  Local and SSH hosts skip discovery (no shares concept) and keep
  the existing single-share form.
- **Sources:** the page now groups cards by host. With 12 shares
  across 3 hosts you see 3 collapsible host headers each carrying
  their share count, a link to the host page, and a chevron that
  collapses the section. Collapsed state is persisted per-host in
  localStorage. A small *Group by: Host / None* toggle in the page
  header lets you fall back to the flat list. Default is *Host*
  whenever any source has a host_id.
- **Backend:** new `akashic-scanner list-shares` subcommand (mirrors
  `test-connection`'s shape — `--password-stdin` JSON creds, JSON
  stdout, `step:reason` stderr); new `POST /api/hosts/{id}/list-shares`
  and `POST /api/hosts/{id}/add-shares` endpoints (idempotent on
  Source.name, returns created/skipped counts).

## v0.5.3 — 2026-05-04

- **Scanners (Phase 2 — full coverage):** parallel scanning is now
  end-to-end working for **every** source type — local, nfs, ssh,
  smb, and s3. The previous v0.5.2 release shipped the agent-side
  unit-coordinated path but only wired it up for local + nfs;
  remote connectors fell back to the legacy single-walker. This
  release adds `WalkShallow` to every connector via a new optional
  `connector.ShallowWalker` interface:
  - SSH lists immediate children via SFTP `ReadDir` with per-dir
    ACL prefetch (full-tree dump skipped — defeats shallow).
  - SMB uses `share.ReadDir` with hashing + security-descriptor
    capture preserved per-file.
  - S3 uses `ListObjectsV2` with `Delimiter="/"` so the response
    cleanly separates `CommonPrefixes` (subdirs) from `Contents`
    (files at the current level).
- **Agent:** the unit runner now type-asserts `ShallowWalker` rather
  than gating on connector type, and the connection is opened once
  for the whole unit-loop lifetime instead of being re-established
  per unit (saves the SSH/SMB auth handshake on every claim).
- **Tests:** compile-time assertion that all five shipped connectors
  implement `ShallowWalker` so a future regression can't drop one
  out of the parallel path silently.

## v0.5.2 — 2026-05-04

- **Scanners (Phase 2):** parallel scanning is now end-to-end working
  for `local` and `nfs` sources. When `max_parallel_scanners > 1`, the
  Go agent enters a unit-coordinated mode: the first scanner to lease
  the scan enumerates the source root, splits each top-level
  subdirectory off as a `scan_work_unit`, then re-enters the lease
  loop. Sibling scanners in the same pool claim units concurrently
  via the SKIP-LOCKED primitive, walk their subtree with the existing
  scanner.Run path, and the API auto-finalises the scan once the last
  unit completes. Per-unit heartbeats keep leases fresh; expired
  leases are reclaimable by any sibling. The legacy single-walker
  path is unchanged for `max_parallel_scanners=1` and for ssh/smb/s3
  sources (which fall back with a one-line log warning until those
  connectors learn to expose immediate-children listings).
- **Walker:** new `walker.WalkShallow` — emits files at the root
  level, returns subdirectory names instead of recursing. Used by the
  unit-coordinated agent's "" root-files unit and tested end to end.
- **API:** the `/api/scans/lease` response now carries
  `source.max_parallel_scanners` so the agent can pick the right path
  without an extra lookup.

## v0.5.1 — 2026-05-04

- **Scanners (Phase 1):** data model + API for parallel scanning. A
  scan can now be split into many `scan_work_units` rows (one per
  directory subtree) that cooperating scanners lease independently
  via the same `SELECT FOR UPDATE SKIP LOCKED` primitive used for
  scan-level leasing. New endpoints under `/api/scans/{id}/work/`:
  `lease`, `heartbeat`, `complete`, `fail`, `split`. Sources gain a
  `max_parallel_scanners` setting (1–16, default 1) that caps the
  distinct-scanner count per scan; the AddSource form and the source
  detail's edit pane both expose it. The scan-level
  `/api/scans/{id}/complete` contract is unchanged for backward
  compatibility — when the last work unit terminates, the API
  transitions the scan and fires the same side-effects (source
  status, last_scan_at, is_reachable, broadcast). Phase 2 — the Go
  scanner refactor that actually populates work units via a
  walker-side split heuristic — ships in a follow-up release; today
  the table is empty for all scans and behaviour is identical.

## v0.5.0 — 2026-05-04

- **Hosts:** new `Host` model — a reusable connection target that owns
  the connection-level config (hostname, port, credentials, key
  material). `Source` rows attach via `host_id` and carry only the
  share-shaped fields (`path` / `share` / `export_path` / `bucket`).
  Adds the `/hosts` page (list + add + edit + test) and a host picker
  on the Add-source form. Add many shares to one host without
  re-entering credentials; rotate a password once and every attached
  share picks it up. Existing non-local sources backfill 1:1 to a
  host of the same name via Alembic migration `0023_hosts`; the user
  can later merge duplicates by attaching their shares to one host
  and deleting the others. Local sources are unaffected (no host).



- **UI:** the source-detail action row was visually inconsistent —
  *Recover orphans…* and the new (v0.4.21) *Check now* button rendered
  as borderless ghost buttons, while the rest of the row (*Edit*,
  *Scan now*, *Delete*) used the bordered `secondary` / `danger`
  styles. Both now use `secondary` so the row reads as a single
  group.

## v0.4.21 — 2026-05-03

- **Sources:** external-drive awareness. Sources can now be flagged
  *Intermittently available* (USB drive, intermittent SSH/SMB/NFS
  mount). The Sources page surfaces a separate **Reachable / Unmounted
  / Not yet checked** indicator for these sources, with a **Check now**
  button that runs the existing test-connection probe against the
  persisted credentials and updates `is_reachable` +
  `last_reachable_at` + `last_reachability_check_at`. **Scan now** on a
  known-unmounted removable source no longer queues a doomed scan —
  the user is told to Check now (or reconnect) first. Successful scans
  also bump `last_reachable_at` so the badge stays fresh without an
  explicit click. New columns ship via Alembic migration
  `0022_source_reachability`; on source create, `is_removable` is
  inferred from the path (`/media/`, `/mnt/`, `/run/media/`,
  `/Volumes/` → true) when the user doesn't set it explicitly.
- **Analytics:** chart tooltips no longer lag on hover. Each chart's
  `<Tooltip>` props (`contentStyle`, `formatter`, `cursor`) used to be
  recreated inline on every render; the `["analytics"]` query
  `staleTime` was 1 min, so a refetch could fire mid-hover and
  re-render the whole page. Charts (`ChartCard`, `GrowthChart`,
  `ForecastChart`, `ExtensionTrendChart`) are now `React.memo`'d, the
  tooltip styling is hoisted into a shared `useChartTooltipStyle`
  helper that memoises on the chart-colors result, derived data
  arrays go through `useMemo`, and the analytics `staleTime` is bumped
  to 5 min (analytics aggregates don't change second-by-second).

## v0.4.20 — 2026-05-03

- **CI:** the v0.4.19 web Docker image build failed with
  `Cannot find module @rollup/rollup-linux-x64-musl` (npm bug
  [#4828](https://github.com/npm/cli/issues/4828)). Regenerated
  `web/package-lock.json` under `node:20-alpine` so the optional
  rollup binaries for both glibc and musl are tracked.

## v0.4.19 — 2026-05-03

- **Ingest:** dedup is now bulk — one `SELECT entries WHERE source_id=? AND path IN (...)`
  per batch instead of one per entry. Removes the dominant cost on
  10M+-file scans; p50 ingest latency on a 1k-batch should drop
  ~5–10×.
- **Ingest:** Meilisearch indexer fetches all touched entries in one
  SELECT instead of N. 2–5× speedup on the post-batch indexing task.
- **Ingest:** background tasks (Meili index, subtree rollup, snapshot
  writer, webhook dispatch) now reuse a process-cached engine per
  database URL instead of building + disposing one each. Fewer
  spurious connections during sustained high-load scans.
- **Schema:** new partial composite index
  `ix_entries_active_content_hash(source_id, content_hash) WHERE is_deleted=false AND kind='file' AND content_hash IS NOT NULL`
  serves the end-of-batch move-detection lookup without scanning the
  much larger historical/deleted set.
- **Scanner:** the walker now honours `context.Context` cancellation —
  a SIGTERM or scan-cancel during a 10M-file walk returns at the next
  directory boundary instead of running to completion. SSH, SMB, and S3
  walk loops also poll `ctx.Err()` between entries.
- **Scanner:** batch sends retry on transient failures (5xx and network
  errors) with exponential backoff + jitter. 4xx is terminal. The HTTP
  client also enables keepalive (`MaxIdleConnsPerHost: 8`,
  `IdleConnTimeout: 90s`) so a fast scanner reuses TCP connections
  across batches instead of paying TLS handshake per POST.
- **Scanner:** SSH ACL prefetch in per-directory mode no longer leaks
  ACL records across directories — the cache is rebuilt at each parent
  change. Previously the cache grew unbounded across the walk.
- **Scanner agent:** the bearer JWT is cached with TTL refresh instead
  of being re-minted on every heartbeat / lease / complete call.
  Heartbeat goroutine + lease loop reuse the same HTTP client with
  keepalive tuned for a long-lived agent.
- **CLI:** `akashic scan list`, `scan cancel <id>`, `scan wait <id>`.
  Exit codes are now meaningful: `0` success, `1` user error (bad args
  / 4xx), `2` server error (5xx / network), `3` scan terminated in
  `failed` state.
- **API:** per-endpoint slow-query thresholds. `SLOW_QUERY_MS`
  (default 100) is the global default; `SLOW_QUERY_MS_OVERRIDES` is a
  JSON map that tightens or relaxes specific routes
  (e.g. `{"browse": 50, "ingest": 200}`).
- **Web:** memoised the Search result row so a parent re-render
  (filter chip change, infinite-scroll fetch, selection toggle) no
  longer reconciles every visible row. Smoother scroll/selection on
  result sets >100 rows.
- **Web:** per-prefix React-Query `staleTime` overrides — sources/
  scanners/users/principals/server-setting ride a 5-minute TTL,
  analytics/dashboard a 1-minute TTL, admin-audit a 5-second TTL,
  rest stay on the 30s default.
- **Web:** RenderBoundary catches WebGL2/Canvas crashes in the storage
  view and shows a fallback message instead of blanking the page.
- **Web:** ESLint added (TypeScript + react-hooks). `npm run lint`
  runs in CI.
- **Docs:** README now mentions the storage-view rendering stack and
  CLI; configuration.md documents the new `SLOW_QUERY_MS*` knobs.

## v0.4.16 — 2026-05-03

- **Storage:** synthetic source nodes in the cross-source view
  are visible again. Childless directories now render with the
  full leaf colour (was: 4 % alpha "directory plate", invisible
  on the dark surface).

## v0.4.15 — 2026-05-03

- **Storage:** the cross-source view (multiple sources, no
  source picked) is now a treemap/sunburst, not a flat list.
  Click — or wheel-zoom-drill — into a source slice to enter
  its tree.
- **Storage:** sunburst is faster at high arc counts. `Path2D`
  cached at layout time + `requestAnimationFrame`-coalesced
  redraws + draws grouped by style cut per-redraw work from
  O(arcs × constant) canvas state changes to O(unique-styles).

## v0.4.14 — 2026-05-02

- **Storage:** hover and pan are smooth at thousands of nodes.
  Sunburst is now Canvas2D (was SVG); treemap stops rebuilding
  the scene on every cursor move and stops re-uploading the
  GPU instance buffer on every pan frame.
- **Storage:** wheel-as-drill. Wheel-in past the max zoom drills
  into the directory under the cursor; wheel-out at fit drills
  up. Same gesture works on the sunburst.
- **Search:** infinite scroll. The page used to render only the
  first 20 hits with no way to scroll. Now loads in chunks of
  100; the underlying Meilisearch `pagination.maxTotalHits` cap
  was raised from 1,000 to 100,000.
- **Privacy:** Meilisearch outbound analytics disabled
  (`MEILI_NO_ANALYTICS=true` in both compose files).
- **UI polish:** dropped residual `backdrop-blur` from
  `ConfirmDialog`, `CommandPalette`, `KeyboardShortcuts` and the
  Treemap's [Fit] button. Same family of GPU compositor cost
  the v0.4.13 Drawer fix targeted.

## v0.4.13 — 2026-05-02

- **UI:** hover stutter on action buttons (Edit / Scan / Delete)
  and lag when the live-log panel is open are gone — the modal
  Drawer no longer applies a permanent `backdrop-blur` GPU
  layer for the full open duration.

## v0.4.12 — 2026-05-02

- **Storage:** drill-down animations. The treemap interpolates
  between layouts on root change instead of snapping.
- **Storage:** pan with shift+drag, wheel-zoom around the cursor,
  [Fit] reset button (top-right when zoomed/panned).
- **Storage:** live mid-scan updates. Opt-in via
  `STREAMING_TOPCHILDREN=true`; ingest batches mark touched
  parents in Redis and a worker rebuilds `top_children`
  incrementally so the Storage view sees fresh data without
  waiting for the post-scan rollup.

## v0.4.11 — 2026-05-02

- **Sources:** scan progress is event-driven, not poll-driven.
  An adaptive change-detection broadcaster (1 % delta floor at
  small estimated totals; ceiling at 100 k) replaces the
  heartbeat-driven push.
- **Storage:** WebGL2 instanced renderer for the treemap.
  Headroom for 50 k+ rectangles at 60 fps.
- **Browse:** cursor-based pagination + server-side sort
  (name / size / modified) + filter chips synced to the URL.
- **Backend:** storage-tree query rewritten as a single recursive
  CTE with `LATERAL` per-directory top-K, replacing the per-row
  Python aggregation.
- **UI:** live-log throttle — auto-scroll capped at 4 Hz with
  length-gating.

## v0.4.10 — 2026-05-02

- **Sources:** Live log tab no longer renders empty when a
  terminal scan finishes mid-stream — the source's primary scan
  selector now prefers the most recent terminal scan over a
  still-running one when both are present.

## v0.4.9 — 2026-05-02

- **Sources:** the watchdog no longer kills in-flight scans on
  sources whose `last_scan_at` is genuinely old. The stale-scan
  threshold is now keyed on the scan's own `started_at`, not the
  source's last successful scan.

## v0.4.8 — 2026-05-02

- **Sources:** the **Scan now** button no longer locks itself
  disabled showing "Queued…" after the scan terminates — the
  bySource selector now reconciles terminal states correctly.

## v0.4.7 — 2026-05-02

- **Sources:** scan progress is also broadcast on the sources
  pubsub channel (not just per-scan), so the Sources page
  refreshes without each row holding its own scan-state
  subscription.
- **Backend:** composite indexes on `scans` for the common
  `(source_id, status, started_at)` access pattern.
- **UI:** memoised `BucketSecurityCard` to stop re-rendering on
  every parent re-render.

## v0.4.6 — 2026-05-02

- **Sources:** fixes for the v0.4.5 selector cache, source-panel
  state preservation across scan transitions, and snapshot
  reconciliation on websocket reconnects.

## v0.4.5 — 2026-05-02

- **Sources:** the Source detail panel no longer thrashes
  during scans. The reactive status hook returns memoised
  derived state instead of recomputing the whole render tree
  on every progress event.

## v0.4.4 — 2026-05-02

- **Dashboard:** stays responsive during scans. Heavy aggregates
  (top sources by size, age histogram, etc.) moved to a lazy
  `useQuery` that doesn't refetch on every scan event.
- **Sources:** **Scan now** is idempotent — clicking twice in
  fast succession no longer queues two scans.

## v0.4.3 — 2026-05-02

- **Sources:** page-load is faster on installs with many
  sources. `useSources` selector + `useScansStream` derivation
  cut both the initial render and the per-event re-render cost.

## v0.4.2 — 2026-05-02

- **Scanner:** exponential-backoff retry on first contact with
  the API, plus a `/health` probe so a scanner started before
  the API is fully up doesn't crash-loop.

## v0.4.1 — 2026-05-02

- **Compose:** the release file's scanner service was missing
  the v0.3.2 `auto` entrypoint shim. Aligned both files.

## v0.4.0 — 2026-05-02

- **Sources:** deleting a source no longer wipes its indexed
  entries. Search and Browse keep working against the historical
  data; the entries get a `(deleted source)` annotation in
  result lists.

## v0.3.2 — 2026-05-02

- **Scanner:** `auto` smart entrypoint chooses the right
  subcommand from the env (claim / discover / agent) so most
  installs don't need to spell out the launch command.
- **Compose:** scanner is a named-volume bundled service in the
  release compose file.

## v0.3.1 — 2026-05-02

- **CI:** Redis service in the test workflow + long-poll
  fallback when the websocket negotiation fails (closes a
  v0.3.0 flaky-test gap).

## v0.3.0 — 2026-05-02

- **Scanner:** easier registration. Three onboarding paths
  (join token, discovery, manual key); the token + discovery
  paths generate the keypair on the scanner host so the private
  key never crosses the wire.

## v0.2.2 — 2026-05-01

- Earlier polish — see `git log v0.2.1..v0.2.2`.

## v0.2.1 — 2026-05-01

- Earlier polish — see `git log v0.2.0..v0.2.1`.

## v0.2.0 — 2026-05-01

- **Scanner architecture:** scans run on **scanner agents**, not
  in API subprocesses. The API enqueues a pending scan and an
  agent leases it via `POST /api/scans/lease`. Multi-site,
  multi-agent, parallel throughput. See README → Scanners.

## v0.1.0 — 2026-05-01

- Initial public release.
