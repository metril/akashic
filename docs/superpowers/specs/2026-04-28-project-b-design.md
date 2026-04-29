# Project B — Self-Service Sources, Live Content, and IA Polish

## Context

Project A (akashic v1) shipped: Go scanner with multi-model ACL capture, Python API, Meilisearch index, identity model, ACL-aware search, audit log, and group auto-resolution across all four connector types. The web dashboard exposes Browse, Search, Sources, Duplicates, Analytics, Settings (Identities), and Admin Audit.

But the dashboard has a real feature gap: **the "Add a source" form is hardcoded to local filesystems only**. SSH/SMB/NFS/S3 sources can only be created via direct API calls. And while the Browse page lists files with their metadata, there's no way to actually open, view, or download a file from the dashboard — you can see the index but not the content. Combined with rough edges in the navigation IA (a flat 7-item sidebar with growing categories that don't group logically), the dashboard is functional but doesn't feel finished.

Project B closes those gaps in four phases:

- **B1: Self-service source creation** — typed forms for all five connector types (local, SSH, SMB, NFS, S3) with field validation and an optional pre-save connection test.
- **B2: Live file view + download** — open file contents from the entry-detail drawer or Browse page; text rendered inline, binary previewed (image/PDF) or download-only. For non-local connectors, the API proxies via the scanner.
- **B3: IA pass** — group the sidebar into logical sections ("Index", "Setup", "Admin"), add a top-level Settings landing page, and tighten breadcrumbs.
- **B4: Visual modernization round 2** — fix remaining round-1 rough edges (spacing, empty states, loading states, color usage). Punt to last because we don't yet know what the new B1/B2/B3 surfaces will need.

Sequencing rationale: B1 first because it's the actual user-blocking gap (you can't even use SMB/NFS/S3 without leaving the UI). B2 second because it's the most visible new capability. B3 third because it depends on knowing the final page set after B1/B2. B4 last because polish should follow shape.

User-locked decisions to date:
- Add SMB/NFS/SSH/S3 source UIs to Project B (this conversation).
- Phase B before any visual round 2 work.

---

## Phase B1 — Self-service source creation

### Goal

Replace the current local-only "Add a source" form on `/sources` with a typed form that supports all five connector types: `local`, `ssh`, `smb`, `nfs`, `s3`. Optional "Test connection" button before save.

### UI shape

A modal or full-page form (decision below) with:

1. **Type picker** — radio group or dropdown: Local / SSH / SMB / NFS / S3. Selection drives which fields show.
2. **Common fields** — Name (required), Scan schedule (optional cron), Exclude patterns (optional list).
3. **Type-specific fields** (conditional, scoped to the scanner CLI flag surface):
   - **Local:** Path.
   - **NFS:** Host, Export path, Mount options (advanced collapsible).
   - **SSH:** Host, Port (default 22), Username, Auth method radio (Password | Private key), Password OR Key path + Passphrase, Known hosts path (required — strict by default to match Phase 14b).
   - **SMB:** Host, Port (default 445), Username, Password, Share name, Domain (optional).
   - **S3:** Endpoint URL (optional, for non-AWS), Bucket, Region, Access key ID, Secret access key.
4. **Test connection button** (optional) — calls `POST /api/sources/test` with the same payload; renders pass/fail with the failing step ("connect", "auth", "list root", "open IPC$"...). Doesn't persist anything.
5. **Save** — `POST /api/sources`, redirect to the created source's detail card.

**Modal vs page:** start with modal (consistent with how identities/bindings are added). If the form grows past ~10 fields per type, split into a dedicated page. SSH and SMB are the largest at ~7 fields — fits a modal.

### Backend additions

- **New** `POST /api/sources/test` — accepts the same body as `POST /api/sources` plus a `dry_run: true` flag. For local/nfs paths, `os.access` check. For SSH/SMB/S3, spawn a scanner subcommand `akashic-scanner test-connection --type=… --host=… …` that connects and lists root, returning JSON `{"ok": bool, "step": "connect"|"auth"|"mount"|"list", "error": "…"}`. Reuse the same stdin-JSON-password convention from Phase 14c so credentials don't leak to `ps`.
- **New scanner subcommand** `test-connection` — minimal: dial → auth → list root entries. Exit 0 on success, 1 on failure with a structured one-line stderr `step:reason`.
- **Schema:** no changes — `connection_config` is already an opaque JSONB blob; the form just produces the right keys per type.
- **Audit:** `source_created` already fires from `POST /api/sources`; `source_test_run` is a new event type emitted by the test endpoint with the type and outcome (no credentials).

### Validation

