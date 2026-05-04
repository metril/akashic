# Changelog

User-visible changes by release. Format follows
[keep-a-changelog](https://keepachangelog.com/) — the first
bullet under each version is the *why*, not the implementation
detail.

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
