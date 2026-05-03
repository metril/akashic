# Changelog

User-visible changes by release. Format follows
[keep-a-changelog](https://keepachangelog.com/) — the first
bullet under each version is the *why*, not the implementation
detail.

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