Client-side per type using the existing form-control primitives. Strict-host-key for SSH (require `known_hosts_path`) is enforced both client-side (required field) and server-side (the existing Phase 14b group-resolution dispatcher will fail later with a clear message if it's missing — but better to catch at create time).

For S3, require either explicit `endpoint` (non-AWS / MinIO) or default to AWS regions; show a tooltip explaining which.

### Why this works

- Fully closes the dashboard feature gap.
- Test-connection is opt-in, so adding it doesn't slow down the happy path.
- Each connector's field surface is small and well-defined by the scanner's existing CLI flags — no new protocol work.

---

## Phase B2 — Live file content view + download

### Goal

Open and download files from the dashboard. Two surfaces:

1. **Browse page row** — a "View" / "Download" overflow menu per file row.
2. **Entry detail drawer** — a Content tab that renders the file inline if it's a previewable type (text < 1MB, image, PDF), otherwise shows a download button.

### Backend

- **New** `GET /api/entries/{id}/content` — streams the file content with `Content-Type` from the entry's `mime_type`. RBAC enforced via `check_source_access(source_id, user, db, "read")`.
- For **local** sources: read directly from the filesystem path the API has access to (the API container would need read access to the source root — this is already the case for local sources since the scanner runs in the same container).
- For **non-local** sources (SSH/SMB/NFS/S3): API spawns scanner subcommand `akashic-scanner fetch --source-id=… --path=…` that opens a connection, reads the bytes, and pipes them to stdout. The API streams stdout to the HTTP response. Same stdin-password convention as B1's test-connection.
- **New** `GET /api/entries/{id}/preview` — for text files only, returns up to N KB of UTF-8-decoded content with charset detection. Avoids loading huge files into JSON. Returns 415 for binary.
- **Cap on download size?** No hard cap, but warn for files > 100MB. The scanner streams chunked; the API forwards. Browser handles the rest.

### Frontend

- **EntryDetail.tsx** — add a "Content" section to the drawer.
  - Text (mime_type starts with `text/`, or `application/json`/`xml`/`yaml`): show first 64KB inline in a monospace block. "View full" link opens new tab to `/api/entries/{id}/content`.
  - Image (mime_type starts with `image/`): `<img src={preview-url}>`.
  - PDF: `<embed>` or download-only.
  - Other: download button.
- **Browse.tsx** — overflow menu per file row: View (opens drawer with Content tab active), Download (triggers `/api/entries/{id}/content` with `?as=attachment=1`).

### Performance and safety

- **Range requests:** the API supports `Range` headers and forwards to the scanner so seeking in large videos works without buffering the whole file. (Local-source path uses `FileResponse` which already handles Range; non-local needs explicit handling in the scanner subcommand.)
- **Concurrent fetches:** scanner subprocess per request; cap to N=10 concurrent at API level via a semaphore. SSH/SMB sessions are short-lived (one fetch per spawn).
- **Content-Disposition:** `inline` by default, `attachment` when `?as=attachment=1`.
- **Defense in depth:** API double-checks `entry.path` is within the source's root before passing to the scanner — no path traversal.

### Why this works

- Reuses the existing scanner connector code (already opens SSH/SMB/etc.) — no new protocol work, just a new CLI subcommand.
- Subprocess-per-request matches Phase 14c's group-resolver pattern, so the integration is well-precedented.
- Streaming avoids memory issues for large files.

---

## Phase B3 — IA pass

### Goal

Group the sidebar's now-7 navigation items into logical sections. Add a top-level Settings landing that points at sub-pages. Make the user's mental model match the project's actual structure.

### Current sidebar (flat)

`Dashboard / Browse / Search / Sources / Duplicates / Analytics / Settings`

Plus Admin Audit appears for admins only via a separate path.

### Proposed sidebar (sectioned)

```
Overview
  Dashboard

Index
  Browse
  Search
  Duplicates
  Analytics

Setup
  Sources
  Settings ▸ (sub-nav: Identities, Tags, Schedules)

Admin (admin-only)
  Audit Log
```

Section headers are unclickable labels in a slightly muted color; the items underneath remain the active links. This pattern fits the existing design system (no new primitives).

### Settings landing page

`/settings` becomes a tiled landing with cards for each sub-area (Identities, Tags, Schedules — Tags and Schedules are placeholders for future sub-pages). Today the only sub-area is `/settings/identities`; the landing page just card-links to it.

### Breadcrumbs

Browse and Sources already use breadcrumbs. Tighten them so the label of the parent section ("Index", "Setup") prefixes deep-link breadcrumbs:

```
Index / Browse / pipeline-test / /tmp / logs
```

### Why this works

- 4 sections × 1–4 items each fits the cognitive load of a 7-item sidebar after a year of growth.
- Section headers are cheap to add (one component, no logic change to active-link state).
- Future pages (Tags, Schedules, Tools) get a clear home.

---

## Phase B4 — Visual modernization round 2

Deliberately scoped after B1/B2/B3 because the surfaces will have changed. Likely items to address based on round 1:

- **Empty states**: every list-or-table page needs a designed empty state (currently most show nothing or a blank cell).
- **Loading states**: a unified skeleton component instead of the mix of spinners, "Loading..." text, and blank cards today.
- **Spacing**: tighten table-row padding, increase card padding for breathing room.
- **Colors**: audit chip/badge color usage so semantic meaning (success/warning/danger/info) is consistent across pages.
- **Icons**: replace the inline SVG path strings in the sidebar with named icon components.

This phase is best planned as a separate design pass once the new shapes are in. Treat the bullets above as the punch list to evaluate against.

---

## Critical files

### Phase B1

**Create**
- `web/src/components/sources/AddSourceForm.tsx` — replaces inline form in `Sources.tsx`.
- `web/src/components/sources/source-fields/{Local,Ssh,Smb,Nfs,S3}Fields.tsx` — per-type field components.
- `api/akashic/routers/source_test.py` — `POST /api/sources/test` endpoint.
- `scanner/cmd/akashic-scanner/test_connection.go` — new subcommand.

**Edit**
- `web/src/pages/Sources.tsx` — drop inline form, mount `<AddSourceForm/>`.
- `api/akashic/services/audit.py` — register `source_test_run` event type (no schema change).
- `scanner/cmd/akashic-scanner/main.go` — wire `test-connection` subcommand dispatch.

### Phase B2

**Create**
- `api/akashic/routers/entry_content.py` — `GET /api/entries/{id}/content` and `/preview`.
- `scanner/cmd/akashic-scanner/fetch.go` — new subcommand for non-local fetches.
- `web/src/components/entry-detail/ContentTab.tsx` — preview + download UI.

**Edit**
- `web/src/components/EntryDetail.tsx` — add Content tab.
- `web/src/pages/Browse.tsx` — add row-level View/Download overflow menu.
- `api/akashic/main.py` — register the new router.
- `scanner/cmd/akashic-scanner/main.go` — wire `fetch` subcommand.

### Phase B3

**Create**
- `web/src/components/Layout/SidebarSection.tsx` — section header primitive.
- `web/src/pages/Settings.tsx` — landing tile page.

**Edit**
- `web/src/components/Layout.tsx` — restructure nav into sections.
- `web/src/App.tsx` — add `/settings` index route.
- `web/src/components/ui/Breadcrumb.tsx` — add optional section prefix.

### Phase B4

TBD — depends on what sticks out after B1–B3 land.

---

## Reuse / existing utilities

- **Modal** primitive (already used by SettingsIdentities for adding bindings).
- **Form controls** (Input, Select, RadioGroup) — already shipped.
- **`subprocess` indirection pattern** in `services/group_resolver.py` (Phase 14c) — same shape works for `_test_connection`/`_fetch_content`.
- **Scanner connector packages** (`internal/connector/{local,ssh,smb,nfs,s3}.go`) — already open and walk all five source types. New subcommands wrap these.
- **RBAC helpers** (`check_source_access`) — applied identically to the new endpoints.
- **Audit service** (`record_event`) — emits `source_test_run` and `entry_content_fetched` events.

---

## Verification (end-to-end per phase)

### B1
1. From the dashboard's `/sources` page, click "Add source", pick SMB. Fill in host/share/user/password. "Test connection" succeeds against a live Samba target.
2. Save. Source appears in the list. Trigger a scan. Files appear in Browse.
3. Repeat for SSH (with key + known_hosts), NFS, S3.
4. Pre-existing local source path still works through the new form.
5. Test a deliberately bad password — UI shows "auth failed" without leaking the password back into the response.

### B2
1. From Browse, click a `.txt` file → drawer opens → Content tab shows the text inline.
2. From Browse row's overflow menu → Download → file downloads with the right filename.
3. From a SMB source, view a 50MB image → `<img>` renders (range requests serving partial content visible in network tab).
4. Try to fetch an entry the user doesn't have RBAC for → 403.
5. Try a path-traversal payload (`?path=../../../etc/passwd`) — rejected before reaching the scanner.

### B3
1. Sidebar shows four sections with matching items.
2. Active-link state still highlights correctly across sections.
3. `/settings` lands on the tile page; clicking Identities goes to `/settings/identities`.
4. Breadcrumbs on Browse start with `Index / Browse / …`.

### B4
TBD per the punch list compiled at start of phase.

---

## Out of scope

- **Mounting source filesystems on the API host.** B2 streams content via the scanner subprocess; we do NOT add `mount.cifs` or similar to the API container. Keeping the kernel mount surface out of the API container is intentional — it'd otherwise need root and CAP_SYS_ADMIN.
- **In-browser file editing.** View and download only. Edit-and-save would need write capabilities on every connector, which doesn't exist yet.
- **Streaming video transcoding.** We serve the file's bytes as-is; the browser handles MIME-type-native rendering. No `ffmpeg` proxy.
- **Source health monitoring** (background reachability checks). Could be a B5 if needed; explicitly not in B1–B4.
