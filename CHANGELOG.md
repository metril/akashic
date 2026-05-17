# Changelog

User-visible changes by release. Format follows
[keep-a-changelog](https://keepachangelog.com/) — the first
bullet under each version is the *why*, not the implementation
detail.

## v0.31.1 — 2026-05-17

**Three scan-UI bug fixes: the Live Log no longer mislabels a
successful scan as "failed", the "Stop scan" button is gone once a scan
has ended, and a source's parallel-scanner count can be saved again.**

### Bug fixes

- **The Live Log no longer shows "failed" after a successful scan.**
  Opening the Live Log from a source card used the *latest* scan for
  that source — which, in the moment just after a re-scan was triggered,
  was still the *previous, failed* run. The panel now always opens on
  the in-flight scan, and its status badge tracks the live scan state
  (so a scan that finishes while you're watching flips to "completed"
  on its own instead of going stale).

- **The "Stop scan" button no longer lingers after a scan ends.** It
  was shown whenever the log's WebSocket was connected — but that
  connection stays open after the scan finishes, leaving a clickable
  Stop button for a scan that was no longer running. It is now gated on
  the scan actually being in progress.

- **A source's "max parallel scanners" can be saved again.** The
  source-edit Save button was disabled whenever the connection-config
  validator objected — and it was re-validating the *masked* saved
  config (secrets shown as `***`), which the form can't fully validate.
  Editing an unrelated field like the parallel-scanner cap was blocked
  by that false objection. The connection-config check now applies only
  when the connection config was actually edited. Source-update audit
  entries also now record changes to `max_parallel_scanners`,
  `preferred_pool`, `is_removable`, the host link and the credential
  profile, which the audit diff previously omitted.

## v0.31.0 — 2026-05-17

**A new admin Maintenance page bundles the operational tooling that
previously needed shell access — and stateful buttons no longer resize
and shove the layout around when their label changes.**

### New features

- **Admin → Maintenance page.** A new admin-only page (alongside
  Access / Audit log / System status) gathers tooling that until now
  meant `docker compose exec … psql` or running
  `python -m akashic.tools.*` by hand:
  - **System overview** — scan, entry, source, scanner and scan-log
    counts at a glance, including how many log rows are past the
    retention window.
  - **Scan & log hygiene** — cancel scans left stuck `pending` or
    `running` after a scanner crash, run the stale-scan watchdog on
    demand, and purge old scan-log entries (the sweep the scheduler
    does every 7 days, now available immediately and tunable).
  - **Search & index jobs** — kick off a Meilisearch reindex or the
    subtree-size / viewable-flag / group-cache backfills as background
    jobs and watch each run through to completion.
  - **Scanner diagnostics** — every scanner's build version and
    liveness, flagging any host a version behind the newest — the
    usual cause of the v0.30.x HTTP 413 scan failures.

  Ships migration `0037` (a `maintenance_jobs` table) — run
  `alembic upgrade head` when deploying.

- **`akashic-scanner version` subcommand.** Running
  `docker compose exec <scanner> akashic-scanner version` now prints
  the exact build, so you can confirm which version a scanner host
  runs without scrolling back through its startup log.

### Bug fixes

- **Buttons no longer resize when their state changes.** A button
  whose label swaps with state — the Live Log auto-scroll toggle,
  "Save" / "Saving…", "Enable" / "Disable", counters like
  "Tag selected (N)" — changed width as its text changed, nudging the
  neighbouring controls around the toolbar. Buttons now reserve a
  stable width and render the loading spinner as a fixed-width
  overlay, so a toggle or a count update never reflows the surrounding
  layout. Fixed across ~20 buttons app-wide.

## v0.30.2 — 2026-05-16

**The scanner name no longer vanishes from the Live Log when the panel
is reopened, and a scanner's running version is now visible per host.**

### Bug fixes

- **Live Log scanner attribution is now durable.** A log line's
  scanner name showed while the scan streamed live but disappeared
  once the log panel was closed and reopened — the reopen/backfill
  path re-derived the name with a read-time JOIN to the `scanners`
  table, which is the wrong design for an immutable log. The scanner
  name is now snapshotted onto each `scan_log_entries` row when the
  line is written (migration `0036`); the backfill and WebSocket-
  snapshot paths read it straight off the row. Attribution now
  survives anything that happens to the `scanners` table afterward,
  and a reopened panel shows exactly what the live stream showed.
  Migration `0036` backfills existing rows whose scanner still exists.

### New behaviour

- **A scanner's running version is visible and kept fresh.** The
  scanner logs `akashic-scanner <version> starting` at startup, so
  `docker logs` shows the deployed build on every host. It also
  reports its version on every lease poll, so Settings → Scanners
  reflects the live binary — previously the version was recorded only
  at claim/registration time and went stale after an in-place image
  upgrade.

### Note — HTTP 413 ingest failures

A scan that fails with `send batch failed: … status 413` is fixed by
**v0.30.1**, not this release: the v0.30.1 scanner splits an
over-limit batch and re-sends the halves, so the scan survives any
reverse-proxy body limit. A *fatal* 413 in the logs means the scanner
that produced it predates v0.30.1 — in a multi-host deployment, every
scanner host must be updated. The new startup version log makes a
stale host easy to spot.

## v0.30.1 — 2026-05-16

**A scan no longer fails when a reverse proxy rejects an oversized
ingest batch, and the scanner name no longer vanishes from a running
scan.** Two unrelated bug fixes a v0.30.0 user hit in the field.

### Bug fixes

- **An HTTP 413 from a reverse proxy no longer kills the scan.** The
  scanner ships file metadata in adaptively-sized batches; if a proxy
  in front of the API has a request-body limit smaller than the batch
  grew to, it returns `413 Request Entity Too Large` — and until now
  the whole scan failed, after the walk had already done all the work.
  The scanner now recovers: on a 413 it splits the batch in half and
  re-sends the halves (recursively, down to a single entry), so every
  entry still reaches the API whatever the proxy's limit. The adaptive
  batcher also lowers its ceiling after a 413 so it stops growing back
  into the same wall. The bundled `web` proxy already allows 32 MB
  bodies; if you front the API with your own proxy, raise its
  `client_max_body_size` (nginx default is 1 MB) — see the README.

- **The scanner name no longer disappears from a running scan.** The
  live scan-state broadcasts (heartbeat, batch-ingest, cancel)
  hardcoded `scanner_name: null`, so the name shown on connect — from
  the correctly-populated WebSocket snapshot — was blanked the moment
  the first live event arrived. The broadcasts now resolve the
  assigned scanner's name.

- **Content extraction no longer silently fails on very large files.**
  A 50 MB file could yield ~50 MB of extracted text, over the API's
  request-body limit, so its content batch was rejected and the text
  never indexed. Extracted text is now capped at 1 MiB per file (far
  more than full-text search needs), and the content-ingest path
  splits oversized batches the same way metadata ingest does.

## v0.30.0 — 2026-05-16

**Text extraction moved into the scanner — search now sees the
contents of SMB, S3 and cloud-drive files.** Previously a standalone
Python `extraction-worker` read file bytes off a *mounted disk*, so
content extraction only ever worked for `local` and `nfs` sources;
`smb`, `s3`, `gdrive` and `onedrive` files were indexed by name only,
never by content. The scanner already has connectors for every source
type, so extraction now runs there: each scanner reads its files,
extracts text (plain decode natively, documents via a co-located
Apache Tika), and posts the text back to the API.

### New behaviour

- **Content extraction works for every source type.** A scan now
  extracts text from new/changed files regardless of whether the
  source is local, NFS, SMB, S3, Google Drive or OneDrive — the
  scanner reads the bytes through the same connector it walks with.
  Document formats (PDF, Office, EPUB, …) go through Tika; plain-text
  files are decoded by the scanner directly.

- **Tika is co-located with the scanner.** For a remote scanner host,
  the new [compose.scanner.yaml](compose.scanner.yaml) runs the
  scanner agent + its own Tika as a unit — document bytes stay on the
  scanner's local network and only the extracted text crosses to the
  API. `AKASHIC_TIKA_URL` configures the address (empty disables
  document extraction; plain-text still works).

### How it works

- The scanner walks metadata as fast as before. After the walk, a
  bounded extraction pool reads the files the API flagged as
  new/changed (returned in each `/api/ingest/batch` response as
  `extract_candidates`), extracts text, and posts it to the new
  `POST /api/ingest/content` endpoint, keyed by `(source_id, path)`.
  The metadata walk is never gated on Tika latency. SMB extraction
  uses a dedicated connector instance + a low worker count so reads
  don't contend with the walk's session.

### Bug fixes

- **A routine re-index no longer wipes extracted text.** The
  Meilisearch metadata flush ([services/meili_indexer.py](api/akashic/services/meili_indexer.py))
  and the tag re-index ([routers/tags.py](api/akashic/routers/tags.py))
  used `add_documents` — a full-document *replace* — so any later
  metadata-only re-index dropped a doc's `content_text`. Both now use
  Meilisearch partial updates (`update_documents`): the metadata
  flush and the content-ingest endpoint are independent partial
  writers that merge by id and never clobber each other.

### Removed

- The Python `extraction-worker` process, the RQ `extraction` queue,
  `akashic/workers/extraction.py`, `akashic/services/extraction.py`,
  and the `rq` dependency — extraction is no longer a separate
  worker. The `extraction-worker` service is gone from both
  `compose.yaml` and `compose.release.yaml`; the `tika` service in
  those files is now profile-gated to `scanner` (the central API
  stack doesn't run Tika).
- The `/api/health/services/extraction/failed` endpoint and the RQ
  queue-depth / failed-job counters — there is no queue to inspect.
  The System Status "Tika" card now shows extraction throughput
  (last 5 min, lifetime total, last-extraction time), fed by counters
  the `/api/ingest/content` endpoint bumps.

### Tests

- New Go: [internal/extract](scanner/internal/extract) — `IsEligible`
  / `ExtractPlain` / `ExtractTika` against a fake Tika; the
  extraction pool drains all jobs, swallows per-file errors, waits
  for in-flight work on `Close`, clamps workers for SMB. New
  `client.SendContent` tests (gzip + retry).
- New pytest [tests/test_ingest_content.py](api/tests/test_ingest_content.py):
  the content endpoint resolves `(source_id, path)`, partial-updates
  Meili, skips unknown paths and directories.
- New pytest [tests/test_meili_indexer.py](api/tests/test_meili_indexer.py)
  case (the regression guard): `flush` uses the partial
  `update_files_partial`, never the full-replace `index_files_batch`.

### Verification

- `go test ./...` from `scanner/` — all packages pass.
- `pytest` from `api/` — all green incl. the new content + indexer
  tests.
- `npx tsc --noEmit && npx vitest run` from `web/` — clean.
- `docker compose -f compose.scanner.yaml` — scanner + co-located
  Tika validate and start.

### Upgrade note

Existing deployments running the v0.29.8 `extraction-worker` will
find it gone after upgrade — that is expected. Already-extracted
content in Meilisearch is preserved (partial updates no longer
overwrite it). Files on `smb`/`s3`/`gdrive`/`onedrive` sources, which
never had content extraction, get it on their next scan.

## v0.29.10 — 2026-05-15

**Phantom "cancelled by api" — scans were re-queued out from under
the scanner running them.** User report: a scanner logged
`scan 7b695464-… cancelled by api` for a scan the user never
cancelled. This is the unfinished half of the v0.29.8 cancel-reason
bug — v0.29.8 fixed the heartbeat poster's *log line* but not the
mechanism that *caused* the phantom cancellation.

### Bug fixes

- **Heartbeats now renew the scan lease**
  ([routers/scan_progress.py](api/akashic/routers/scan_progress.py)).
  The scan-claim lease has a 60 s expiry and **nothing ever extended
  it** — the heartbeat handler updated `last_heartbeat_at` but never
  `lease_expires_at`. So every scan running longer than ~60 s had an
  expired lease while still heartbeating once a second. The
  scheduler's `_requeue_orphan_leases` then reset the perfectly
  healthy scan to `pending` with no assignee, a second scanner
  re-leased it, both scanned the same source, and whichever finished
  first sent the other a 409 — surfacing as `cancelled by api`. The
  heartbeat — which *is* the liveness signal — now extends the lease
  by `_LEASE_DURATION_SECONDS`, so a live scan is never mistaken for
  an orphan. A genuinely dead scanner still loses its lease after
  60 s and the work is correctly re-queued.

- **Scanner no longer overwrites the API's terminal status**
  ([scanner/internal/agent/agent.go](scanner/internal/agent/agent.go)).
  When a heartbeat got a 409, the agent logged `cancelled by api`
  and POSTed `/complete` with status `"cancelled"` — clobbering
  whatever terminal status the API had already written (a watchdog
  `"failed"`, a sibling scanner's `"completed"`). A 409 means the
  API *already* set the authoritative status; the scanner has
  nothing to write. The `errCancelled` sentinel is renamed
  `errAPITerminated`, and a new `terminalDisposition` helper makes
  the agent post `/complete` only when the scanner itself decided
  the outcome (clean finish → `completed`, scan error → `failed`),
  never on a 409. The accurate human-readable reason is still logged
  by the heartbeat poster's `decodeCancelMessage` (v0.29.8).

### Tests

- New [tests/test_scan_progress.py](api/tests/test_scan_progress.py)
  case: a heartbeat renews `lease_expires_at` to ≈ now + 60 s, even
  from an already-expired lease.
- New [tests/test_stale_scan_watchdog.py](api/tests/test_stale_scan_watchdog.py)
  cases: `_requeue_orphan_leases` leaves a scan with a fresh
  (renewed) lease alone, and still re-queues one whose lease has
  genuinely expired.
- New [internal/agent/agent_test.go](scanner/internal/agent/agent_test.go)
  table test for `terminalDisposition`: `nil → completed`,
  `errAPITerminated → post nothing`, other error → `failed`.
- Fixed the v0.29.8 [tests/test_extraction_retry_cap.py](api/tests/test_extraction_retry_cap.py)
  to use an isolated Redis db (15) — a running `extraction-worker`
  drained the test's jobs from db 0 before it could assert.

### Verification

- `pytest` — 751 passed.
- `go test ./...` from `scanner/` — all packages pass.
- `npx tsc --noEmit && npx vitest run` — clean (139 passed).

## v0.29.9 — 2026-05-15

**Production compose file gets the v0.29.8 Tika worker.** The
v0.29.8 `extraction-worker` service and the Tika healthcheck fix
landed only in `compose.yaml` (the dev/build file);
`compose.release.yaml` (production, pre-built GHCR images) was
missed — so production deployments would still hit the unbounded
`rq:queue:extraction` backlog v0.29.8 set out to fix.

### Bug fixes

- **`extraction-worker` added to `compose.release.yaml`**
  ([compose.release.yaml](compose.release.yaml)). Same service as
  the dev file but reusing the pre-built
  `ghcr.io/metril/akashic-api` image (the RQ worker module ships
  in the api package). Two replicas, `restart: unless-stopped`.

- **Tika healthcheck added to `compose.release.yaml`**
  ([compose.release.yaml](compose.release.yaml)). The release file
  had no Tika healthcheck at all — the file's header claims
  healthchecks "match compose.yaml exactly", which was untrue for
  Tika. Without it the new worker's `tika: service_healthy`
  dependency could never be satisfied. Uses the same bash
  `/dev/tcp` probe as v0.29.8's `compose.yaml` fix.

### Verification

- `docker compose -f compose.release.yaml config` — valid.

## v0.29.8 — 2026-05-15

**Four-bug fix bundle from a multi-scanner production deployment.**
The user reported four issues that the new multi-scanner setup
surfaced: a second scanner took too long to join a running scan;
the scan log lost its per-scanner attribution when the panel was
closed and reopened; Tika extraction jobs piled up unbounded; and
scans sometimes self-cancelled with a misleading "cancelled by
user" message. All four were pre-existing latent bugs.

### Bug fixes

- **Second scanner joins a scan within seconds, not minutes**
  ([routers/scanners.py](api/akashic/routers/scanners.py)).
  `notify_eligible_joiners` was only fired inside the
  `split_units` handler — so a joining scanner stayed parked in
  its 30 s long-poll until the lease holder had mounted the
  source, walked the root, and posted its first split (commonly
  30–60 s on an SMB share). The notify now also fires at
  `/api/scans/lease` claim time, so the second scanner starts
  mounting in parallel with the first. Short-circuits when the
  source's `max_parallel_scanners <= 1`.

- **Scan log keeps per-scanner attribution on reopen**
  ([routers/scan_websocket.py](api/akashic/routers/scan_websocket.py)).
  The WebSocket snapshot's `_log_line()` serializer dropped
  `scanner_id` + `scanner_name` — fields the live stream and the
  REST backfill both carry. Closing and reopening the log panel
  sends a fresh snapshot, so the per-line scanner badge silently
  vanished even though the data was on the row. The snapshot now
  LEFT JOINs `scanners` and serializes both fields, matching the
  other two paths.

- **Tika extraction jobs actually get processed**
  ([compose.yaml](compose.yaml)). `compose.yaml` started the Tika
  service but never the RQ worker that drains its queue — the
  worker was a manual `rq worker extraction` step nobody ran, so
  jobs accumulated in `rq:queue:extraction` forever (the smoke
  test found a 1 508-job backlog). A new `extraction-worker`
  service (2 replicas, `restart: unless-stopped`) now runs
  alongside the API. Scale out with
  `docker compose up --scale extraction-worker=N`.
  (v0.29.9 extends this to `compose.release.yaml`.)

- **Scans no longer mislabel non-user cancellations as "by user"**
  ([routers/scan_progress.py](api/akashic/routers/scan_progress.py),
  [observe/heartbeat.go](scanner/internal/observe/heartbeat.go)).
  The heartbeat endpoint returned a bare HTTP 409 for *any*
  terminal scan state, and the scanner unconditionally logged
  "scan cancelled by user; exiting" on every 409 — wrong when the
  watchdog had reaped a stale scan or a sibling scanner closed it
  cleanly. The 409 body now carries `{status, reason}`; the
  scanner decodes it and logs accurately: "terminated by watchdog
  (stale heartbeat)", "scan completed", or "cancelled by user"
  only when it genuinely was. New `scans.cancellation_reason`
  column (migration `0035_scan_cancel_reason`); legacy NULL rows
  fall back to the "by user" message for compatibility.

- **Tika container healthcheck fixed**
  ([compose.yaml](compose.yaml)). The v0.29.0 healthcheck shelled
  out to `wget`, which the `apache/tika:3.0.0.0` image does not
  ship — the container sat `unhealthy` indefinitely. Harmless
  until the new `extraction-worker` added a `tika:
  service_healthy` dependency. The check now uses bash's
  `/dev/tcp` pseudo-device (the image has bash + java only).

### Surface

- **Extraction retry cap + dead-letter visibility.** Each
  extraction job is enqueued with `Retry(max=3, interval=[10, 60,
  300])` ([routers/ingest.py](api/akashic/routers/ingest.py)) — a
  poison file (corrupt PDF, Tika 500) lands in `rq:failed` after
  three attempts instead of re-queuing forever. The System Status
  page's "Failed" card now shows the last-failure timestamp and a
  "view" link to a new read-only endpoint
  `GET /api/health/services/extraction/failed` that lists failed
  jobs with their exception messages
  ([routers/health_services.py](api/akashic/routers/health_services.py),
  [pages/AdminSystemStatus.tsx](web/src/pages/AdminSystemStatus.tsx)).

### Tests

- New [tests/test_scan_join_notify_on_claim.py](api/tests/test_scan_join_notify_on_claim.py):
  the lease endpoint fires the join notify; single-scanner
  sources short-circuit.
- New [tests/test_scan_websocket.py](api/tests/test_scan_websocket.py)
  case: the WS snapshot carries `scanner_id` + `scanner_name`,
  including null for legacy rows.
- New [tests/test_scan_cancellation_reason.py](api/tests/test_scan_cancellation_reason.py):
  user-cancel / watchdog-reap / terminal-complete each yield the
  right `reason` in the 409 body; legacy NULL stays representable.
- New [tests/test_extraction_retry_cap.py](api/tests/test_extraction_retry_cap.py):
  enqueued jobs carry `retries_left=3` + `[10, 60, 300]` intervals.
- New [internal/observe/heartbeat_test.go](scanner/internal/observe/heartbeat_test.go):
  `decodeCancelMessage` routes each `reason` variant to the right
  log line and falls back cleanly on malformed / legacy bodies.

### Verification

- `pytest` — 748 passed (was 739; +9 new).
- `go test ./internal/observe/...` + `go build ./...` + `go vet` — clean.
- `npx tsc --noEmit && npx vitest run` — clean (139 passed).
- `docker compose up -d extraction-worker` — tika healthy, both
  worker replicas drained the live 1 508-job backlog.

## v0.29.7 — 2026-05-15

**Scan log survives scan completion.** User report: "the live log
disappears once a scan is completed." The SourceDetail "Live log"
tab vanished the instant a scan transitioned to a terminal state —
both the tab button (gated on `isScanning`) and its content (gated
on `isScanning && activeScanId`) unmounted, taking the buffered
log lines with them via `useScanStream`'s `setState(initialState)`
cleanup. The user couldn't review the just-completed scan's log
without re-triggering or hunting in the audit history.

### Bug fixes

- **SourceDetail "Scan log" tab persists past scan completion**
  ([components/sources/SourceDetail.tsx](web/src/components/sources/SourceDetail.tsx)).
  New `latestScanId` prop (terminal-inclusive, derived from
  `useActiveScanForSource`) drives both tab visibility and content.
  The tab button shows whenever the source has any scan to inspect;
  the content stays mounted across the scan's terminal transition.
  Renamed from "Live log" → "Scan log" to reflect that it works for
  both running and completed scans.
  ([pages/Sources.tsx](web/src/pages/Sources.tsx))
  passes `latestScanId={latestScanForOpen?.id ?? null}` alongside
  the existing `activeScanId`.

### Surface

- **Terminal-status badge in the log panel header**
  ([components/scans/ScanLogPanel.tsx](web/src/components/scans/ScanLogPanel.tsx)).
  When viewing a completed/failed/cancelled scan's log, a Badge
  appears next to the WS status pill (which would otherwise still
  say "Live" or "Closed" — misleading for a frozen, ended scan).
  Variant follows the scan's terminal state: `online` for
  completed, `failed` for failed, `neutral` for cancelled. Hidden
  for in-flight scans (the existing pill is enough).

- **Tab auto-resets to Details if no scan exists**. If the user is
  on the Scan log tab and `latestScanId` becomes null (e.g., the
  source has never been scanned or its scan history was purged),
  the tab falls back to Details rather than disappearing
  underneath the user.

### Tests

- New [components/scans/ScanLogPanel.test.ts](web/src/components/scans/ScanLogPanel.test.ts):
  6 cases covering `terminalBadgeVariantFor` (null for in-flight /
  nullish input, completed → online, failed → failed, cancelled →
  neutral, unknown → null). React-render assertions stay in manual
  smoke since the web test setup is node-env, no jsdom.

### Verification

- `npx tsc --noEmit && npx vitest run` — clean (139 passed; was 133).
- Backend untouched — pytest + go test unchanged.

## v0.29.6 — 2026-05-15

**SMB probe surfaces share-ACL denial. Empty-batch wire-shape crash
fixed. AIMD no longer halves on 4xx.** Three problems chained
together in production: user supplied credentials that authenticate
but lack list permission on the SMB share, the probe still reported
green, the scan ran and the walker hit ACCESS_DENIED on every
ReadDir → zero entries → final batch sent with `Entries: nil` which
JSON-marshals to `null`, which the API rejects as 422.

### Bug fixes

- **SMB probe: ReadDir(`.`) smoke after Mount**
  ([scanner/internal/connector/smb.go:Connect](scanner/internal/connector/smb.go)).
  v0.29.1 caught guest fallback. v0.29.5 caught empty-password
  bypass. v0.29.6 catches the third bypass: authenticated session
  + permitted tree-connect (mount succeeds) + DENIED list/read at
  the share root. Pre-fix Connect returned nil after Mount; the
  probe lands ok=true; the scan silently fails. Post-fix the
  connector does a `share.ReadDir(".")` smoke before returning;
  any error there is surfaced as `step=auth` with the diagnostic
  `share "X" mounted but ReadDir denied (credentials lack list
  permission for user "Y": ...)`.

- **Empty-batch JSON wire shape**
  ([scanner/internal/scanner/scanner.go:Run](scanner/internal/scanner/scanner.go)).
  `var batch []models.EntryRecord` declared a NIL slice; the final
  `enqueue(true)` on a zero-entry walk sent `Entries: nil` which Go's
  encoding/json marshals to `"entries":null`, rejected by the API's
  required `list[EntryIn]` Pydantic field with 422. Bug existed
  since the scanner first emitted final batches; v0.29.2's pipeline
  refactor changed when it surfaced. Initialise as
  `batch := []models.EntryRecord{}`, reset to fresh empty slice
  after each enqueue, plus a defensive `if batch == nil` guard
  inside enqueue.

### Hardening

- **AIMD ignores non-load 4xx**
  ([scanner/internal/client/client.go](scanner/internal/client/client.go),
  [scanner/internal/scanner/scanner.go](scanner/internal/scanner/scanner.go)).
  v0.29.2's AdaptiveBatcher halved on any non-nil error — but a
  422 / 400 / 401 is misuse / code bug, not overload. Halving the
  batch makes no sense and muddies the diagnostic (the user just
  saw "1000 → 500 after error" alongside their wire-shape 422,
  implying a load issue that wasn't there). Post-fix the client
  exports `IsLoadSignal(err) bool` (true for 5xx, network failure,
  413; false for other 4xx). The sender goroutine skips
  AdaptiveBatcher.Observe entirely on non-load errors so the size
  neither halves NOR grows.

### Observability

- **Walk-finished log line**
  ([scanner/internal/scanner/scanner.go:Run](scanner/internal/scanner/scanner.go)).
  Pre-fix the only post-walk log was inside the success branch
  (`scan complete: N files, M dirs, K batches`), which was SKIPPED
  when the send-batch path errored — exactly the empty-batch
  crash case. Now an INFO line `walk finished: N files, M dirs,
  X inaccessible dirs, Y inaccessible files, Z pending` fires
  unconditionally, so an empty walk is visible in
  `docker compose logs scanner`.

### Tests

- New scanner tests:
  - [scanner/internal/scanner/empty_batch_wire_test.go](scanner/internal/scanner/empty_batch_wire_test.go)
    — 2 cases verifying the JSON wire shape for an empty-walk
    final batch (`"entries":[]`, never `null`; round-trips as a
    JSON array).
  - [scanner/internal/scanner/batchsize_load_signal_test.go](scanner/internal/scanner/batchsize_load_signal_test.go)
    — table test: 500/503/413 halve; 422/400/401 leave the batch
    size unchanged.
  - [scanner/internal/client/client_load_signal_test.go](scanner/internal/client/client_load_signal_test.go)
    — 7 cases for the `IsLoadSignal` classifier (nil, each typed
    error, wrapped through `fmt.Errorf %w`, integration against
    a stub HTTP server for every status code).
- Extended [scanner/internal/probe/probe_smb_test.go](scanner/internal/probe/probe_smb_test.go)
  with the new ReadDir-denied error string → step=auth case.

### Verification

- `pytest tests/` — 737 still pass (no API changes).
- `go test ./...` from `scanner/` — all green.
- `npx tsc --noEmit && npx vitest run` — clean (133 passed).

## v0.29.5 — 2026-05-15

**SMB empty-password bypass, scan-join observability, credential
retest, credentials encrypted at rest.** Four bugs / hardening items
the user surfaced after v0.29.4 deployed.

### Bug fixes

- **SMB reachability still claimed access with wrong credentials.**
  v0.29.1 added a guest/anonymous session-flag rejector, but there's
  a parallel bypass when the merged config carries `password = ""`
  — go-smb2's `NTLMInitiator{User: "alice", Password: ""}` is
  permitted (the vendor only rejects empty `User`). Some SMB servers
  (Samba `force user`, Windows null-password accounts, anonymous
  shares) respond with a fully AUTHENTICATED session — not guest —
  so `IsGuest()` / `IsAnonymous()` never fires.

  Three-layer fix:

  - **Scanner probe** ([scanner/internal/probe/probe.go:runSMB](scanner/internal/probe/probe.go#L112-L155)).
    Reject empty password as `step=config` unless
    `connection_config.allow_empty_password=true` is explicitly set.
  - **Scanner connector** ([scanner/internal/connector/smb.go:Connect](scanner/internal/connector/smb.go#L57-L100)).
    Defense in depth — the connector refuses empty password at Connect
    so a credential row that slipped through API validation still
    gets caught at scan time. Opt-in via new
    `SetAllowEmptyPassword(true)` method.
  - **API validation** ([routers/credential_profiles.py](api/akashic/routers/credential_profiles.py)
    + [routers/sources.py:_validate_smb_password_requirement](api/akashic/routers/sources.py)).
    `POST/PATCH /api/credential-profiles` rejects SMB profiles with
    missing/empty `password`. `POST/PATCH /api/sources` rejects SMB
    sources whose merged config (host_profile + host + source_profile
    + source) has no non-empty password. Same `allow_empty_password`
    opt-in for legitimate lab / anonymous-share configurations.

### Observability + tuning

- **Multi-scanner re-scan diagnostics** ([services/scan_join.py](api/akashic/services/scan_join.py),
  [routers/scan_work.py:split_units](api/akashic/routers/scan_work.py)).
  The notify-side of v0.29.0's push-based discovery was silent on
  re-scans where the second scanner didn't engage. Now:
  - INFO log on every `/work/split` showing the notify count.
  - INFO log when `notify_eligible_joiners` finds 0 eligible scanners
    after the filter, including "of N total with pool/source access"
    so the gap is observable (`docker compose logs api | grep scan_join`).
  - DEBUG log every 10 consecutive 204 timeouts in the scanner agent's
    `scanJoinLoop` so an idle-and-listening agent is visible from the
    scanner-side log too.

- **Softer online threshold for scan-join notifications**
  ([routers/sources.py:_eligible_scanners_for](api/akashic/routers/sources.py#L664-L700)).
  The bulk-probe path keeps the 120 s online window (don't waste 5 s
  per dead scanner). The notify path now uses 600 s — the join queue
  is durable for 7 days and capped at 50, so notifying a
  marginally-stale scanner is essentially free and a missed
  notification is the actual user-visible failure mode for re-scans
  where the second scanner was idle between scans.

### Hardening

- **Auto-retest reachability after a credential-profile update**
  ([services/credential_retest.py](api/akashic/services/credential_retest.py)).
  After `PATCH /api/credential-profiles/{id}` actually changes the
  credentials, a background task fans out fresh probes against every
  source whose effective credentials derive from that profile —
  directly via `Source.credential_profile_id` OR via
  `Host.credential_profile_id`. Cap of 50 sources per fan-out so a
  widely-shared profile doesn't trigger a thundering herd; the
  remainder can be retested manually via the panel. Closes the "I
  fixed broken creds but the panel keeps showing green" foot-gun.

- **Credentials encrypted at rest**
  ([services/credential_crypto.py](api/akashic/services/credential_crypto.py),
  [migration 0034_credential_profile_encrypt](api/alembic/versions/0034_credential_profile_encrypt.py)).
  Pre-fix the `credential_profiles.credentials` JSONB column stored
  usernames + passwords in plaintext — anyone with DB read access
  (pg backup, replica snoop, support-session `SELECT *`) saw every
  SMB/NFS credential. Post-fix:
  - New `credentials_encrypted: bytea` column. Fernet over
    HKDF-SHA256 of `settings.secret_key` (same primitive as OAuth
    refresh tokens; see [services/secret_encryption.py](api/akashic/services/secret_encryption.py)).
  - Migration `0034` adds the column, encrypts every legacy
    plaintext row, NULLs the plaintext column. Refuses to run with
    the dev-default `SECRET_KEY` so an unprepared deployment can't
    accidentally "encrypt" with a known key.
  - Read path ([services/source_config.py:_profile_credentials](api/akashic/services/source_config.py))
    prefers the encrypted column, falls back to plaintext for
    pre-migration rows. Decrypt failure (rotated key / tampered
    ciphertext) returns `{}` and logs at WARN.
  - Write paths in the credential-profiles router encrypt on
    create/update; the plaintext column is left NULL.
  - `CredentialProfileResponse.from_model` decrypts then applies the
    existing `_scrub_config` masking so API responses still mask
    `password` / `secret_key` / `api_token` as `"***"`.

### Tests

- New scanner tests:
  [scanner/internal/probe/probe_smb_empty_password_test.go](scanner/internal/probe/probe_smb_empty_password_test.go)
  — 3 cases for the new config-step guard + opt-in behaviour (native
  bool + string form). Updated `TestSMBNoServerFailsAtConnect` to
  supply a password so it exercises the connector path.
- New API tests:
  - [test_credential_crypto.py](api/tests/test_credential_crypto.py) —
    7 cases for the encrypt/decrypt primitive (round-trip, tamper,
    wrong key, empty, str/bytes ciphertext input, type errors).
  - [test_smb_password_validation.py](api/tests/test_smb_password_validation.py)
    — 8 cases for credential-profile + source create/update API guards.
  - [test_credential_retest.py](api/tests/test_credential_retest.py)
    — 5 cases for the auto-retest fan-out (direct, via-host, cap,
    dispatch count, empty case).
  - [test_scan_join_threshold.py](api/tests/test_scan_join_threshold.py)
    — 3 cases for the wider notify-path online threshold + the
    0-eligible diagnostic log line.
- Updated 6 pre-existing tests to add passwords to their SMB profile /
  source payloads now that empty SMB passwords are rejected
  ([test_credential_profiles.py](api/tests/test_credential_profiles.py)
  + [test_source_reachability.py](api/tests/test_source_reachability.py)).

### Schema

- Migration `0034_cred_prof_encrypt`: adds
  `credential_profiles.credentials_encrypted: bytea NULL`, runs
  one-time encrypt sweep, NULLs out plaintext column. Reversible.

### Verification

- `pytest tests/` — 737 passed, 1 skipped (23 new cases + 6 updated).
- `go test ./...` from `scanner/` — all packages green (3 new SMB
  empty-password cases).
- `npx tsc --noEmit && npx vitest run` — clean (133 passed).

## v0.29.4 — 2026-05-15

**CI test-server gzips: lets v0.29.x Release pipelines actually pass.**
v0.29.2's client started gzipping batches >= 1 KB, but the
`scanner_test.go` test server still parsed raw JSON. Local-connector
entries (full ACL/hash/owner fields) cross the threshold in a 2-entry
batch, so `Decode` errored, the `onBatch` callback never fired, and
`TestScanner_ScanLocal` failed seeing 1 of 3 batches counted. v0.29.2
and v0.29.3 both released their docker artifacts via the tag-push
Release workflow which ran the failing test → release failed → no
images published.

Fix: gzip-decode the request body in `newTestServer` when
`Content-Encoding: gzip` is set, mirroring what the API's
`_GzipRequestMiddleware` does in production. Bumped as v0.29.4 so the
released tag carries the fix — re-running the Release workflow on
v0.29.2 / v0.29.3 would have run the same failing test against the
pre-fix code.

No application change. Same code as v0.29.3, plus the test-server
update.

### Verification

- Build CI green (run 25902020627): web tests + typecheck ✓,
  API tests ✓ (11m19s), scanner tests ✓ (19s), all image builds ✓.

## v0.29.3 — 2026-05-15

**Live Log surfaces the AIMD batch size.** Closes out the v0.29.2
plan: the heartbeat carries `current_batch_size` and the scan row
captures it, but nothing on the frontend rendered the value. Now the
Live Log header shows a small `batch 1250` chip next to the status
pill, updating live from the WS progress events. Hidden on legacy /
pre-v0.29.2 agents where the field is null.

### Surface

- WS progress events ([routers/scan_progress.py](api/akashic/routers/scan_progress.py))
  + per-scan snapshot ([routers/scan_websocket.py](api/akashic/routers/scan_websocket.py))
  + source-events broadcast all carry `current_batch_size` so any
  consumer can render it.
- Live Log drawer chip ([components/scans/ScanLogPanel.tsx](web/src/components/scans/ScanLogPanel.tsx))
  reads from the progress event first, falling back to the snapshot
  field — so a scan that opened the drawer mid-flight gets an
  immediate value from the snapshot then tracks the heartbeat from
  there.
- Frontend `Scan` / `ScanSnapshot` / `ScanProgressEvent` types
  ([web/src/types/index.ts](web/src/types/index.ts)) gain
  `current_batch_size?: number | null`.

### Verification

- `npx tsc --noEmit && npx vitest run` — clean (133 passed).
- Targeted pytest — 30 passed across scan_progress + the
  v0.29.2 scan_counters / meili_indexer / tag_propagation_bulk
  suites.

## v0.29.2 — 2026-05-15

**Throughput rework: pipeline + gzip + AIMD + N+1 elimination + Redis
scan counters + Meili debouncer.** Stage 2 of v0.29. The user reported
"the batching seems slow" after v0.28.2; this release attacks four
walls in series:

  1. Walker→sender pipelining + gzip + adaptive batch sizing in the
     scanner (Part B).
  2. Bulk tag-inheritance propagation in ingest (Part F) — one
     `SELECT … FROM unnest(uuid[], text[])` per source per batch
     instead of one round trip per new entry.
  3. Redis-backed per-scan counters (Part G) — removes the per-batch
     `Scan`-row lock so two scanners on the same scan no longer
     serialize at COMMIT time.
  4. Per-source Meilisearch debouncer (Part H) — replaces the
     per-batch background task with a 5 s / 5000-entry debounced
     bulk index call.

### Throughput — scanner (Part B)

- **Walker→sender pipelining** ([scanner/internal/scanner/scanner.go](scanner/internal/scanner/scanner.go)).
  Pre-fix the walker synchronously blocked on each batch's
  `SendBatch` HTTP round-trip (200–600 ms per batch on a real
  network), idling the walker 12–50% of the time on fast storage.
  Post-fix a bounded channel (buffer=3) sits between the walker and
  a dedicated sender goroutine; the walker pushes a batch and
  continues immediately. Final-batch + error paths close the channel
  cleanly so the sender drains without deadlock or goroutine leak.
- **Adaptive batch sizing (AIMD)** ([scanner/internal/scanner/batchsize.go](scanner/internal/scanner/batchsize.go)).
  Static `BatchSize: 500` is wrong for someone — local NVMe sources
  sustain 5000+/batch, remote SMB with full ACLs barely 250. AIMD
  converges on whatever the source × API × Postgres × proxy can
  sustain: initial 1000, floor 250, ceiling 5000; latency target
  band 100–400 ms; below low → +250; above high → ÷2; error → ÷2 +
  clamp to floor. Env overrides (`AKASHIC_INGEST_BATCH_SIZE_*`) pin
  the size when ops want fixed sizing. Adjustments log to
  `docker compose logs scanner` so changes are visible in real time.
  Heartbeat carries the current value through a new
  `current_batch_size` field so the Live Log row tooltip can
  surface it.
- **gzip request body** ([scanner/internal/client/client.go](scanner/internal/client/client.go),
  [api/akashic/main.py](api/akashic/main.py)).
  Batches >= 1 KB now ship gzip-encoded (`Content-Encoding: gzip`)
  with an ASGI middleware on the API side that decompresses before
  routing. Scan-batch JSON compresses 80–90% (repeated keys + paths
  + ASCII strings), so wire size shrinks 5–10× — a meaningful win
  for remote scanners on slow uplinks. Includes a 32 MB
  decompressed-size cap as a gzip-bomb guard.

### Throughput — API (Parts F + G + H)

- **Bulk tag-inheritance propagation** ([services/tag_inheritance.py:propagate_to_new_entries](api/akashic/services/tag_inheritance.py),
  [routers/ingest.py](api/akashic/routers/ingest.py)).
  Pre-fix the ingest hot path called `propagate_to_new_entry` once
  per new entry from three sites (Phase 1 tombstone resurrect, Phase
  3 fresh INSERT, Phase 4 race-loser resurrect) — at ~1 ms per
  round-trip, a 2000-row 80%-new batch spent ~1.6 s in this N+1
  loop alone. New bulk variant takes a list of
  `(entry_id, source_id, path)` and runs one
  `SELECT … FROM unnest(uuid[], text[]) … JOIN entries anc …` per
  source per batch. The kept singular `propagate_to_new_entry` is a
  thin wrapper for non-ingest callers (admin tag-apply,
  move-rebalancer).
- **Redis-backed per-scan counters** ([services/scan_counters.py](api/akashic/services/scan_counters.py)).
  Pre-fix every batch held a brief row-level lock on the `scans`
  row while it did `scan.files_found += N`, `scan.files_new += M`,
  etc. With multi-scanner cooperation finally working in v0.29.0
  and AIMD batch sizes climbing, the lock became the dominant
  per-batch serialization point. Post-fix the hot path does one
  `HINCRBY` per non-zero counter into a Redis hash
  `akashic:scan:{id}:counters` — single round-trip, no row lock,
  no transaction contention. Reads (heartbeat broadcast,
  `/api/scans/{id}`) overlay row + hash so the live counter view
  matches the pre-v0.29.2 semantics. Terminal-status transitions
  (`/api/scans/{id}/complete`, `_maybe_finalize_scan`, stale-scan
  watchdog) flush the hash as deltas onto the row, then delete the
  hash. 7-day TTL on the hash + watchdog flush handle the crash-
  recovery case.
- **Per-source Meilisearch debouncer** ([services/meili_indexer.py](api/akashic/services/meili_indexer.py)).
  Pre-fix every ingest batch enqueued an
  `_index_files_to_meilisearch` background task — hundreds of
  Meili tasks queued back-to-back on a high-throughput scan, each
  creating per-task overhead and serializing internally on Meili's
  indexing queue. Post-fix every batch SADDs entry ids onto a per-
  source set `akashic:meili:pending:{source_id}`; a single
  `run_debouncer()` asyncio task (started from the app lifespan)
  sweeps every 1 s and flushes a source when its set hits 5000
  entries or the first-marked-at timestamp is 5 s old, whichever
  comes first. Parallel across sources — two concurrent scans on
  different sources flush independently. Scan-completion paths
  call `flush(source_id)` directly so search results catch up at
  terminal time without waiting for the debounce window.

### Schema

- New migration `0033_scan_curr_batch_size`: adds nullable
  `scans.current_batch_size INTEGER` so the Live Log row tooltip
  can render the heartbeat-reported AIMD value.

### Surface

- `HeartbeatIn` schema gains `current_batch_size: int | None`
  ([api/akashic/schemas/scan.py](api/akashic/schemas/scan.py)).
  Legacy scanners (pre-v0.29.2) omit the field and continue to
  work; only the new agent populates it.
- The per-scan WS `scan.state` events now reflect Redis-overlaid
  `files_found` so a long running scan's dashboard tile updates
  smoothly, not at flush boundaries.

### Tests

- Scanner Go: new `batchsize_test.go` (8 cases — grow/halve/in-band/
  error/ceiling/floor/pin/hooks), `pipeline_test.go` (walker proceeds
  through slow sender; sender error propagates; final batch reaches
  server), `client_gzip_test.go` (large body gzipped + decodes
  cleanly; small body sent plain).
- API: new `test_scan_counters.py` (8 cases — accumulate, dedup zero,
  reject typo, overlay sums, flush as delta + clear, multi-flush
  accumulation, no-op on empty, concurrent adds from two
  "scanners"), `test_meili_indexer.py` (8 cases — mark_dirty
  accumulates, empty input no-ops, should_flush size/age/empty,
  flush empty/idempotent, two-source independence, flush clears
  keys), `test_tag_propagation_bulk.py` (5 cases — empty list,
  many-children-one-ancestor, no cross-contamination, singular
  wrapper, mixed-source grouping).

### Verification

- `pytest tests/` — 715 passed, 1 skipped (21 new cases across the
  three new test files).
- `npx tsc --noEmit && npx vitest run` — clean (133 passed).
- `go test ./...` from `scanner/` — all packages green (8 new AIMD
  cases + 3 new pipeline cases + 2 new gzip cases).

## v0.29.1 — 2026-05-15

**SMB probe honesty.** v0.29.0 shipped an NFS-side fix for the
reachability false-positive, but the user's actual report was about
SMB. Root cause was different: SMB servers (Samba, older Windows)
honour a "fall back to guest on bad credentials" policy and return a
SUCCESSFUL session-setup with `SMB2_SESSION_FLAG_IS_GUEST` set instead
of an auth failure. The pre-fix SMB connector silently accepted the
guest session, so the probe reported `ok=true` when the user knew the
credentials wouldn't work — they didn't; the server was handing out
guest sessions.

### Bug fixes

- **SMB probe rejects guest / anonymous downgrades**
  ([scanner/internal/connector/smb.go](scanner/internal/connector/smb.go#L57-L135),
  [scanner/internal/probe/probe.go:runSMB](scanner/internal/probe/probe.go#L112-L154)).
  After a successful `Dial`, the connector now checks
  `session.IsGuest()` / `IsAnonymous()`. If either is set, the
  session is logged off and Connect returns an explicit auth error
  (`server fell back to guest session for user "alice" — supplied
  credentials were rejected`). Required exposing the SMB2 session
  flags on the vendored go-smb2 `*Session` — added `IsGuest()` and
  `IsAnonymous()` methods in
  [scanner/internal/vendor/go-smb2/client.go](scanner/internal/vendor/go-smb2/client.go).
- **SMB probe error classification**
  ([scanner/internal/probe/probe.go:classifySMBProbeError](scanner/internal/probe/probe.go#L134-L154)).
  Pre-fix every `Connect` error mapped to `step=auth`, which made
  the guest-rejection diagnostic indistinguishable from a real
  "host unreachable" failure in the reachability panel. Mirrors
  the CLI's `classifySMBError`: `smb dial` → step=connect,
  `smb session` → step=auth, `smb mount` → step=mount.

### Tests

- New [scanner/internal/probe/probe_smb_test.go](scanner/internal/probe/probe_smb_test.go):
  config-step short-circuits, error-classifier table tests (including
  the new guest / anonymous rejection messages landing on step=auth),
  and a routable-failure case proving `step=connect` (not `step=auth`)
  surfaces for an unreachable host.

### Verification

- `go test ./...` from `scanner/` — all packages green (8 new SMB
  probe cases + the v0.29.0 NFS cases).
- The v0.29.0 NFS-side honesty fix still stands — different code path,
  different downgrade vector. Both layers needed.

## v0.29.0 — 2026-05-15

**Multi-scanner cooperation, NFS probe honesty, history dedup, services
panel.** Stage 1 of the v0.29 work — the user added a second scanner
and discovered four separate cliffs: only one scanner ever did any
work, the NFS reachability test claimed green when credentials were
broken, repeat reachability tests stacked identical dots forever, and
the Tika/Meili workflow was a black box. All four addressed in one
release; the throughput rework (pipeline, gzip, AIMD batch size,
bulk N+1, Redis-backed scan counters, Meili debouncing) lands in
v0.29.1.

### Bug fixes

- **Multi-scanner scans actually use both scanners**
  ([services/scan_join.py](api/akashic/services/scan_join.py),
  [routers/scan_work.py](api/akashic/routers/scan_work.py)).
  Pre-fix the cap-enforcement code in `_distinct_active_scanners`
  existed but never mattered: scanner B polling `/api/scans/lease`
  was filtered out by `assigned_scanner_id IS NULL` once scanner A
  claimed the scan, and had no other channel to discover an in-flight
  cooperative scan — so B idled forever and A worked the whole pool
  alone. Push-based discovery via a new per-scanner Redis list
  `scanner:{id}:scan_join`: after `POST /scans/{id}/work/split` the
  API LPUSHes a join payload to every eligible-other scanner; each
  long-polls `GET /api/scanners/{id}/scans/long-poll` and BRPOPs the
  payload, then dispatches straight into `runUnitCoordinated` — the
  same path the original lease holder runs. `LTRIM 0 49` caps backlog
  per scanner; eligibility mirrors the existing `_eligible_scanners_for`
  rules (pool match, allowed_source_ids match, currently online).
  Source-side guard: `max_parallel_scanners <= 1` short-circuits the
  notify so single-scanner sources never wake other agents.
- **NFS reachability now reflects credentials**
  ([scanner/internal/probe/probe.go:runNFS](scanner/internal/probe/probe.go#L187-L300)).
  Pre-fix the in-process probe was `net.Dial("tcp", host:port)` —
  ok=true purely from port reachability, even when AUTH_SYS uid was
  root-squashed or the export wasn't actually accessible. Post-fix
  it calls `nfsprobe.Probe` (the same in-process protocol library
  the API-side `test_nfs` invokes via `akashic-scanner test-connection`)
  with AUTH_SYS uid/gid plus Kerberos plumbing forwarded from
  `connection_config`. Now `ok=true` means "this scanner can mount and
  walk this export", not "the NFS port answers". `export_path` is now
  required (a missing `export_path` returns `step=config` rather than
  silently falling back to TCP-only success). SMB / S3 / cloud probes
  were already honest — only NFS had the TCP-fallthrough bug.
- **History dots stop stacking on no-state-change**
  ([services/reachability_results.py:record_result](api/akashic/services/reachability_results.py#L24-L75),
  [components/sources/AllowedScannersPanel.tsx:HistoryDots](web/src/components/sources/AllowedScannersPanel.tsx)).
  Pre-fix every probe inserted a fresh row, so clicking "Test" three
  times in a row produced three identical green dots regardless of
  whether the state actually changed. Write-side dedup: when the
  newest row for the (source, scanner) pair has identical
  `(ok, step, error)`, UPDATE its `completed_at` to now instead of
  INSERTing a new row. Read-side defence in `HistoryDots`: collapse
  consecutive same-state entries client-side before the `.slice(0, 5)`
  so legacy rows that pre-date the dedup don't render as duplicate
  dots.

### Observability

- **Service activity surface for Tika + Meilisearch**
  ([routers/health_services.py](api/akashic/routers/health_services.py),
  [pages/AdminSystemStatus.tsx](web/src/pages/AdminSystemStatus.tsx)).
  Two new admin-auth endpoints, both with 5-second in-process caches:
  - `GET /api/health/services` — per-service liveness for Postgres,
    Redis, Meilisearch, Tika. `{ok, latency_ms, error}` per service.
    Drives a sidebar chip
    ([ServicesHealthBadge.tsx](web/src/components/admin/ServicesHealthBadge.tsx))
    that flips green / amber / red without needing the user to navigate.
  - `GET /api/health/services/activity` — rich payload. Tika side:
    RQ extraction queue depth (`LLEN rq:queue:extraction`), failed-job
    count (`ZCARD rq:failed`), and three new Redis counters the
    extraction worker now writes (`akashic:tika:extracted_total`,
    `akashic:tika:last_extracted_at`, per-minute bucket
    `akashic:tika:extracted:YYYYMMDDHHMM` with 6-minute TTL — summed
    over the last 5 buckets for "extracted in last 5 min").
    Meilisearch side: document count from
    `/indexes/files/stats`, pending task count + last-task status
    from `/tasks` — Meilisearch's own admin surfaces. A Meili-side
    failure surfaces as `ok: false` with the error string rather than
    cratering the activity endpoint.
- **Tika healthcheck in compose** ([compose.yaml](compose.yaml)).
  `apache/tika:3.0.0.0` had neither a compose healthcheck nor any
  liveness signal beyond port-reachable. Added a `wget`-based check
  against `/tika` so the new `/api/health/services` doesn't have to
  carry the whole "is Tika alive" answer at request time.
- **Scanner agent long-polls scan-join channel**
  ([scanner/internal/agent/scan_join.go](scanner/internal/agent/scan_join.go),
  [agent.go](scanner/internal/agent/agent.go)).
  New `scanJoinLoop` goroutine started from `Run()`. Mirrors the
  existing `reachabilityLoop` (probes) and lease loop ergonomics —
  one cooperative scan at a time per agent, blocks in
  `runUnitCoordinated` until its leased units drain.

### Surface

- New admin route `/admin/system-status` showing per-service liveness
  chips plus Tika and Meilisearch activity cards (queue depth,
  throughput, document count, last task). 10 s poll while the page
  is open; 30 s poll on the sidebar chip.
- New sidebar entry under Admin → "System status".
- New hooks: [useServicesHealth.ts](web/src/hooks/useServicesHealth.ts),
  exposing `useServicesHealth` + `useServicesActivity`.

### Tests

- New API tests:
  [test_scan_join_queue.py](api/tests/test_scan_join_queue.py) — 10
  cases covering notify fan-out rules (pool, allowed_source_ids,
  online, enabled), LTRIM cap, JWT-gated long-poll endpoint, durable
  list semantics.
  [test_reachability_results_dedup.py](api/tests/test_reachability_results_dedup.py)
  — 4 cases covering consecutive-identical merge, state-change INSERT,
  per-pair scoping, and the `scanner_id=None` (inline) path.
  [test_health_services.py](api/tests/test_health_services.py) — 6
  cases covering auth gating, payload shape, 5 s cache, Tika counter
  read, and graceful Meili-failure surfacing.
- New scanner Go test:
  [scanner/internal/probe/probe_nfs_test.go](scanner/internal/probe/probe_nfs_test.go)
  — guards the config-step short-circuits (no host / no export_path)
  and confirms an unreachable host returns a protocol-step failure
  rather than the pre-fix TCP-only ok=true.

### Verification

- `pytest tests/` — 694 passed, 1 skipped.
- `npx tsc --noEmit && npx vitest run` — clean (133 passed).
- `go test ./...` from `scanner/` — all packages green.

## v0.28.2 — 2026-05-14

**Multi-scanner stability + observability.** Three bugs surfaced after
a user added a remote scanner: ingest batches 413'd through the web's
nginx proxy, reachability probes silently dropped during multi-scanner
fan-out, and the Live Log gave no clue which scanner produced each
row. All three fixed in one patch.

### Bug fixes

- **Remote scanner ingest no longer 413s.** `web/nginx.conf`'s
  `/api/` block had no `client_max_body_size`, so it inherited
  nginx's 1 MB default — under which a 1000-entry batch (~1–2 MB
  serialized) failed every time. Local-compose scanner happens to
  hit `api:8000` directly so this never reproduced in dev. Fix:
  `client_max_body_size 32m;` plus `proxy_read_timeout`/
  `proxy_send_timeout` bumped to 120 s (the 30 s probe long-poll
  plus bulk `/test-shares` could nudge the default 60 s upstream
  timeout). Scanner default `BatchSize` also lowered 1000 → 500 in
  [scanner/internal/agent/agent.go](scanner/internal/agent/agent.go)
  and [scanner/internal/agent/unit_runner.go](scanner/internal/agent/unit_runner.go)
  as a defensive belt for ops running their own reverse proxy.
- **Probe queue is now durable** ([services/probe_dispatch.py](api/akashic/services/probe_dispatch.py)).
  `scanner:{id}:probe` switched from Redis pub/sub to a list backed
  by `LPUSH` + `BRPOP`. The pub/sub regime silently dropped any
  probe published during the ~50 ms gap between consecutive long-
  poll cycles — visible during bulk `/test-shares` fan-out, where
  one scanner per share would time out to `pending=true` while the
  rest reported. Lists are durable until consumed; `LTRIM 0 99` caps
  per-scanner backlog at 100 items so an offline scanner can't
  accumulate unbounded probes.

### Observability

- **Scanner-side log lines.** `docker compose logs scanner` now
  actually shows what the agent is doing: probe receive / result /
  report-error in [reachability.go](scanner/internal/agent/reachability.go),
  scan begin / exit in [agent.go](scanner/internal/agent/agent.go),
  and per-batch counts in [scanner/internal/scanner/scanner.go](scanner/internal/scanner/scanner.go).
- **Scanner attribution in the Live Log.** New migration
  `0032_scan_log_scanner_id` adds a nullable `scanner_id` column to
  `scan_log_entries`. The ingest JWT minted by `_mint_ingest_jwt`
  at lease time now carries a `scanner_id` claim — server-side
  minted, trusted, not lifted from a client header. A new
  `get_ingest_scanner_id` dep in [auth/dependencies.py](api/akashic/auth/dependencies.py)
  extracts it; scan-progress POSTs in
  [scan_progress.py](api/akashic/routers/scan_progress.py) persist
  it on every heartbeat / log / stderr row.
- **Per-row scanner pill in the Live Log panel**
  ([ScanLogPanel.tsx](web/src/components/scans/ScanLogPanel.tsx)).
  Hash-keyed colour from an 8-shade palette so each scanner reads
  as a distinct, consistent colour across reloads. Legacy rows
  (pre-v0.28.2 ingest JWTs without the claim) hide the pill
  rather than showing a misleading one.

### Schema

- New migration `0032_scan_log_scanner_id`:
  - `scan_log_entries.scanner_id UUID NULL` with `ON DELETE SET NULL`
    so deleting a scanner row doesn't cascade out historical logs.
  - Partial index `ix_scan_log_entries_scanner_id` only on
    non-NULL rows.

### Verification

- `pytest tests/`: 674 passed, 1 skipped.
- `npx tsc --noEmit && npx vitest run`: clean (133 passed).
- Scanner Go: `go test ./internal/agent/... ./internal/scanner/...` green.
- Live deploy: api + scanner + web restart healthy; alembic version
  `0032_scan_log_scanner_id` applied; nginx config in the running
  container shows the new ceiling + timeouts.

## v0.28.1 — 2026-05-09

**Online vs reachability — split the concepts; route every credentialed
probe through scanners.** The v0.28.0 redesign collapsed continuous
polling and added on-demand triggers, but it kept a misnaming and a
mechanism mismatch the user surfaced after live use:

- The host `test-connection` endpoint was a TCP-only probe (open a
  socket to host:port). That's an *"is it online?"* check, not
  reachability — the API has no credentials.
- Real reachability — does this scanner have credentials and can it
  list this share — belongs to the scanners. v0.28.0 had the API
  spawn `akashic-scanner test-connection` inline for non-local
  sources and *attribute* the result to each requested scanner, which
  was a UX lie (every scanner got the same row because the API did
  the work).
- `HostAllowedScannersPanel` showed aggregated reach counts but had
  no button to refresh them — there was no API surface to fan out a
  credentialed probe across attached shares × scanners.

### Behaviour changes

- **Online vs reachability vocabulary split.** API does *online checks*
  (TCP, no creds, fast triage). Scanners do *reachability* (credentialed,
  actual share listing). Two words, two surfaces, no overlap.
- **Truly per-scanner credentialed probes.** Every probe — local OR
  remote — routes through the scanner long-poll. The API never spawns
  `akashic-scanner test-connection` again. Each scanner genuinely
  dials the source from its own network position; per-scanner
  attribution in `reachability_results` is now honest.
- **Bulk Test reachability on Hosts.** New "Test reachability" button
  on HostDetail and HostAllowedScannersPanel runs the full grid:
  every attached share × every online scanner permitted to claim
  it. One click, one round-trip per scanner.

### API surface

- **NEW** `POST /api/hosts/{id}/online-check` *(renamed from `/test-connection`)*
  — TCP-only "is the server up?" probe from the API. No credentials,
  no share listing, no `reachability_results` row. Audit event:
  `host_online_check`.
- **NEW** `POST /api/hosts/{id}/test-shares` — bulk fan-out: for each
  attached share, dispatches a credentialed probe to every online
  scanner permitted to claim it. Returns flat per-(source, scanner)
  result rows.
- `POST /api/sources/{id}/test-scanners` — same shape, simplified
  internals: removes the `if src.type == "local"` branch and the
  inline dispatch path. All source types route through
  `probe_dispatch.dispatch_remote`.
- `GET /api/sources/{id}/reachability-summary` — adds
  `last_scanner_name` so badge tooltips can attribute the result
  ("Verified by scanner ebaf7c3c8d36-SMRR") without a follow-up GET.

### Backend internals

- **Deletes** `services/probe_dispatch.dispatch_inline` and the
  `from akashic.services.source_tester import test_connection`
  re-export. The API never has credentials in hand for credentialed
  probes.
- **Updates** `dispatch_remote` to handle the OAuth-token refresh
  that lived in `dispatch_inline`: before publishing to a scanner
  channel, mints a fresh access token for OAuth-shaped sources and
  injects it into `connection_config`. OAuth-grant failures
  short-circuit with a synthetic `step="auth"` per-scanner result
  so the user gets a clear "sign in again" signal without an agent
  round-trip.
- **`_eligible_scanners_for`** now filters to online scanners by
  default (`last_seen_at > now() - 2 minutes`); explicit
  `scanner_ids` overrides the filter so an offline scanner still
  shows up as `pending=true` in the response.

### Frontend

- HostDetail: "Test connection" → **"Online?"** button (tooltip:
  "TCP probe — no credentials"). Adds **"Test reachability"** for
  the bulk fan-out.
- HostAllowedScannersPanel: new **"Test reachability"** button +
  header copy: "Reachability is what each scanner reports —
  credentialed access, not just network ping."
- AllowedScannersPanel + AllowedSourcesModal: header copy clarifies
  "Each scanner probes the source from its own network position
  with its own credentials."
- ReachabilityBadge tooltip attributes the latest result: "Verified
  by scanner X" (when `last_scanner_id` is set) or "Verified by
  scan completion" (implicit per-scan bumps).

### Schema

- **No migration.** Schema unchanged; this release is API surface +
  semantics + UX naming on top of v0.28.0's tables.

### Verification

- `pytest tests/`: 667 passed, 1 skipped.
- `npx tsc --noEmit && npx vitest run`: clean (133 passed).
- Scanner Go tests: green; no agent changes.
- Live deployment: api + scanner restart healthy. `/api/hosts/{id}/online-check` returns 200 on a TCP-reachable host. `/api/hosts/{id}/test-shares` publishes M×N probes to scanner channels and returns per-(source, scanner) results.

## v0.28.0 — 2026-05-09

**Reachability redesign — on-demand only, pubsub-distributed.** The
v0.5.6 continuous-poll subsystem was over-built for a status that
almost never changes. It enqueued a probe per source every 5 minutes,
ran two competing workers (in-process API + scanner agents) racing
for the same `reachability_checks` rows, and produced false-positive
"Stale" badges in the eligibility panels whenever the in-process
worker beat the agent to a probe (verified live: a successful
12,967-file Music scan still showed "Stale" for the scanner). The
scan-lease query also pre-filtered local sources by recent failed
probes — load-bearing on data we're now choosing not to maintain.

The user's framing was right: "source-online" and
"(scanner, source)-can-reach" are different concepts; the latter
only matters when assigning sources to a scanner; both should be
**triggerable, not polled**. The scan IS the strongest reachability
proof we'll ever have.

### Behaviour changes

- **No more continuous reachability polling.** Both scheduler loops
  (`_reachability_enqueue_loop`, `_reachability_self_worker_loop`)
  are deleted. The `reachability_check_enabled` and
  `reachability_check_interval_seconds` settings are dropped.
  Replaced by a daily prune tick that caps the new
  `reachability_results` table at the 20 most recent rows per
  (source, scanner) pair.
- **Probes run on user trigger or implicitly from a successful scan.**
  Three triggers exist: explicit "Test all" / per-row "Test now"
  buttons in the eligibility panels; explicit "Test connection" on
  the Host detail; implicit insertion from a successful scan
  completion (the scanner just walked the source — the strongest
  possible probe).
- **Eligibility panels** (`AllowedScannersPanel`,
  `AllowedSourcesModal`, `HostAllowedScannersPanel`) drop the
  "Stale", "★ Recommended", and "Auto-fill recommended" UX. They
  now show plain checkboxes + a per-row probe state + a small
  history disclosure (5 most recent results as colored dots).
  The doomed-scanner ConfirmDialog is gone — the user chose the
  scanner explicitly; no nanny prompt.
- **Source `is_reachable` / `last_reachable_at` /
  `last_reachability_check_at` columns dropped**, plus the same
  three on `hosts`. State is derived on read from the latest
  `reachability_results` row across all scanners or the latest
  successful scan, whichever is fresher.
- **Removable-disk "Scan now" guard removed.** Without a cached
  reachability flag the guard would always be wrong; the scan
  failure path surfaces "actually offline" cleanly.
- **Scan-lease no longer pre-filters local sources by reachability.**
  `routers/scanners.py:839-843` removed — the filter relied on
  continuous-poll data that no longer exists.

### API surface (changes)

- **NEW** `POST /api/sources/{id}/test-scanners` — runs probes for one
  or more scanners against this source. Inline for non-local
  sources (the API can dial SMB/NFS/S3/etc. directly); long-poll
  dispatched to the scanner over `scanner:{id}:probe` for local
  sources. Results land in `reachability_results`. Returns the
  array of results synchronously; slow scanners come back
  `pending: true` and their results land later via the
  `source:{id}:reachability` pubsub channel.
- **NEW** `POST /api/scanners/{id}/test-sources` — mirror from the
  scanner side, for `AllowedSourcesModal`.
- **NEW** `GET /api/sources/{id}/reachability-summary` — compact
  `{ ok, last_at, last_step, last_error, last_scanner_id }` for
  the badge component.
- **NEW** `GET /api/scanners/{id}/probes/long-poll` — scanner-JWT
  authenticated. Holds the connection up to 30 s waiting for a
  probe to be published; returns 204 on timeout.
- **NEW** `POST /api/scanners/{id}/probes/{request_id}/report` —
  scanner posts the result; persists to `reachability_results` and
  fans out to the per-source frontend channel.
- **REMOVED** `POST /api/sources/{id}/check-reachability` — subsumed
  by `/test-scanners`.
- **REMOVED** `POST /api/scanners/{id}/reachability/poll` — replaced
  by `/probes/long-poll`.
- **REMOVED** `POST /api/scanners/{id}/reachability/{check_id}/report`
  — replaced by `/probes/{request_id}/report`.

### Schema

- New migration `0031_reachability_ondemand`:
  - DROP `reachability_checks` table + its three indexes.
  - DROP `sources.is_reachable / last_reachable_at /
    last_reachability_check_at`.
  - DROP same three columns on `hosts`.
  - CREATE `reachability_results` (append-only history) with one
    composite index on `(source_id, scanner_id, completed_at DESC)`
    so latest-per-pair lookups resolve via index without a sort.
  - Irreversible — the dropped data was ephemeral status.

### Scanner agent

- Replaces the v0.5.7 `/reachability/poll` cadence loop with a
  long-poll loop on `/probes/long-poll`. Each iteration: long-poll
  for one probe (server holds the connection up to 30 s), run it,
  POST the result, repeat. On 204 timeout reconnect immediately;
  on 5xx back off 5 s. Sequential — one probe at a time — to keep
  failure handling simple. The earlier 15 s polling wake-up cost
  is eliminated entirely.

### Frontend

- `AllowedScannersPanel`, `AllowedSourcesModal`,
  `HostAllowedScannersPanel`: stale/recommended UX gone; new
  Test buttons (bulk + per-row); history disclosure inline.
- `ReachabilityBadge`: derives state from
  `/sources/{id}/reachability-summary`; states reduced to
  Reachable / Unreachable / Not yet checked.
- `ReachabilityDot`: dropped `stale` and `stale_unchecked` from
  the type union and color map.
- `SourceDetail`: dropped the `is_removable && !is_reachable`
  Scan-now guard and the legacy "Check now" button.
- `Hosts`: dropped staleness threshold + dot.

### Verification

- `pytest tests/`: 663 passed, 1 skipped.
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 133 passed.
- Scanner Go tests: `go test ./internal/agent/...` green.
- Alembic upgrade head against the live DB applied cleanly; new
  schema verified via `\d reachability_results`.
- Restart cycle: api + scanner come up healthy; scanner handshake
  succeeds; long-poll loop is silent (one heartbeat every 30 s,
  no continuous reachability traffic).

## v0.27.2 — 2026-05-09

**Live Log finished the v0.24.0 audience-scoping job.** v0.24.0 fixed
`POST /api/ingest/batch` to accept the ingest-audience JWT but the
sibling scan-progress endpoints (`/heartbeat`, `/log`, `/stderr`)
kept requiring `get_current_user` (audience=`akashic-api`). The
scanner agent presents the same ingest JWT to all four — so
heartbeats and log/stderr POSTs silently 401'd every call, no log
entries ever reached the database, and the Live Log drawer was
stuck on "Waiting for output…" forever.

### Bug fixes

- **Scan-progress POST endpoints accept the ingest-audience JWT.**
  [api/akashic/routers/scan_progress.py](api/akashic/routers/scan_progress.py)
  swaps `get_current_user` → `get_ingest_user` on `/heartbeat`,
  `/log`, and `/stderr`. The GET `/log` endpoint stays on
  `get_current_user` (the UI fetches log lines for backfill, not
  the scanner). Affected versions: every release since v0.24.0.

### Verification

- End-to-end: a fresh full-scan on the Music source produced 203
  log entries in `scan_log_entries` (vs. 0 on every prior scan
  since v0.24.0) within the first 30 s. Live Log now streams.
- Tests: full pytest 662 passing.
  [test_ingest_token_audience.py](api/tests/test_ingest_token_audience.py)
  gained two regression cases — one asserts access tokens are
  rejected by all three scan-progress POSTs, the other asserts
  ingest tokens are accepted on the happy path.

## v0.27.1 — 2026-05-09

**Two latent bugs that together stopped scans from running.** User
reported "the scanner is not picking up the scan jobs"; live diag
showed 8 SMB scans queued, scanner online + enabled + unrestricted,
yet every `POST /api/scans/lease` returned 401. Fixing the lease auth
exposed a second bug: bulk ingest blew through postgres's hard
32767-param cap on real-world batch sizes, 500-ing every batch and
failing the scan end-to-end.

### Bug fixes

- **Scanner agent: mint a fresh JWT per call.**
  [scanner/internal/agent/agent.go](scanner/internal/agent/agent.go)
  cached one signed bearer header for ~4 minutes and reused it across
  every API call. The API enforces one-time JTI replay protection
  (`services/scanner_jti.py`, review I7 in v0.24.0), so reusing a
  cached token caused every call after the first to 401 with "token
  replay detected". The scanner appeared online (because the rare
  first-of-cycle heartbeat succeeded and bumped `last_seen_at`) but
  never claimed any work. Fix: drop the `jwtCache`, mint per call.
  Ed25519 signatures are microsecond-cheap; the cache was a
  premature optimisation that broke the security guard. Affected
  versions: every release with the I7 JTI guard (v0.24.0 onward).
- **Ingest: chunk the bulk `INSERT ... ON CONFLICT` under the 32767
  param limit.** The new-entry path in
  [api/akashic/routers/ingest.py](api/akashic/routers/ingest.py)
  passed every row from a batch in a single `pg_insert(Entry).values(rows)`
  call. Entry has 34 columns, so any batch over ~990 rows hit
  `asyncpg.InterfaceError: the number of query arguments cannot
  exceed 32767`. Real-world SMB scans send batches in the thousands,
  so every batch 500'd and the scan failed. Fix: chunk rows so
  `cols * chunk_rows ≤ 30 000` (~880 rows per chunk for Entry). Two
  regression tests guard the path.

### Verification

- End-to-end: with both fixes deployed, two real SMB scans completed
  cleanly in seconds — TV (7732 files) and Music (12967 files), zero
  errors.
- Tests: new
  [test_ingest_param_limit.py](api/tests/test_ingest_param_limit.py)
  asserts a 1500-row batch returns 200 and emits ≥2 chunked INSERTs.
  Existing [test_ingest_no_savepoints.py](api/tests/test_ingest_no_savepoints.py)
  still passes (50-row batches stay in one chunk).
- Scanner Go test
  [agent_test.go](scanner/internal/agent/agent_test.go) flipped from
  asserting cache reuse to asserting distinct `jti` claims per
  `authHeader` call.

## v0.27.0 — 2026-05-09

**Settings redesign — kill the sidebar, match the rest of the app.**
v0.25.0 introduced a left sub-sidebar inside `/settings/*`; v0.26.0
polished it. The user kept saying it "feels out of place" — and they
were right. Settings was the only place in akashic with nested
sidebar navigation; every other page (Dashboard, Browse, Search,
Sources, Hosts, Analytics, Admin) renders as a single full-width
pane under the global sidebar. This release reverts the IA mistake
and adopts the convention every modern B2B SaaS uses for sectioned
settings (GitHub, Linear, Stripe, Vercel): top-tab navigation inside
a single `<Page>` wrapper.

### UX

- **Top-tab navigation replaces the nested sidebar.** A single
  `<Page title="Settings">` with an underline-style tab strip below
  (Credentials / OAuth / Scanners / Schedules / Identities / Tags),
  each tab carrying the same icon set introduced in v0.26.0 (shield,
  box, database, clock, user, tag). Active tab gets an accent
  underline; inactive tabs are muted and lift on hover. The strip
  scrolls horizontally on narrow viewports.
- **Each settings sub-page lost its `<Page>` wrapper.** The page
  chrome now lives in the parent `Settings` component once, instead
  of repeating per sub-page. Sub-page descriptions moved inline as
  small muted paragraphs; `<h2 className="sr-only">` keeps screen
  readers correct.
- **`/settings` index now redirects to `/settings/credentials`** (the
  most common connection setting) instead of `/settings/identities`.
- All six existing `/settings/{credentials,oauth,scanners,schedules,
  identities,tags}` URLs are preserved verbatim — bookmarks and deep
  links keep working.

### Removed

- `web/src/components/settings/SettingsLayout.tsx` (the nested-rail
  shell from v0.25.0).
- `web/src/components/settings/SettingsSidebar.tsx` (the grouped
  nav with Connections / Operations / Identity & Access / Data
  Model). The grouping disappears with the sidebar — the flat tab
  strip is enough at six leaves.

### Internal

- New `web/src/pages/Settings.tsx` owns the parent chrome + tab
  strip; renders the active sub-route via `<Outlet />`.
- The OAuth add-provider wizard (v0.25.0) and the host
  credential-profile fix (v0.26.0) are untouched. No backend
  changes in this release.

## v0.26.0 — 2026-05-09

**Host credential-profile bug fix + Settings UX overhaul.**

A credential profile attached to a Host was silently ignored by the
host-level probe and discover-shares endpoints — `/test-connection`
and `/list-shares` passed only `host.connection_config` to the probe,
never layering in `host.credential_profile.credentials`. So a host
with no inline username/password but a profile attached failed with
"no credentials" even though the data was right there. Both endpoints
now route through the existing `merge_host_and_source` helper that
the scan-lease path has been using since v0.5.9.

The settings IA redesign (v0.25.0) shipped the right structure but
felt unfinished — text-only sidebar, raw HTML inputs in one form,
inconsistent loading/error states, no list search, dense Scanners
page, squashed OAuth credentials. This release polishes all of that.

### Bug fixes

- **Host credential profile honoured by probe + share-discovery.**
  POST `/api/hosts/{id}/test-connection` and POST
  `/api/hosts/{id}/list-shares` now layer
  `host.credential_profile.credentials` under `host.connection_config`
  via `merge_host_and_source(host, None)`. Two regression tests
  guard against re-introducing the regression. Affected
  versions: every release since v0.5.9.

### UX

- **Settings sidebar gained icons.** Each leaf (Credentials, OAuth
  providers, Scanners, Schedules, Identities, Tags) renders with an
  icon that matches the main top-level rail's design language.
  Three new icons added to the registry (`clock`, `user`, `tag`).
- **`SectionState` helper standardises loading / error / empty
  states.** Six settings pages each had their own if/else tree with
  different paddings (py-8 vs py-12), different error styling
  (rose-600 div vs Card vs alert), and different empty-state
  patterns. `<SectionState loading? error? empty?
  emptyTitle/emptyMessage/emptyAction>` collapses all three into one
  consistent layout, applied across all six sub-pages.
- **Per-list search.** Credentials (filters by name, type,
  description), Scanners (filters by name, pool, hostname plus an
  "Online only" toggle), and Tags (filters by name) all gained an
  inline search input. Each shows a `"N of M shown"` count when a
  filter is active so the user can tell their search didn't blank-
  render the page.
- **Scanners page restructured into four explicit Card sections.**
  Active scanners (with search) → Pending claims (only renders when
  discovery is on or there are pending requests) → Add a scanner
  (join-token wizard, primary action) → Advanced (manual key, in a
  collapsed `<details>`). Per-row action cluster moved from a
  vertical 3-button stack to inline ghost buttons (Rotate / Enable /
  Delete), with the key fingerprint moved to a tooltip on the
  scanner name.
- **OAuth provider row now two-line with copy buttons.** `client_id`
  and `redirect_uri` each render on their own row in muted-but-
  readable type, with a copy-to-clipboard icon button beside each.
  The redirect URI is exactly what users paste into a provider
  console; the previous single-line `<code>` row was hard to read
  and impossible to copy precisely.
- **Settings → Identities forms moved to the design system.**
  `<input>` and `<select>` raw-HTML fields replaced with `<Input>`
  and `<Select>` from the design system, matching every other
  settings page. The Add-identity form gets its own card with a
  proper heading.
- **Empty states surface the primary action.** Credentials,
  Scanners (Active), and OAuth (Provider apps) empty states now
  render their primary `+ Add` button inside the empty-state card
  itself, not just at the top of the section.

## v0.25.0 — 2026-05-09

**Settings IA redesign + OAuth wizard.** The `/settings` tile grid had
grown to 6 leaves with no visual hierarchy and one round-trip per
navigation; mature B2B tools (GitLab, Sentry, Linear) all use a
two-column grouped sidebar for this. The OAuth page compounded the
problem with three always-shown provider rows whose fields are
identical at every layer.

### UX

- **Two-column Settings layout.** `/settings/*` now renders inside a
  `SettingsLayout` route shell with a grouped left rail
  (Connections / Operations / Identity & Access / Data Model) and the
  active sub-page in the right pane. The flat tile grid on `/settings`
  is gone; the index redirects to `/settings/identities` and existing
  deep links keep working unchanged. The top-level "Settings" sidebar
  link now stays highlighted on every `/settings/*` sub-route (was
  only highlighted on the bare index). On `<md` viewports the rail
  collapses behind a "Menu" button.
- **Add-provider wizard for OAuth.** Pre-fix the page rendered three
  always-visible rows (Google / Microsoft / Dropbox), each prompting
  the user to "Configure" regardless of intent — even though the
  fields are identical at the schema, API, and form level (verified
  against the static registry in `services/oauth_providers.py`). New
  flow: only configured providers appear in the list, and `+ Add
  provider` opens a two-step modal — pick provider type, then fill
  the shared client_id / client_secret / redirect_uri form. Picker
  surfaces the provider's developer-console URL inline so admins know
  where to register the OAuth app. Future provider types (Box, custom
  OIDC) plug in by adding to the backend registry without touching
  the page layout. Existing edit-existing path keeps its modal
  unchanged but reuses the same `ProviderForm` component.

### Internal

- New `components/settings/SettingsLayout.tsx` and
  `SettingsSidebar.tsx`; new `components/oauth/AddProviderWizard.tsx`
  and `ProviderForm.tsx` (extracted from the inline editor in
  `SettingsOAuth.tsx` so both the wizard and the edit modal share it).
- Old `pages/Settings.tsx` (tile grid) deleted.
- No backend changes — the OAuth backend was already provider-agnostic.

## v0.24.0 — 2026-05-08

**Two-round full-codebase security + perf review, all findings shipped.**
Spawned independent reviewers across scanner, API auth, API data, and
web; addressed every Critical, Important, and Notable finding, plus a
self-review of the resulting fixes for shortcuts that were swapped out
for the proper approach in a follow-up.

The largest single change is the **ingest hot path**: pre-fix the
new-entry insert wrapped each row in `db.begin_nested()` (SAVEPOINT +
flush + RELEASE per row, 3000 round-trips on a 1k-new-entry batch).
Replaced with one bulk `INSERT … ON CONFLICT DO NOTHING RETURNING` plus
a four-phase pipeline: stage candidates → bulk insert → side effects
on the returned-success set → bulk re-fetch + treat-as-update for
race losers. Regression test asserts zero SAVEPOINT statements emitted
on the new-entry path. Stale-sweep got the same treatment via a single
`UPDATE … RETURNING` that no longer materialises every soft-deleted
row into the ORM (millions of rows on large rescans).

### Breaking

- **Ingest JWT scoped to its own audience (`akashic-ingest`).** The
  scanner agent's 24h token (`api_jwt` in the lease response, plus
  the `AKASHIC_API_KEY` env in `scan_runner.spawn_scan`) used to be a
  full admin access token. A compromised scanner host could replay it
  against any admin endpoint. Now the token carries `aud=akashic-ingest`
  and only `/api/ingest/batch` accepts it; `decode_access_token` (the
  user-endpoint path) rejects it as audience-mismatched. Out-of-tree
  callers minting their own admin token to call ingest must switch to
  `create_ingest_token` (or `decode_ingest_token` on the verifying
  side). All in-tree callers + 9 ingest test files updated.

### Security

- **Webhook SSRF guard.** `POST /api/webhooks` accepted any URL and
  POSTed from the API's network namespace (private IPs, cloud
  metadata IMDS, `file://`). New `services/url_guard` rejects non-
  http(s) schemes and any hostname that resolves to private,
  loopback, link-local, multicast, or reserved space. Applied at
  schema validation AND dispatch time (defense in depth — covers
  pre-existing rows with cookie-rebound DNS).
- **search_as is admin-only.** The override that swaps caller tokens
  for arbitrary identity claims used to be available to any
  authenticated user; now 403 unless `user.role == "admin"`.
- **Scanner JWT replay protection.** Agent tokens now carry a `jti`
  claim; the API SET-NXs `(scanner_id, jti)` into Redis with a TTL ≥
  the token's remaining lifetime. A captured-on-the-wire token can no
  longer be replayed within its 5-minute exp window. Fail-open on
  Redis outage so a transient cache-tier failure doesn't lock out
  every scanner.
- **`aud` claim required on access tokens.** `decode_access_token`
  passes `audience="akashic-api"` and `require_aud=True`, so a token
  minted for another service that happens to share the HMAC secret
  can no longer be presented here.
- **OAuth callback bound to browser session.** State JWT now carries
  a SHA-256 hash of the initiator's refresh cookie; callback recomputes
  from the actual cookie and rejects on mismatch (timing-safe compare).
  An attacker who somehow obtains a valid state token can't drive the
  flow from a different browser. Callback's `postMessage` target
  origin is now derived from `app.redirect_uri` instead of the wildcard
  `"*"`.
- **OAuth credential-id ownership check.** `POST /api/sources` rejects
  attaching an `oauth_credential_id` already bound to a different
  source (409). Closes the cross-admin credential-hijack vector.
- **Scanner-claim hostname validation + rate limit.** Hostnames
  validated against an RFC1123 regex; `/api/scanners/claim` is now
  rate-limited 5/min/IP. Same shared rate-limiter used by
  `/api/scanners/discover` and `/api/oauth/callback`.
- **CORS allow-list config.** `CORS_ALLOW_ORIGINS` env var (JSON
  list) — empty default means same-origin only. New `cookie_secure`
  setting (default `True`) gates the Secure flag on the OIDC state
  cookie + refresh cookie.
- **LDAP login emits `ldap_login_success` audit event.** Pre-fix
  LDAP-authenticated sessions were invisible to the audit trail.
- **`groups_source` admin-only on PATCH.** A regular user could
  promote their manually-entered binding to the trusted "claim"
  provenance, misleading any UI that gates on confidence.
- **Web access JWT moved out of localStorage.** Stored in a module-
  level variable only; `localStorage` keeps a non-credential
  `session_present` flag that triggers a silent refresh on cold load.
  `silentRefresh` now `clearToken()`s on a 401 from `/auth/refresh`
  so a stale cookie doesn't loop the app on bootstrap.
- **Admin route guards on `/admin/audit` + `/admin/access`.**
  `?next=` capture on 401 so post-login lands back at the original
  destination (validated to start with `/`, no `//`).

### Performance

- **Ingest concurrent-insert race → bulk `INSERT … ON CONFLICT DO
  NOTHING RETURNING`.** No per-row SAVEPOINTs. Regression test
  asserts zero SAVEPOINT statements emitted on a 50-new-row batch.
- **End-of-scan stale-sweep → bulk `UPDATE … RETURNING`.** No more
  loading every soft-deleted row into the ORM. Move detection still
  works against the slim returning rows.
- **End-of-scan move detection → one bulk `content_hash IN (…)`
  query** instead of one SELECT per stale file with a hash. N+1 →
  1 round-trip per batch.
- **OneDrive permission fan-out drains in-flight goroutines on
  cancel.** Walk and WalkShallow no longer leak goroutines holding
  the rate-limit semaphore when scanCtx is cancelled mid-loop.
- **`GET /api/identities` collapsed N+1 binding lookups** into one
  `SELECT WHERE fs_person_id IN (…)`.
- **`PATCH /api/sources/{id}/allowed-scanners`** pre-computes the
  exclusion list once instead of re-running the query inside the
  scanner loop.
- **`subtree_rollup` UPDATE** inlined `path` into the `me` subquery
  so the LATERAL joins compare against a column in scope, not a
  correlated `(SELECT path FROM entries WHERE id = me.id)` per row.
- **Tag re-index + top_children worker reuse the ingest router's
  process-cached engine pool** instead of building + disposing a
  fresh `AsyncEngine` per task.
- **Search SQL fallback now logs the original Meili exception**
  before falling through. Pre-fix a misconfigured Meili turned every
  search into a Postgres ILIKE full-table-scan with no operator
  signal.
- **Search regex mode `SET LOCAL statement_timeout='2s'`** caps
  catastrophic-backtracking patterns server-side.
- **Browse `?include_total=` opt-in** for clients that don't need
  the footer count (default `true` so the SPA's existing badge is
  preserved).
- **OneDrive `do()` accepts `body []byte`** so the 401-retry path
  can rebuild a fresh `bytes.Reader`. Pre-fix the `io.Reader` would
  be drained by the first attempt.

### Correctness

- **Tombstone resurrection — re-appearing files are no longer
  silently search-invisible.** Pre-fix the ingest pre-load picked up
  `is_deleted=true` rows and routed them to `_apply_existing`, which
  un-deleted them but skipped `propagate_to_new_entry` and didn't
  enqueue them for Meili re-indexing. A deleted-then-recreated file
  came back with stale tags and was missing from search until the
  next full rescan.
- **Bulk duplicate-delete — disk delete + DB delete now durable as
  a pair.** Per-iteration `db.commit()` so a crash midway can't
  leave files gone from disk but rows live in postgres.
- **`rebalance_on_move` LIKE escape.** Same `replace(replace(replace(
  …, '\', '\\'), '%', '\%'), '_', '\_') ESCAPE '\'` chain we apply
  in `apply_tag` and `propagate_to_new_entry` — pre-fix a directory
  whose path contained `%` or `_` would leave stale inherited tag
  rows after a move.
- **Ingest refuses unknown `scan_id` (404).** Pre-fix the endpoint
  auto-created a `Scan` row with the client-supplied UUID — a
  compromised scanner could inject arbitrary scan rows with chosen
  UUIDs and corrupt future scan stats. All scans must be pre-created
  via `/api/scans/trigger` or the lease path.
- **Scanner unit terminal-status uses a fresh detached context.**
  `failUnit` / `completeUnit` were called with the cancelled
  `scanCtx` on shutdown and the unit stayed "leased" until the
  60s lease TTL. Now: 5s context rooted in `context.Background()`
  so the API hears the outcome regardless.
- **SMB `Connect` honors its context via `DialContext`.** Pre-fix
  `net.Dial` had no timeout — an unreachable host blocked the
  goroutine for the OS TCP timeout (~3 min default).
- **WebDAV `propfind` re-encodes path segments.** Subsequent BFS
  levels carried URL-decoded paths from previous response bodies;
  strict servers would reject the unencoded request.
- **Connector content-hash prefixes.** Immich now stores `sha1:<hex>`
  and S3 stores `etag:<hex>`, matching the namespace convention used
  by other connectors so cross-source dedup doesn't collide.
- **GDrive size parse zero-guard.** Only sets `SizeBytes` when
  Sscanf returns no error AND `n > 0`; pre-fix any parse failure
  surfaced as a 0-byte file in the dashboard's size buckets.
- **OneDrive `/permissions` now paginates.** Items shared with >200
  users had truncated ACLs.
- **NFS probe stats the configured export path** after the TCP
  reachability check so a probe-OK / scan-fails mismatch surfaces.

### UX

- **`/login?next=` capture on private-route redirect** so deep links
  survive the auth bounce.
- **`useScanStream` rAF callback bails on unmounted component.** No
  more "can't perform state update on an unmounted component" warnings.
- **`ModalShell` traps Tab/Shift-Tab focus + restores on close.**
  `BulkTagDialog` and `JoinTokenWizard` migrated from hand-rolled
  overlays to inherit ESC + focus trap.
- **`AddBindingForm.sourceId` syncs when `sources[]` arrives async.**
  Pre-fix the form submitted `source_id=""` silently if sources hadn't
  finished loading.
- **`SettingsOAuth` ProviderEditor allows updating `client_id` /
  `redirect_uri` without re-entering the (write-only) secret.**
  Backend schema accepts `client_secret: null` on update.
- **`SettingsScanners` join-token status badge** uses `online` /
  `info` / `neutral` instead of always `neutral`.
- **`SettingsSchedules` Save** disabled while in flight to prevent
  double-submit.
- **`Sources` "Scan all" dialog** error-handles + closes on terminal
  state.

## v0.23.0 — 2026-05-07

**Scope cut: home-user storages only.** The connector list narrows
from 15 source types to 10 by removing `ssh`, `box`, `sharepoint`,
`azureblob`, and `gcs`. The four enterprise object-store / sharing-
platform types pull a lot of code (Azure SDK, GCS SDK, Box JWT
helper, Microsoft SharePoint extras) that no home deployment needs;
SSH had grown a substantial sidecar (paramiko-based group resolution,
content-fetch and duplicate-delete branches, scanner CLI dispatch).

After this lands, akashic supports: **local, smb, nfs, s3,
paperless-ngx, immich, webdav, gdrive, onedrive, dropbox**.

- **Connectors removed.** `scanner/internal/connector/{box,sharepoint,
  azureblob,gcs,ssh}.go` (and their `_test.go` companions) deleted,
  along with their five dispatch sites in
  `scanner/cmd/akashic-scanner/{fetch.go,main.go,test_connection.go}`,
  `scanner/internal/agent/{agent.go,unit_runner.go}`, and
  `scanner/internal/probe/probe.go`. `go mod tidy` drops the Azure
  SDK, GCS storage SDK, `golang.org/x/crypto/ssh`, `github.com/pkg/sftp`,
  and the Google API toolchain — about 30 indirect deps gone.
- **Box JWT app-auth gone.** `api/akashic/services/box_jwt.py` and
  `api/tests/test_box_jwt.py` deleted; the
  `mint_access_token_for_source` dispatch in `services/source_oauth.py`
  collapses back to the OAuth-only path. The `box` entry leaves the
  OAuth provider registry; `microsoft` stays (still used by OneDrive).
- **Scanner CLI cleaned up.** `test-connection` no longer accepts
  `--key`, `--known-hosts`, `--account-name`, `--container`,
  `--auth-mode`, `--endpoint-suffix`, or `--gcs-prefix` flags.
- **POSIX group resolution loses its SSH path.** The paramiko-based
  remote-`id -Gn` resolver in `services/group_resolver.py` (and its
  ~50 lines of scaffolding around `_paramiko_client`,
  `_ssh_load_known_hosts`, `_resolve_posix_ssh`) is gone. The
  `paramiko` Python dep is removed from `pyproject.toml`.
- **OIDC auto-provisioning narrowed.** `auth/oidc_provisioning.py`
  drops `ssh` from the posix_uid match set — only `local` and `nfs`
  qualify now.
- **Host & source schemas pruned.** `routers/hosts.py` removes ssh
  from `_HOST_TYPES` / `_DEFAULT_PORTS`; `routers/sources.py` removes
  the four enterprise-cloud entries from `HOSTLESS_SOURCE_TYPES`;
  `schemas/credential_profile.py` narrows `SUPPORTED_TYPES` to the
  three remaining host-attached types.
- **Web form trimmed.** Five Fields components removed. `HostType`
  narrows to `smb | nfs | s3`; the `SshHostFields` helper inside
  `HostFields.tsx` is gone. `SOURCE_TYPES` / `SOURCE_TYPE_LABELS` /
  `HOSTLESS_SOURCE_TYPES` shrink in lockstep across `sourceTypes.ts`,
  `ShareFields.tsx`, `SourceFieldSet.tsx`, `AddSourceForm.tsx`, and
  `AddHostForm.tsx`.

**Breaking change — existing data.** This release does **not** ship a
data migration. `Source.type` is a plain `String` column (not a
Postgres enum), so existing rows with `type IN ('ssh','box',
'sharepoint','azureblob','gcs')` won't break the schema, but every
API path that hits them will return 400 and any scanner lease will
fail. Operators with such rows should clean them up by hand:

```sql
DELETE FROM source_oauth_credentials WHERE source_id IN
    (SELECT id FROM sources WHERE type IN ('ssh','box','sharepoint','azureblob','gcs'));
DELETE FROM sources WHERE type IN ('ssh','box','sharepoint','azureblob','gcs');
DELETE FROM hosts   WHERE type = 'ssh';
DELETE FROM oauth_app_configs WHERE provider = 'box';
```

Upstream infrastructure that survives: `cloud_drive` ACL discriminator
(still used by gdrive / onedrive / dropbox), `entries.native_id`
column (migration 0030), the OAuth foundation (table, callback,
refresh worker), and the `microsoft` OAuth provider entry (shared
with OneDrive).

## v0.22.0 — 2026-05-06

**Scanner perf — parallel permission fetches.** Final follow-up to the
cloud-storage roadmap. The OneDrive and Box connectors used to fetch
ACL metadata one item at a time inside the BFS callback; the v0.15.0
OneDrive code even flagged it explicitly as future work. On a 200-item
fixture with a 50ms-per-request /permissions delay, the OneDrive walk
went from a 10s+ serial baseline to 1.3s with the new fan-out — ~8×
on cloud-drive sources where ACL fetches dominate scan time.

- **OneDrive: bounded fan-out for /permissions.** Walk and WalkShallow
  now buffer each directory's children, fan out the permissions calls
  through a semaphore-bounded worker pool (default 8), and emit
  results in original API response order. ``stats.InaccessibleFiles``
  is incremented in the post-fan-out pass on the main goroutine, so
  no atomic was needed. Children-callback ordering is preserved —
  load-bearing for tag-inheritance and ACL-denorm assumptions.
- **Box: same pattern for /collaborations.** The Box connector hit the
  same shape — one ``/2.0/{files,folders}/{id}/collaborations`` call
  per child serially. Same 8-worker pool, same buffer-and-fanout. Box
  rate-limits at 1000/min/app — well under what 8 workers can
  generate at typical 100-200ms latency.
- **Worker count is constant for now.** Both connectors share the
  same shape but keep separate constants
  (``onedrivePermWorkers`` / ``boxCollabWorkers``) so per-provider
  rate-limit guidance can drift independently. Tunable via a config
  field is on the table; no in-the-wild rate-limit complaint to
  react to yet.
- **Tests.** Added ``TestOneDriveWalkParallelizesPermissionFetches``
  (200 items × 50ms ≈ 1.3s with 8 workers; 3s upper bound) and
  ``TestOneDriveWalkPreservesChildOrder``. Existing OneDrive + Box
  test suites stayed green.

## v0.21.0 — 2026-05-06

**UX polish.** Second of three follow-ups after the cloud-storage
roadmap. Tightens the source-config form (inline validation +
masked-secret hide/show), makes tag mutations feel immediate via
optimistic updates, and surfaces the source association on OAuth
credentials so disconnect is no longer a guessing game.

- **MaskedInput component.** New ``components/ui/MaskedInput.tsx``
  wraps a single-line password-style input with an eye toggle for
  reveal/hide. Hidden by default — shoulder-surf safe and aligned
  with browser autofill UX. Adopted across S3, SMB, NFS (Kerberos
  password), SSH (password + key passphrase), Box (client_secret
  + JWT passphrase), Azure Blob (account_key, SAS token), Paperless
  (api_token), Immich (api_key), WebDAV (password), and HostFields'
  five password fields. Multi-line credentials (GCS service account
  JSON, Box JWT private key) keep their textarea — the eye toggle
  isn't a sensible affordance there and the existing "***"
  unchanged-on-blank pattern handles the edit case.
- **Inline form validation.** New ``hooks/useFieldValidation.ts``
  tracks touched fields and intersects with a debounced live error
  map; AddSourceForm computes per-field errors via the new
  ``validateShareConfigFields`` (parallel to the existing single-
  string ``validateShareConfig`` — both kept) and threads them to
  ShareFields. Local / SMB / NFS / S3 / Paperless / Immich /
  WebDAV / Azure Blob / GCS / SharePoint inputs now flash a red
  border + error text on blur when invalid (URL must start with
  http(s), bucket required, etc.) and clear it 300ms after typing
  resumes. Touched-then-untouched isn't possible — the form-level
  Save-disabled gate still uses the legacy validator so submit
  failure paths are unchanged.
- **Optimistic tag application.** ``EntryTags``' apply/remove
  mutations now write into the entry-detail cache via ``onMutate``
  and revert via ``onError`` if the server roundtrip fails (with a
  toast). The chip flips visually before the network completes;
  ``onSettled`` invalidates so the authoritative state catches up.
  Inherited copies of a removed tag stay (matches the API's "only
  remove direct" semantics).
- **OAuth credentials show their attached source.** The credentials
  endpoint now projects ``source_name`` (eagerly via the existing
  ``lazy="joined"`` relationship — no extra query), and SettingsOAuth
  renders a "Used by: <source name>" badge on each credential row.
  The disconnect ConfirmDialog surfaces the same name in its body
  copy so operators see exactly what'll break before clicking
  through. (The original plan called for a count; the underlying
  schema is 1:1 via migration 0029's partial unique index, so a
  named association is more useful than a 0/1 number.)

## v0.20.0 — 2026-05-06

**Search & browse polish.** First of three releases targeting the
cracks that surfaced after the cloud-storage roadmap closed out.
Closes the search-result → browse navigation gap, surfaces the
existing duplicate-detection work inline on search rows, adds
sort + facet chips on Search, and confirms the folder-count
badges that landed earlier in the Browse tree.

- **"Open containing folder" link in entry detail.** Inside the
  drawer's Identity section, a Folder row links straight back into
  Browse at the entry's parent path. Hidden for orphaned entries
  (source deleted) and for filesystem-root entries — there's
  nothing to open.
- **Inline duplicate-count badge on Search rows.** The Search row
  now renders a "+N copies" Badge when the entry's
  ``content_hash`` is shared with other indexed files in the
  user's permitted source set. Click → ``/duplicates?hash=<...>``,
  which auto-expands the matching group and scrolls it into view.
  ``content_hash`` and ``dup_count`` ride on the SearchHit; the
  count is computed server-side per request rather than indexed
  in Meili (it'd churn whenever any other row's hash mutated).
- **Sort UI on Search.** Results were always relevance-ordered;
  the new dropdown adds Name / Size / Modified with an
  asc/desc toggle. Both the Meilisearch path (sortable attrs
  already configured) and the SQL fallback honor the same sort
  knob; ``?sort=size&order=asc`` survives reload like the rest of
  the URL state.
- **Result-shape facet chips.** Source / MIME / extension are now
  surfaced as click-to-filter chips with hit counts on the Search
  page, alongside the existing Library Metadata facets. They
  were already filterable Meilisearch attrs; the API just wasn't
  asking Meili for the per-bucket distribution. The Source row
  hides itself when the user has already restricted to a single
  source via the dropdown.
- **Folder count badges in Browse.** Already wired in an earlier
  release (we'd missed it during the audit). Direct-child counts
  ride on each directory entry's ``BrowseChild.child_count``; the
  Browse tree renders them as a neutral Badge next to the folder
  name. Re-confirmed and documented here.

## v0.19.0 — 2026-05-06

Box **JWT app-auth** as a second variant alongside OAuth — closes the
last open follow-up from the cloud-storage roadmap. Server-to-server
auth for unattended scanning of an enterprise tenant: operator
generates an RSA keypair in their Box developer console, gets the app
authorized in their Box admin console, and pastes the credentials
into the source form. No user popup, no per-user grant.

- **JWT mint helper.** ``services/box_jwt.py`` builds the
  ``urn:ietf:params:oauth:grant-type:jwt-bearer`` assertion Box's
  token endpoint expects: RS256 signature, ``kid`` header, claims =
  ``iss=client_id`` / ``sub=enterprise_id`` / ``box_sub_type="enterprise"``
  / ``aud=https://api.box.com/oauth2/token`` / random ``jti`` /
  30s ``exp`` window. The exchange POST returns an access token
  with the usual ~60min TTL; akashic re-mints fresh JWTs on each
  scan rather than persisting refresh tokens (Box JWT app-auth
  doesn't issue them).
- **Encrypted private keys.** When the pasted PEM is encrypted, the
  helper decrypts it through ``cryptography``'s
  ``load_pem_private_key`` before signing — both encrypted and
  unencrypted PKCS#8 PEMs work.
- **Unified mint dispatch.** ``mint_access_token_for_source`` (used
  by both the lease path and the scanner-facing refresh endpoint)
  branches on ``connection_config["auth_mode"]``: ``"jwt"`` for Box
  uses the new helper; everything else falls through to the
  existing OAuth path. From the scanner's perspective both auth
  modes are identical — same ``access_token`` injection, same
  ``connector.NewBoxConnector(...)`` consumption.
- **Pre-create probe support.** ``/api/sources/test`` mints from the
  pasted JWT credentials when the source row doesn't exist yet, so
  users get a green "Test connection" before saving a JWT-mode Box
  source — same experience as the OAuth flow's
  ``oauth_credential_id``-based pre-create mint.
- **UI.** ``BoxFields`` grew an "Authentication" toggle (OAuth vs
  JWT). JWT mode renders client_id, client_secret (password),
  enterprise_id, public_key_id, private_key (textarea), and an
  optional passphrase — masked-secret edit handling matches the
  rest of the app (typing replaces, blank leaves unchanged).
- **Subtitle hint.** The Sources card surfaces JWT-mode Box sources
  as ``Box (JWT)`` so admins can see the auth shape at a glance.
- **Tests.** Six new pytest cases (assertion shape + RS256 signature
  verification against a generated keypair, encrypted-PEM
  passphrase decrypt path, missing-field rejection, bad-PEM
  rejection, exchange-success with stubbed httpx, and provider-
  4xx → ``OAuthExchangeFailed``). Full pytest 648/648 green,
  vitest 133/133, ESLint 0 errors, tsc clean, Go build + tests
  green.

This closes out **all** outstanding follow-ups from the cloud-storage
roadmap. Akashic now supports every source type in the original
13-connector plan, with full ACL coverage on the cloud-drive
providers (Drive, OneDrive, SharePoint, Dropbox, Box) and both
auth variants on Box.

## v0.18.1 — 2026-05-06

Fills the v0.17.0 ACL gap on Dropbox sources — explicitly-shared
items now get a real cloud_drive ACL surfaced on the entry-detail
drawer instead of an empty Sharing section.

- **Dropbox sharing-grant ACL.** When a folder's ``sharing_info``
  carries a ``shared_folder_id``, the connector calls
  ``/2/sharing/list_folder_members`` once and builds a cloud_drive
  ACL from the user + group members. When a file has
  ``has_explicit_shared_members=true``, ``/2/sharing/list_file_members``
  does the same. Items not flagged as shared skip the extra calls
  so unshared drives stay fast — most users' Dropboxes have only a
  small fraction of items shared, and the typical walk pays no
  per-file ACL cost.
- **Role mapping.** Dropbox's ``access_type`` maps to cloud_drive
  roles cleanly: ``owner`` → owner, ``editor`` → writer,
  ``viewer`` / ``viewer_no_comment`` → reader. Inherited grants
  preserve the ``inherited`` flag so the ACL renderer styles them
  the way it does for Drive / OneDrive / Box.
- **Pending invitees ignored.** The Dropbox API surfaces pending
  invitees in a separate ``invitees`` slice; we don't include them
  in the ACL since they don't actually have access yet.
- **UI.** The "ACLs aren't yet surfaced" warning that v0.17.0
  shipped under the path-scope input is gone — the connector now
  populates them as expected.
- **Failure mode.** ACL enrichment is best-effort — a transient
  ``list_*_members`` failure swallows quietly and leaves the entry
  with no ACL, rather than failing the whole walk over a sharing
  API hiccup.
- **Tests.** Four new Go tests (access-type role mapping, empty-
  response → nil ACL, shared-vs-private branching that asserts
  ``list_folder_members`` only fires for the shared folder).
  Existing Dropbox tests stay green; total connector tests now
  11 — pytest 642/642, vitest 133/133, ESLint 0 errors, tsc clean,
  Go build + tests green.

## v0.18.0 — 2026-05-06

Tier 4 PR 3 — **Box** as a source type (OAuth variant). Closes out
the planned roadmap for cloud-storage source coverage. The Box
provider config has been in the OAuth registry since v0.12.0, so
this release is the connector + UI on top of the existing OAuth +
cloud_drive plumbing.

- **Box connector.** ``scanner/internal/connector/box.go`` walks
  ``/2.0/folders/{id}/items`` BFS keyed by parent id, paginated via
  Box's offset/limit cursor (limit=200). ReadFile streams from
  ``/2.0/files/{id}/content`` (Box hands back a 302 redirect to
  the storage backend; the http.Client follows). No Box SDK dep —
  plain net/http + JSON.
- **Permissions → cloud_drive ACL.** Per-item ``/collaborations``
  endpoint (``/files/{id}/collaborations`` for files,
  ``/folders/{id}/collaborations`` for folders). Box's role lattice
  maps onto the cloud_drive lattice cleanly:
  - ``owner`` / ``co-owner`` → ``owner``
  - ``editor`` / ``viewer_uploader`` / ``previewer_uploader`` /
    ``uploader`` → ``writer``
  - ``viewer`` / ``previewer`` → ``reader``
  - unknown future roles floor to ``reader`` rather than dropping
    the grant — Box may add new roles over time, and a soft floor
    is friendlier than silently losing visibility.

  Pending invitations (``status != "accepted"``) are skipped — the
  recipient hasn't actually got access yet.
- **Hash flow.** Box returns ``sha1`` inline on file rows; emit
  ``sha1:<hex>`` to fit the existing prefix-tagged
  content_hash vocabulary.
- **Source-type registration.** ``box`` joins
  HOSTLESS_SOURCE_TYPES on both api and web; ``test_box`` runs an
  inline ``GET /2.0/users/me`` against api.box.com; the new
  ``BoxFields`` TSX component hosts the Sign-in popup + optional
  ``folder_id`` scope (Box's All Files root is the literal id "0";
  empty scope walks from there).
- **Tests.** Five new Go connector tests (Connect smoke,
  missing-token guard, BFS walk + path/hash/native_id plumbing,
  paginated walk via offset/limit cursor, role-mapping correctness
  including pending-skip and unknown-role floor). Full pytest
  642/642 green, vitest 133/133, ESLint 0 errors, tsc clean, Go
  build + tests green.

**JWT app-auth roadmap.** The original Tier-4 plan calls for Box's
JWT app-auth as a second variant alongside OAuth — RSA-signed JWTs
for unattended server-to-server scanning of an enterprise tenant
without any user popup. That's a meaningfully different auth shape
(stored RSA private key, JWT minting, no SourceOAuthCredential row)
and ships as a follow-up release rather than crammed into this PR.

This release closes out the planned cloud-storage source coverage
in the sources roadmap: the original roadmap
spanned 13 connectors across 4 tiers, and akashic now supports all
of them — local, NFS, SMB, SSH, S3 (the original lineup), Paperless
+ Immich (Tier 3), Azure Blob + GCS (Tier 2), WebDAV (Tier 4 PR 1),
Drive + OneDrive + SharePoint (Tier 1 PR-C), Dropbox (Tier 4 PR 2),
and now Box (Tier 4 PR 3).

## v0.17.0 — 2026-05-06

Tier 4 PR 2 — **Dropbox** as a source type. OAuth on top of the
foundation that's been in place since v0.12.0; the Dropbox provider
config was wired into the OAuth registry then, so this release is
just the connector + UI on top.

- **Dropbox connector.** ``scanner/internal/connector/dropbox.go``
  walks via ``/2/files/list_folder`` with ``recursive: true``, then
  paginates via ``/2/files/list_folder/continue``. One round-trip
  per page (default 2000 entries) covers the entire subtree —
  substantially fewer calls than Drive's per-folder BFS. ReadFile
  streams from ``/2/files/download`` with the path passed via the
  ``Dropbox-API-Arg`` header per Dropbox convention.
- **Path-based addressing.** Dropbox uses paths as canonical
  identifiers, so the connector emits ``path_display`` directly —
  no synthesized-path machinery, no name-collision suffix
  (Dropbox enforces unique sibling names). The provider id is
  still persisted in ``native_id`` for completeness.
- **Block-based content hash.** Dropbox's ``content_hash`` is a
  proprietary block-based SHA-256 that doesn't equal any standard
  hash, so it's prefixed ``dropbox:`` in the entry record. Dedup
  queries against entries from other sources won't accidentally
  collide it with the standard ``sha256:`` from OneDrive.
- **API and UI.** ``test_dropbox`` runs an inline POST against
  ``/2/users/get_current_account``; ``dropbox`` joins
  HOSTLESS_SOURCE_TYPES; the new ``DropboxFields`` TSX component
  hosts the Sign-in popup + optional path scope. The form
  surfaces a banner explaining that sharing-grant ACLs aren't yet
  populated for Dropbox sources.
- **Known limitation.** This release emits ``acl=null`` for every
  Dropbox entry. Surfacing the per-file member list takes one
  extra API call per shared item and would multiply round-trips
  on the common case (most items unshared); a follow-up adds
  best-effort enrichment via ``list_folder_members`` for
  explicitly-shared folders only.
- **Tests.** Eight new Go connector tests (Connect smoke,
  missing-token guard, single-page walk, paginated walk,
  deleted-entry skip, hash-omitted-when-computeHash-false, 401
  → refresh-and-retry, ReadFile API-arg header). Full pytest
  642/642 green, vitest 133/133, ESLint 0 errors, tsc clean,
  Go build + tests green.

## v0.16.0 — 2026-05-06

Tier 1 PR-C (part 3, complete) — **SharePoint** document libraries as a
source type. Closes out Tier 1: cloud-drive scanning is now end-to-end
for the three providers people actually ask for first (Google Drive,
OneDrive, SharePoint), with the OAuth foundation, native_id +
cloud_drive ACL plumbing, and OneDrive's Graph machinery all reused
underneath.

- **SharePoint connector.** ``scanner/internal/connector/sharepoint.go``
  walks ``/sites/{site_id}/drive[s/{drive_id}]/items/{id}/children`` via
  Microsoft Graph v1.0. Reuses the OneDrive ``driveItem`` /
  ``driveItemPermission`` JSON shapes and the ``buildOneDriveEntry`` /
  ``buildOneDriveACL`` mappers — only the URL prefix and a handful of
  edge cases (site display-name resolution at Connect, drive vs
  drives endpoint toggle) are SharePoint-specific.
- **Multi-library support.** ``drive_id`` is optional in
  connection_config. Empty falls through to the site's default
  document library; sites with multiple libraries set this to the
  specific drive id. ``item_id`` scopes the walk to a folder
  subtree.
- **Site display-name as path root.** ``Connect`` resolves
  ``/sites/{site_id}`` to fetch ``displayName`` and uses it as the
  synthesized path's first segment (e.g.
  ``/Marketing Team/Reports/Q1.pdf``). Falls back to ``name`` then
  ``"SharePoint"`` when the display name is empty.
- **Sign-in flow.** Same Microsoft OAuth flow as OneDrive — the form
  uses the existing ``microsoft`` provider config. Add the
  ``Sites.Read.All`` scope to your Azure App Registration
  alongside the OneDrive ``Files.Read.All`` if you want one client
  to cover both source types.
- **Source-type registration.** ``sharepoint`` joins
  HOSTLESS_SOURCE_TYPES on both api and web; ``test_sharepoint``
  runs an inline ``GET /sites/{id}`` against Graph; the schema
  summary shows the trailing fragment of the colon-triple site id
  rather than the full hostname-comma-guid-comma-guid identifier.
- **Tests.** Five new Go connector tests (Connect smoke,
  missing-token / missing-site_id guards, BFS walk + path
  synthesis with site display name, drive-base toggle between
  default and explicit drive). Full pytest 642/642 green, vitest
  133/133, ESLint 0 errors, tsc clean, Go build + tests green.

Tier 1 is now complete: the OAuth foundation (v0.12.0), native_id +
cloud_drive ACL plumbing (v0.13.0), and Drive / OneDrive / SharePoint
connectors (v0.14.0–v0.16.0) collectively bring akashic from
"filesystem indexer" to "indexer that handles the cloud storage where
most files actually live."

## v0.15.0 — 2026-05-06

Tier 1 PR-C (part 2) — **OneDrive** as a source type, via Microsoft
Graph. Reuses the OAuth foundation (v0.12.0), cloud_drive ACL
(v0.13.0), and scanner-token plumbing (v0.14.0); adds the Graph
endpoint differences and a "Sign in with Microsoft" UI. Both
consumer (personal Microsoft accounts) and work/school (Azure AD)
OneDrive are supported via the same `/me/drive/...` endpoint shape;
the OAuth provider's `/common/` issuer accepts both.

- **OneDrive connector.** ``scanner/internal/connector/onedrive.go``
  walks DriveItems via Graph v1.0 — ``/me/drive/items/{id}/children``
  with ``@odata.nextLink`` pagination, ``$top=200`` per page,
  ``$select`` projection so we don't pull fields we don't use.
  ``ReadFile`` streams from ``/items/{id}/content`` (Graph hands back
  a 302 to a pre-signed storage URL; we follow). No msgraph-sdk
  dependency.
- **Permissions → cloud_drive ACL.** Per-item ``/permissions``
  fetched after listing each child (Graph doesn't ship permissions
  inline). Mapping: ``read`` → reader, ``write`` → writer, ``owner``
  → owner; ``link.scope = anonymous`` → anyone-with-link, ``scope =
  organization`` → domain. Graph's ``inheritedFrom`` populates the
  cloud_drive grant's inherited / inherited_from_id /
  inherited_from_path fields.
- **Hash flow.** OneDrive returns hashes inline on file items —
  prefer ``sha1Hash`` (universally available across consumer +
  business OneDrive), fall back to ``sha256Hash``, then the
  consumer-only ``quickXorHash`` (prefixed ``quickxor:`` to keep
  it visually distinct).
- **Sign-in flow.** "Sign in with Microsoft" mirrors the Drive flow
  — popup → callback → SourceOAuthCredential row created with
  ``source_id=NULL`` → form carries the credential id forward →
  POST /api/sources attaches the credential to the new source row.
  ``test_onedrive`` runs an inline ``GET /me`` against Graph using
  the minted access token.
- **Source-type registration.** ``onedrive`` joins
  HOSTLESS_SOURCE_TYPES on both api and web; SOURCE_TYPE_LABELS
  carries "OneDrive (Microsoft 365)"; a parallel ``OneDriveFields``
  TSX component renders the Sign-in popup + optional ``item_id``
  scope input.
- **Tests.** Six new Go connector tests (Connect smoke,
  missing-token guard, BFS walk + path synthesis, three ACL
  mapping cases for user / anonymous-link / organization-link
  grants, plus role-strongest selection). Full pytest 642/642
  green, vitest 133/133, ESLint 0 errors, tsc clean, Go build +
  tests green.

SharePoint follows next, sharing the OneDrive Graph client and
adding a site-picker step before the folder picker.

## v0.14.0 — 2026-05-06

Tier 1 PR-C (part 1) — **Google Drive** as a source type. End-to-end on
top of the OAuth foundation (v0.12.0) and the cloud_drive ACL plumbing
(v0.13.0). Sign in with Google from the source create flow, paste an
optional folder ID to scope the scan, save — the scanner walks Drive
via the v3 REST API and emits one EntryRecord per file with the
provider's permissions mapped onto the cloud_drive ACL discriminator.

- **OAuth-token-injection at lease time.** When a scan leases for a
  source with a connected SourceOAuthCredential, the API mints a
  fresh access token and drops it into ``connection_config`` as
  ``access_token`` alongside the existing keys. Scans that exhaust
  the access-token TTL mid-walk re-mint via the new
  ``POST /api/scanners/oauth/access-token`` endpoint, gated to
  scanners holding an active lease on the source.
- **Drive connector.** ``scanner/internal/connector/gdrive.go``
  walks Drive via the v3 REST API (no Google Cloud SDK dependency
  — the surface we use is small enough that the SDK's value
  doesn't justify ~100 transitive deps). BFS by parent ID; ``files.list``
  pagination via ``pageToken``; ``files.get?alt=media`` for content
  fetch. Native ID and md5 checksum (when present — Google-format
  docs leave it empty) flow into the entry row. The walker handles
  Drive's name-collision quirk (siblings can share a name) by
  appending `` (id)`` to the second collision and beyond, keeping
  the synthesized display path unique.
- **Permissions → cloud_drive ACL.** Each file's ``permissions[]``
  is mapped principal-by-principal: Drive's ``owner`` /
  ``organizer`` (Shared Drive admin) / ``fileOrganizer`` /
  ``writer`` / ``commenter`` / ``reader`` roles, ``user`` /
  ``group`` / ``domain`` / ``anyone`` principal types. Inherited
  flags survive the round-trip so the entry-detail panel renders
  inherited rows distinctly.
- **Sign-in flow.** "Sign in with Google" from the AddSource form
  opens an OAuth popup (mode=associate). The callback persists a
  SourceOAuthCredential row with ``source_id=NULL``; the form
  carries the credential id forward, and ``POST /api/sources``
  attaches it to the new source row at create time. The
  pre-create "Test connection" path mints an access token from the
  not-yet-attached credential id so the user gets a green probe
  before saving.
- **Source-type registration.** ``gdrive`` joins the hostless source
  registry on both api and web. ``test_gdrive`` runs an inline
  ``about.get`` against drive.googleapis.com using the minted
  access token, with auth/connect step classification matching the
  WebDAV/Paperless probe shape.
- **Tests.** Five new Go connector tests (Connect smoke,
  missing-token guard, BFS walk + path synthesis, name-collision
  disambiguation, cloud_drive ACL build). Four new pytest cases
  (scanner-endpoint auth gate, force-refresh round-trip,
  test-source with not-yet-attached credential, source-create
  attaches credential). Full pytest 642/642 green, vitest 133/133,
  ESLint 0 errors, tsc clean, Go scanner build + tests green.

OneDrive + SharePoint follow next, sharing the OAuth + cloud_drive
plumbing.

## v0.13.0 — 2026-05-06

Tier 1 PR-B — **native_id + cloud_drive ACL plumbing.** The schema and
shared services that the upcoming Drive / OneDrive / SharePoint / Box /
Dropbox connectors all need: an opaque-id column on entries, a fifth
ACL discriminator (`cloud_drive`) with effective-permissions and
denormalization wired up, and the matching scanner Go types. No
connectors yet — those land in PR-C — but ingesting an `EntryRecord`
with `native_id` and a `cloud_drive` ACL now round-trips end-to-end.

- **`entries.native_id`.** Nullable text column for the provider's
  opaque identifier (Drive permissionId, OneDrive driveItem id, Box
  file id, Dropbox path_lower hash). Filesystem connectors leave it
  NULL. A composite `(source_id, native_id)` partial index supports
  the cloud connectors' "resolve provider id → entry row" lookup
  during permission and metadata refreshes.
- **`cloud_drive` ACL discriminator.** Added alongside the existing
  posix / nfsv4 / nt / s3 union. Models the per-principal-grant shape
  cloud drives actually use (no POSIX-style owner+mode+ACE):
  `(principal, role, link?, inherited)` with `principal.type` in
  `user | group | anyone | domain`, `role` in
  `owner | writer | commenter | reader | file_organizer`, and a
  `domain_restricted_to` field that surfaces deployment-level
  external-sharing constraints captured at scan time.
- **Effective-permissions evaluator.** `compute_effective` handles
  the new variant: writer/file_organizer → r/w/d, owner adds
  change_perms, reader/commenter → read-only, anyone-with-link
  matches every principal, domain grants match by email-suffix.
  First match wins — cloud drives have no deny grammar.
- **Denormalization to `viewable_by_*` tokens.** New token vocabulary
  alongside the existing `posix:`, `sid:`, `nfsv4:`, `s3:user:`
  prefixes:
  - `cloud_drive:user:<email-or-id>` for named users
  - `cloud_drive:group:<id>` for groups
  - `cloud_drive:domain:<domain>` for domain-restricted grants
  - `*` (existing ANYONE) for anyone-with-link
  Search filter chips and ACL-aware browse use the existing
  `viewable_by_read && ARRAY[…]` machinery untouched.
- **Scanner Go types.** `EntryRecord.NativeID` and `cloud_drive`
  ACL JSON serialization (`CloudDrivePrincipal` /
  `CloudDriveGrant` / `CloudDriveLink`). PR-C's connectors populate
  these; existing filesystem connectors leave both empty so wire
  format stays backwards-compatible.
- **UI.** New "Sharing" section on the entry detail drawer renders
  the cloud_drive grant list with role chips, inherited rows
  styled subtly, and "anyone with link" highlighted with a public
  badge. The provider's opaque id surfaces under "Hash" as a
  dedicated **Provider ID** row when present.

## v0.12.0 — 2026-05-06

Tier 1 PR-A — **OAuth foundation**. Lays the plumbing the upcoming
Drive / OneDrive / SharePoint / Dropbox / Box connectors all need:
encrypted refresh-token storage, server-side token minting, a callback
route, and a Settings → OAuth providers page where the deployment
owner pastes their own OAuth app credentials. No source-type
connectors yet — those land in PR-C — but the round-trip is fully
verifiable end-to-end via the **Test** button on the providers page.

- **Per-provider OAuth client config.** Settings → OAuth providers
  lists Google, Microsoft, Dropbox, and Box. Configure each with the
  `client_id` / `client_secret` / `redirect_uri` from your own OAuth
  app registration (Google Cloud Console, Azure App Registrations,
  Dropbox app console, Box developer console). Akashic does **not**
  ship a shared OAuth app — first-party trust and the regulatory
  story are cleaner this way.
- **Encrypted-at-rest secrets.** `client_secret` and OAuth refresh
  tokens are stored as Fernet ciphertext, with the symmetric key
  derived from `AKASHIC_SECRET_KEY` via HKDF-SHA-256
  (`services/secret_encryption.py`). Rotating that key invalidates
  every stored OAuth grant — re-run the authorize flow per source
  after rotation.
- **Refresh-token isolation.** Refresh tokens never leave the API.
  When a scanner needs to read from a future Drive/OneDrive/etc
  source, it'll receive a short-lived access token minted on demand
  (`services/source_oauth.mint_access_token`); long scans re-mint
  mid-run. Compromising a scanner host doesn't expose the
  long-lived secret.
- **Test button.** "Test" on each provider row opens the provider's
  consent screen in a popup, runs the full authorization-code
  exchange against the configured client app, fetches the connected
  account's email, and immediately discards the resulting credential
  — proving the round-trip works without leaving a row that nothing
  yet consumes.
- **Connected accounts panel.** Lists OAuth grants the API has
  stored, including the connected account email + access-token
  expiry. The **Refresh** button forces a refresh-token round-trip so
  ops can verify token rotation works against the live provider
  before any scanner connector is wired up.
- **Stateless callback.** State for the authorize → callback round
  trip is a 10-minute JWT signed with `AKASHIC_SECRET_KEY` (no Redis
  dependency); audience and expiry guards are enforced on decode.
- **Schema:** new tables `oauth_app_configs` (per-provider client app)
  and `source_oauth_credentials` (per-source grants — `source_id`
  nullable so PR-A's smoke-test grants can exist before any source
  references them; PR-C ties them to source rows).

Plumbing only — Drive / OneDrive / SharePoint connectors land in
Tier 1 PR-C, after the native-id + cloud-drive ACL plumbing of PR-B.

## v0.11.0 — 2026-05-06

Tier 4 PR 1 — **WebDAV** as a hostless source type. The plan called
this "the highest deploy-count coverage per LOC of any connector,"
and that holds: one PROPFIND-shaped connector covers Nextcloud,
ownCloud, Synology File Station, generic Apache mod_dav, and
sabredav installs.

- **WebDAV as a source type.** Pick "WebDAV" in Add Source. Paste
  the share-root URL (Nextcloud appends
  `/remote.php/dav/files/<user>/`; Synology DSM proxies WebDAV at
  port 5006; mod_dav uses whatever the operator mounted), optional
  basic-auth username + password, and a TLS-verify toggle for
  self-signed home installs. The scanner walks via
  `PROPFIND Depth: 1` BFS — most servers reject `Depth: infinity`
  for safety, so we paginate per directory instead of asking for
  the whole subtree in one shot.
- **Standard PROPFIND props surfaced.** Each emitted entry carries
  size (`getcontentlength`), modified time
  (`getlastmodified`, parsed as RFC1123 with RFC1123Z and RFC850
  tolerance), creation time when present, MIME type
  (`getcontenttype`), and the server's ETag as the content_hash
  (prefixed `etag:` to keep it visually distinct from MD5/SHA
  hashes). Directory entries are detected via
  `<D:resourcetype><D:collection/></D:resourcetype>`.
- **Symlink-cycle protection.** Some servers happily follow
  filesystem symlinks back into already-visited directories. The
  walk maintains a `seen` map keyed by relative path so the same
  directory is never PROPFIND'd twice — the worst a symlink loop
  can do is duplicate one entry's emit, not infinite-loop the scan.
- **Per-directory error tolerance.** A 4xx / 5xx on a single
  subdirectory bumps the scan's `inaccessible_dirs` counter and
  the walk continues — matches the local walker's permission-
  denied semantics. The api surfaces "N inaccessible items
  skipped" on the source's scan history card.
- **Inline httpx test connection.** The "Test" button on Add
  Source hits PROPFIND directly from the api container; auth
  rejection (401/403) → `auth`, method-not-allowed (server doesn't
  speak WebDAV at this URL) → `list`, transport / TLS / DNS →
  `connect`. One round trip, no scanner subprocess.

Internal:

- New scanner connector at `scanner/internal/connector/webdav.go`
  with full Connect / Walk / WalkShallow / ReadFile / Delete. No
  external WebDAV library — the protocol is straightforward enough
  that `encoding/xml` + `net/http` cover it inline. 6 unit tests
  cover PROPFIND XML parsing (including percent-encoded hrefs,
  multistatus dispatch, RFC1123 date parsing), buildWebDAVEntry
  for files vs directories, BFS Walk against a fake server, auth
  rejection, and method-not-allowed handling.
- New probe `runWebDAV` in `scanner/internal/probe/probe.go`
  reuses the connector's Connect for the agent's reachability
  poll loop.
- Three scanner dispatch sites extended (`connectorFromLeased`,
  `buildConnector`, `probe.Run`).
- API: `test_webdav` in `services/source_tester.py` (inline
  httpx); `_summary_for` strips URL scheme;
  `HOSTLESS_SOURCE_TYPES` set extended.
- Web: `WebDAVFields` component with URL / username / password /
  tls-verify; `ShareFields` + `SourceFieldSet` dispatch;
  `SourceType` union + `WebDAVConfig` + validation rule.

Known limitations (v0.11.x follow-ups):

- **No ACL surface.** Standard PROPFIND doesn't return permissions;
  Nextcloud's `oc:permissions` extension exposes an opaque
  permission string, but mapping that into akashic's per-principal
  ACL model is meaningful work — punted to a Tier 4 follow-up if
  there's demand. For now, every WebDAV entry is treated as
  accessible to whoever can reach the source.
- **Bearer / digest / mTLS not supported.** Basic Auth covers the
  vast majority of WebDAV deploys today; bearer and digest auth
  modes can be added as new `auth_mode` values without touching
  the walk path. Client certs likewise wait on real demand.

## v0.10.0 — 2026-05-05

Tier 2 complete — **Google Cloud Storage** as a hostless source type.
With v0.8.1 (S3-compat polish) and v0.9.0 (Azure Blob), this closes
the object-store family: every cloud storage backend a typical user
has is now natively supported.

- **GCS as a source type.** Pick "Google Cloud Storage" in Add
  Source. Fill in bucket, an optional key prefix, and pick an auth
  mode. The scanner walks the bucket via the JSON API
  (`storage.Bucket.Objects`), emits one entry per object with
  size / Updated / MD5 (or CRC32C+gen+size fallback), and follows
  the same prefix-as-folder convention as S3 + Azure Blob so Browse
  navigates hierarchically.
- **Two auth modes.**
  - `service_account_json` — paste the contents of a service account
    JSON key file (Google Cloud → IAM &amp; Admin → Service
    Accounts → Keys → Add key). The form uses a multi-line textarea
    since the JSON is ~2KB; the api masks it on response via the
    existing "json" → "***" scrubber convention.
  - `application_default` — Application Default Credentials. Picks
    up GKE workload identity, `GOOGLE_APPLICATION_CREDENTIALS` env
    var, or `gcloud auth application-default login` creds at scan
    time. The recommended production path — no inline secret.
- **Optional bucket-prefix scoping.** Set a prefix to index a
  subtree only — useful for per-tenant prefixes in shared buckets.
  Empty = whole bucket.
- **HMAC users keep using the S3 type.** GCS exposes an
  S3-compatible XML API at `storage.googleapis.com` for HMAC
  interop access keys. Users who can only get HMAC creds (no
  service account, no workload identity) add an S3 source with
  provider preset "Other" and that endpoint. The GCS connector
  here focuses on the JSON API where it has access to richer
  per-object metadata.

Internal:

- New scanner connector at `scanner/internal/connector/gcs.go` with
  full Connect / Walk / WalkShallow / ReadFile / Delete and
  two-mode auth dispatch. Pinned to
  `cloud.google.com/go/storage@v1.43.0` +
  `google.golang.org/api@v0.190.0` because the more recent
  releases bumped Go minimums to 1.25 (the scanner Dockerfile
  ships golang:1.23-alpine — same constraint that drove the
  Azure SDK pin in v0.9.0).
- New probe `runGCS` in `scanner/internal/probe/probe.go` reuses
  the connector's Connect for the agent's reachability poll loop.
- Three scanner dispatch sites extended (`connectorFromLeased`,
  `buildConnector`, `probe.Run`).
- New CLI flags on `akashic-scanner test-connection`:
  `--gcs-prefix` plus `--bucket` reused. Service account JSON
  arrives over `--password-stdin` so the multi-KB payload
  doesn't bloat `/proc/<pid>/cmdline`.
- API: `test_gcs` in `services/source_tester.py` subprocesses
  the CLI; `_summary_for` renders `gs://<bucket>` (or
  `gs://<bucket>/<prefix>`); `HOSTLESS_SOURCE_TYPES` set
  extended to include `gcs`.
- Web: `GCSFields` component swaps the credential input by
  auth mode (textarea for the JSON key, info banner for ADC);
  `ShareFields` + `SourceFieldSet` dispatch; `SourceType` union
  + `GCSConfig` + `GCSAuthMode` + validation rule.
- Five connector tests cover joinPrefix, ObjectAttrs → EntryRecord
  mapping, MD5/CRC32C hash fallback, two-mode auth validation,
  and missing-bucket / invalid-JSON paths.

**Tier 2 status: complete.** v0.8.1 (S3-compat presets + path_style
override) + v0.9.0 (Azure Blob + 3 auth modes) + v0.10.0 (GCS + 2
auth modes) ship the full object-store family. The remaining
items in the cloud-storage roadmap are Tier 1 (Drive + OneDrive +
SharePoint + the OAuth foundation) and Tier 4 (Dropbox + WebDAV +
Box).

## v0.9.0 — 2026-05-05

Tier 2 PR 2 — **Azure Blob Storage** as a hostless source type. Three
auth modes ship together so this works for both home setups (account
key) and AKS workload-identity production deploys (Azure AD).

- **Azure Blob as a source type.** Pick "Azure Blob Storage" in Add
  Source. Fill in storage account name + container, pick an auth
  mode, paste the matching credential. The scanner walks the
  container via `ListBlobsFlatPager`, emits one entry per blob with
  size / LastModified / ContentMD5 (or ETag fallback) populated, and
  follows S3's prefix-as-folder convention so Browse navigates
  hierarchically.
- **Three auth modes.**
  - `account_key` — Shared Key auth. Paste the access key from
    Azure portal → Storage account → Access keys. Easiest one-off,
    rotates poorly.
  - `sas_token` — Shared Access Signature query string. Paste with
    or without the leading `?` (the connector normalises). Bounded
    lifetime + scoped permissions.
  - `azure_ad` — `DefaultAzureCredential`. Picks up workload identity
    (AKS), managed identity, env vars, or `az login` creds in that
    order at scan time. The recommended production path — no inline
    secret to rotate.
- **Sovereign-cloud support.** New `endpoint_suffix` field defaults
  to `core.windows.net`. Override with `core.usgovcloudapi.net` (US
  gov), `core.chinacloudapi.cn` (China), or `core.cloudapi.de`
  (legacy Germany).
- **Explicit auth-step classification.** Test-connection probes
  (both inline via reachability and via the api's "Test" button)
  classify Azure SDK errors into the standard `auth | connect |
  list | config` taxonomy: `AuthenticationFailed` /
  `AuthorizationFailure` / `InvalidAuthenticationInfo` →
  `auth`; `ContainerNotFound` / container-existence failure →
  `list`; transport / DNS / TLS → `connect`.

Internal:

- New scanner connector at `scanner/internal/connector/azureblob.go`
  with full Connect / Walk / WalkShallow / ReadFile / Delete and
  three-mode auth dispatch. Dependencies pinned to azblob@v1.5.0 +
  azidentity@v1.8.2 + azcore@v1.18.0 — the more recent SDK majors
  bumped Go minimums to 1.25, beyond what the scanner Dockerfile
  ships today (golang:1.23-alpine).
- New probe `runAzureBlob` in `scanner/internal/probe/probe.go` —
  reuses the connector's Connect for the reachability poll loop's
  validation.
- Three scanner dispatch sites extended (`connectorFromLeased`,
  `buildConnector`, `probe.Run`).
- New CLI subcommand args on `akashic-scanner test-connection`:
  `--account-name`, `--container`, `--auth-mode`,
  `--endpoint-suffix`. Auth secret arrives over the existing
  `--password-stdin` plumbing so it never appears in
  `/proc/<pid>/cmdline`.
- API: `test_azureblob` in `services/source_tester.py` subprocesses
  the CLI; `_summary_for` renders `<account>/<container>`;
  `HOSTLESS_SOURCE_TYPES` set extended to include `azureblob`.
- Web: `AzureBlobFields` component swaps the credential input by
  auth mode; `ShareFields` + `SourceFieldSet` dispatch; `SourceType`
  union + `AzureBlobConfig` + `AzureBlobAuthMode` + validation rule.
- Five connector tests cover normalisePrefix, pathSegmentsExcluded,
  buildAzureBlobEntry mapping, three-mode auth-validation paths,
  and missing-required-field paths.

Known limitations (v0.9.x follow-ups):

- **Container ACL / Azure RBAC role enumeration** is not surfaced
  per blob — the connector treats every blob as readable by the
  scanner's caller identity. The wider akashic ACL model assumes
  per-entry grants; mapping Azure RBAC + container-level shared
  access policies into that shape is meaningful work and waits on
  a Tier 4 follow-up.
- **One container per source.** Multiple containers in the same
  storage account today require multiple Source rows with the
  credential typed each time. Promoting Azure Blob to host+source
  (storage account = host, container = source) is a v0.9.x
  follow-up if anyone hits the friction.

## v0.8.1 — 2026-05-05

S3-compat polish — first slice of Tier 2. Akashic's S3 connector
already supported MinIO via the `endpoint` field, but the
hard-coded `UsePathStyle = true` whenever `endpoint` was set broke
Wasabi and Backblaze B2 (both want virtual-hosted-style with their
endpoint URLs). v0.8.1 makes the addressing style explicit and adds
a one-click provider preset.

- **Provider preset dropdown** on the S3 source / host form. Pick
  AWS / MinIO / Wasabi / Backblaze B2 / Other — the preset prefills
  the endpoint placeholder + path-style default + region
  placeholder, and adds a one-line hint about that provider's
  quirks. The dropdown sticks across edits via domain-name
  detection (so a saved Wasabi source still shows "Wasabi" in the
  dropdown when re-opened).
- **Explicit `path_style` override.** New optional boolean on S3Config
  (and the host-shaped variant). Omit for auto (path-style on when
  endpoint is set, off otherwise — matches the legacy behaviour for
  AWS + MinIO). Set explicitly to `false` for Wasabi / B2 with a
  regional endpoint, or to `true` for AWS-shaped URLs behind a
  reverse proxy that needs path-style routing. The toggle stays
  hidden in the UI unless the value is already explicit (i.e., the
  user picked a preset that defines it, or they came from a host
  edit drawer with an existing override) — most users should rely
  on the preset's default.

Internal:

- `S3Connector.SetPathStyle(*bool)` sets the override; nil falls
  back to "true if endpoint is set". `Connect()` now always sets
  `UsePathStyle` explicitly (instead of conditionally inside an
  `if endpoint != ""` block) so the override applies for AWS too.
- `agent.go connectorFromLeased` reads `path_style` from
  connection_config and threads it into the connector.
- `probe.runS3` honours the same override on the reachability poll
  loop's path.
- `scanner test-connection --path-style=auto|true|false` (default
  auto) wires the override through the CLI subcommand. Auto/empty
  preserves the legacy behaviour for callers that don't set the
  flag.
- API `services/source_tester.test_s3` forwards
  `connection_config["path_style"]` as `--path-style` so the "Test"
  button matches what a real scan would do.

## v0.8.0 — 2026-05-05

Tier 3 complete — Immich joins Paperless-ngx as the second self-hosted
library source type. Point akashic at an Immich instance and every
photo/video lands in a chronological browse hierarchy with EXIF, GPS,
faces, and album memberships filterable from Search.

- **Immich as a source type.** Pick "Immich" in Add Source, paste your
  instance URL + an API key (created in Immich under *Account
  Settings → API Keys*), optionally whitelist by album and toggle
  "Include archived". The scanner walks `POST /api/search/metadata`
  with pagination and emits one entry per asset. Hostless like
  Paperless and local — no Host row to manage.
- **`/All Photos/<yyyy>/<mm>/` synthetic hierarchy.** Each asset's
  path is keyed off its `fileCreatedAt` (falling back to EXIF
  `dateTimeOriginal`, then "Undated") so Browse reads chronologically
  regardless of how albums are organised. Album membership is
  surfaced separately as `domain_metadata.album` (multi-valued) so
  filtering by album works without making the path tree fragile to
  album reorganisation. The 8-char asset ID is appended to the
  filename so two photos with the same name in the same month don't
  clobber each other on the unique (source, path) constraint.
- **EXIF / GPS / people / album in the entry detail drawer.** The
  Library Metadata section now surfaces, per asset: Immich ID,
  original filename, original on-disk path, capture timestamp,
  camera make + model, GPS lat/lng, image dimensions, recognized
  people (multi-valued chips), and album memberships (multi-valued
  chips). Each clickable cell jumps Search to assets matching that
  value. The `person`, `album`, `camera_make`, and `camera_model`
  facets are already in `DOMAIN_METADATA_FACET_KEYS` from v0.6.0,
  so the Library Metadata facet panel on Search lights up
  automatically with counts.
- **Album whitelist + "include archived" toggle.** Set the album
  filter (comma-separated, case-insensitive names) to scope the scan
  to a subset — useful for indexing only "Public" albums. Archived
  assets are skipped by default so akashic mirrors Immich's
  hides-from-grid behaviour; flip the toggle to include them anyway.
- **In-process test connection.** Same shape as the Paperless probe:
  the api container hits `/api/server-info/ping` with the api_key,
  classifies 401/403 as `auth`, non-2xx as `list`, transport errors
  as `connect`. One round trip from Add Source's "Test" button.

Internal:

- New scanner connector at `scanner/internal/connector/immich.go`
  with full Walk + Connect + album-membership cache. Connect loads
  every album's asset list to build a per-asset `[]albumName` map so
  the Walk's per-asset lookup is O(1). Five unit tests cover entry
  build, no-EXIF fallback, mock pagination + album mapping, album
  whitelist filtering, and auth rejection.
- New probe `runImmich` in `scanner/internal/probe/probe.go` for the
  agent's reachability poll loop.
- Three scanner dispatch sites extended (`connectorFromLeased`,
  `buildConnector`, `probe.Run`).
- API: `test_immich` in `services/source_tester.py` mirrors the
  Paperless probe pattern; `_summary_for` strips the URL scheme;
  `HOSTLESS_SOURCE_TYPES` set extended to include `immich`.
- Web: `ImmichFields` component, `ShareFields` + `SourceFieldSet`
  dispatch, `HOSTLESS_SOURCE_TYPES` set + `ImmichConfig` type +
  validation rule. AddSourceForm's `Exclude<SourceType, ...>` casts
  extended to keep the host-shaped helpers narrow.

Known limitations (deferred to follow-ups):

- **Content fetch is not wired** for Immich either. Same reason as
  Paperless — the per-entry native-id plumbing lands with the
  Tier 1 CC2 work, after which both connectors get content fetch
  as a one-line change.
- **Album fan-out at Connect.** Loading every album's asset list is
  one HTTP call per album; for libraries with hundreds of albums,
  Connect (and so the "Test" button) takes a few seconds. Streaming
  the membership map mid-scan instead of up-front is a v0.8.x
  follow-up if anyone hits the slowness in practice.
- **People require inline asset shape.** Older Immich versions
  don't return `people` inline on `/api/search/metadata`; per-asset
  resolution via `/api/asset/{id}` would fix this but isn't wired
  in v0.8.0 because the typical install runs a recent enough
  Immich for the inline shape.

## v0.7.0 — 2026-05-05

Tier 3 — Paperless-ngx, the first self-hosted library source type.
v0.6.0 laid the data plumbing; this release makes it user-visible —
point akashic at a Paperless instance and the Browse / Search / entry
detail UI lights up with documents.

- **Paperless-ngx as a source type.** Pick "Paperless-ngx" in Add
  Source, paste your instance URL + an API token (created in
  Paperless under *My Profile → Create Auth Token*), optionally
  whitelist by tag, and akashic walks `/api/documents/` and indexes
  every document. Hostless like local — the URL + token live on the
  source itself, no separate Host row to manage. TLS verification is
  on by default; a per-source toggle handles self-signed home
  installs.
- **Synthetic `/<correspondent>/<document_type>/<year>/` hierarchy.**
  Paperless documents have no native paths, so the scanner builds
  one. *Bank statements* from *Chase* in 2024 land at
  `/Chase/Bank statement/2024/<title>.pdf` and Browse navigates them
  the same as a real filesystem. Documents without a correspondent /
  document_type / created date fall back to `Unsorted`, `Unfiled`,
  `Undated`. Title collisions within the same year overwrite each
  other today; documented in the connector code as a known
  limitation pending the Tier 1 native-id work.
- **Document metadata in the entry detail drawer.** The Library
  Metadata section now surfaces correspondent, document type, tags
  (multi-valued), Paperless ID, archive serial number, original
  filename, and an OCR content preview. Each tag chip and
  metadata cell is a `FilterableCell` — clicking jumps Search to
  documents matching that key/value.
- **Tags are multi-valued search facets.** Paperless library tags
  (e.g., `tax`, `archived`) flow into `domain_metadata.tags` as a
  list. The Meilisearch index now flattens string-array facets so
  each individual tag is independently filterable, and the Library
  Metadata facet panel on the Search page shows the top tags by
  count alongside the existing scalar facets (correspondent,
  document type).
- **In-process test connection.** The "Test" button on Add Source
  hits the Paperless API directly from the api container (httpx, no
  scanner subprocess) so probe latency is one round trip. Auth
  rejection (401/403) → `auth` step; non-2xx → `list`; transport →
  `connect`.

Internal:

- New scanner connector at `scanner/internal/connector/paperless.go`
  with full Walk + Connect + lookup-table caching. Eight unit tests
  cover path synthesis, ancestor emission, tag filtering, mock
  pagination, and auth rejection.
- New probe `runPaperless` in `scanner/internal/probe/probe.go` for
  the agent's reachability poll loop.
- Three scanner dispatch sites updated: `connectorFromLeased`,
  `buildConnector`, `probe.Run`. `agent.go` gains a `boolFromConfig`
  helper and a `splitCommaList` for the new tag_filter input shape.
- API: new `test_paperless` in `services/source_tester.py`,
  `paperless` summary in `schemas/source.py`, `HOSTLESS_SOURCE_TYPES`
  set in `routers/sources.py` so the host-attachment validation
  treats local + paperless uniformly.
- Search: `DOMAIN_METADATA_FACET_KEYS` gains `tags`; `build_entry_doc`
  now flattens string-array values into Meilisearch's filterable
  attributes (each element is a separate filterable token). Filter
  grammar Literal extended in lockstep on web + python.
- Web: `PaperlessFields` component, `ShareFields` + `SourceFieldSet`
  dispatch, `HOSTLESS_SOURCE_TYPES` set + `PaperlessConfig` type,
  `AddSourceForm` switched from per-call `isLocal` to `isHostless`
  so `paperless` skips the host picker and credential override
  panels the same way `local` always has. `LibraryMetadata` gains a
  multi-value chip strip for string-array fields.

Known limitations (deferred to follow-ups):

- **Content preview / fetch from Paperless is not wired.** The OCR
  text appears as a `content_preview` snippet in the entry detail
  drawer's Library Metadata, but `ReadFile` returns "not supported"
  — the scanner has no stable per-entry native id yet, which is
  the Tier 1 CC2 work. When that lands, paperless content fetch is
  a one-line wire-up.
- **Title collisions overwrite.** Two documents with the same title
  in the same correspondent/doc-type/year clobber each other on the
  unique (source, path) constraint. Paperless titles are typically
  date-stamped so collisions are rare; native-id paths fix this for
  good when CC2 ships.
- **Tag whitelist is naive.** Comma-separated case-insensitive
  match. Boolean expressions or "exclude" tags would need UI work
  not in scope for v0.7.0.

## v0.6.1 — 2026-05-05

Bug fix — credential profiles now actually work on the **host edit**
form.

- **Editing a host with a credential profile attached no longer
  demands credentials.** When the user created a host through "Add
  host", picked a saved credential profile, and later opened the host
  to change something unrelated (port, hostname, name), the form
  refused to save with "Username is required" / "Password is
  required". Cause: the create form correctly told the validator and
  field renderer to skip credential checks while a profile was
  attached, but `HostDetail`'s edit flow never tracked the profile
  id, never showed the picker, and never passed the
  "omitCredentials" flag — so the validator saw the masked `"***"`
  sentinels coming back from the API as empty values and rejected
  the form. The edit form now mirrors AddHostForm: it has a
  `<ProfilePicker>`, hides credential inputs when a profile is
  selected, skips credential validation in that case, and sends
  `credential_profile_id` on save so the user can also detach /
  switch profiles inline.

## v0.6.0 — 2026-05-05

Tier 3 foundation — first PR of the cloud-storage roadmap. No
user-facing source types ship in this release; v0.6.0 lays the data
and search plumbing the upcoming Paperless-ngx and Immich connectors
need so those PRs land as small, self-contained connectors instead of
multi-thousand-line beasts.

- **`entries.domain_metadata` (JSONB).** Self-hosted libraries
  surface metadata that doesn't fit POSIX fields — Paperless-ngx
  carries correspondent / document_type / custom_fields per
  document, Immich carries camera EXIF / face / GPS / album per
  asset. Persisted in a new nullable JSONB column on entries with a
  partial GIN index. Filesystem-source rows leave it NULL. Schemaless
  on purpose — the facet UI keys off well-known names; connector-
  emitted keys outside that list still index, they just aren't
  filterable until they join the list.
- **Library Metadata section in the entry detail drawer.** When an
  entry carries domain_metadata, a new section renders below
  Extended attributes. Well-known keys (Correspondent / Document
  type / Person / Album / Camera make / Camera model) render as
  `FilterableCell`s — clicking a value jumps to /search filtered to
  that key/value pair. Other keys render as plain text since they
  aren't predicate-filterable.
- **Library Metadata facet panel on the Search page.** A strip
  above the result list that shows the top values per
  domain_metadata key in the current result set, with counts. The
  panel reads Meilisearch's facet distribution returned by GET
  /api/search (new `facet_distribution` field on the response).
  Clicking a facet chip toggles the predicate in the URL filter
  state. The panel elides itself when no entries in the result set
  carry a known domain_metadata key, so filesystem-only result sets
  see no UI change.
- **`domain_metadata` predicate in the filter grammar.** Added to
  both filter grammar implementations (web + py), the URL serializer,
  the SQL fallback in `to_sqlalchemy()`, and the Meilisearch
  expression in `to_meili()`. Unknown fields decode to no-op on
  stale URLs, matching the existing behaviour for the predicate-
  extension story.

Internal:

- New scanner `EntryRecord.DomainMetadata map[string]any`. Filesystem
  connectors leave it nil; future Paperless-ngx / Immich connectors
  populate it.
- New alembic migration `0028_entries_domain_metadata` — adds the
  column + partial GIN index, both reversible.
- `services/search.DOMAIN_METADATA_FACET_KEYS` is the single source
  of truth for which keys flatten into Meilisearch as filterable
  attributes; mirrored as `DOMAIN_METADATA_FIELDS` in
  `web/src/lib/filterGrammar.ts` and as the
  `DomainMetadataPred.field` Literal in
  `api/akashic/services/filter_grammar.py`. Adding a new well-known
  key is a three-line change in lockstep across those three files.

## v0.5.13 — 2026-05-05

Round 4 of the button affordance audit, after the user reported still
seeing buttons that don't look like buttons. Three independent audit
passes were run in parallel — grep-based pattern sweep, page-by-page
visual walk, and design-system primitive review — surfacing overlapping
but non-identical findings. v0.5.13 ships every HIGH and MEDIUM finding
plus the design-system fix that has the biggest multiplier effect:

- **ConfirmDialog Cancel is now visible.** Cancel was rendered with
  `variant="ghost"` (no border, muted text), so on every confirm
  dialog — delete source, delete profile, delete identity, rotate
  keys, etc. — Cancel blended into the dialog background while the
  primary/danger Confirm pulled focus. Users who wanted to back out
  had to hover to find it. Switched to `variant="secondary"` so it
  reads as a real button paired against the action.
- **Form-submit buttons promoted to `Button`.** Settings → Identities
  Add identity / Add binding, Admin Access File lookup, and ACL
  Effective Permissions Compute were all bare
  `<button class="bg-accent-600 text-white …">` with no focus ring,
  no disabled-loading state. Now use the standard `Button` primitive
  with `loading` prop.
- **Tab strips now signal interactivity at rest.** Inactive tabs in
  SourceDetail (Details / History / Live), ScanLogPanel (Activity /
  Raw stderr), and JoinTokenWizard (shell / docker / compose / k8s)
  used to render as plain muted text. Added `hover:bg-surface-muted/40`
  + `transition-colors` + focus-visible ring so users see hover
  feedback on the whole tab area, plus `role="tab"` / `aria-selected`
  for screen readers.
- **Chip × touch targets.** Filter-chip remove ×, tag-chip remove ×,
  ACL group-chip remove × — all standardized to a 20×20 rounded
  hit target with hover background. Pre-fix the × was a bare
  character with no padding, sub-12×12 effective click area.
- **Inline action links promoted.** FilterChips "Clear all", NfsFields
  "Show advanced options", EffectivePermissions "+ Add group",
  NtACL "Show N inherited entries" — were styled as
  `text-fg-muted hover:underline` body-text links. Now rendered as
  `Button` ghost / secondary so they read as real actions.
- **Drawer close × bigger touch target.** The close × on every
  drawer (Source / Host / Scan log / KeyboardShortcuts help) was a
  20×20 effective target — below WCAG AA's 24×24 minimum. Bumped to
  32×32 with explicit focus-visible ring.

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
