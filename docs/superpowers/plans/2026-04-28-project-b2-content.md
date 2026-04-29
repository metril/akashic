# Phase B2 — Live File Content View + Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** From the dashboard, open and download a file's actual content. Two surfaces:

1. **Browse page row** — overflow menu per file row with View / Download.
2. **Entry detail drawer** — a Content section that previews text inline, renders images, or shows a Download button for binary types.

Backend: a new `GET /api/entries/{id}/content` streams bytes through. For local sources, the API uses `FileResponse` (free Range support). For non-local sources (ssh/smb/nfs/s3), a new scanner `fetch` subcommand opens a connection, reads the file, and pipes bytes to stdout — the API streams stdout to the HTTP response.

A separate `GET /api/entries/{id}/preview` returns a JSON payload with up to N KB of UTF-8-decoded text and charset metadata, so the frontend can render text without loading raw bytes through JSON.

**Out of scope (later phases):** range-request support for non-local sources (the scanner pipes whole-file streams; range-aware scanner CLI is a B2.1 follow-up). In-browser editing. Streaming video transcoding. Mounting source filesystems on the API host.

---

## Architecture

```
Browser
  └── React: ContentTab / Browse row menu
       │
       ▼
api container
  ├── GET /api/entries/{id}/content     (RBAC + dispatch)
  │     ├── kind=local → FileResponse(path)        (Range native)
  │     └── kind!=local → spawn `akashic-scanner fetch …`
  │                       │  (stdin: {"password":"…","key_passphrase":"…"})
  │                       │  stdout = file bytes
  │                       │  stderr = error
  │                       ▼
  │                       StreamingResponse forwarding stdout
  └── GET /api/entries/{id}/preview     (text-only JSON)
        │  (delegates to /content path internally)
        │  reads first N KB, charset-detects, returns:
        │  { encoding: "utf-8", text: "…", truncated: true|false }
        ▼
```

Concurrency is bounded with a semaphore at the API layer (default N=10) so a flood of opens doesn't fork unbounded scanner processes.

---

## File structure

**Create**
- `scanner/cmd/akashic-scanner/fetch.go` — new subcommand.
- `api/akashic/services/entry_content.py` — local + scanner-stream dispatch.
- `api/akashic/routers/entry_content.py` — GET `/api/entries/{id}/content`, `/preview`.
- `api/tests/test_entry_content_endpoint.py` — endpoint tests with mocked subprocess + filesystem.
- `web/src/components/entry-detail/ContentTab.tsx` — preview / download UI.
- `web/src/components/sources/scrubConfig.ts` — utility (move existing inline scrubbing if helpful — or skip).
- `web/src/hooks/useEntryPreview.ts` — useQuery for `/preview`.

**Edit**
- `scanner/cmd/akashic-scanner/main.go` — wire `fetch` subcommand dispatch.
- `api/akashic/main.py` — register the new router.
- `api/akashic/services/source_tester.py` — extract `_scanner_binary_path` and `_run_scanner` helpers into `services/scanner_helpers.py` so entry_content.py can reuse them. **Or** duplicate; the duplication is small and the helpers are stable.
- `web/src/components/EntryDetail.tsx` — add a Content section that mounts ContentTab.
- `web/src/pages/Browse.tsx` — add View / Download per file row.

**No deletes.**

---

## Cross-task spec: response shapes

### `GET /api/entries/{id}/content`

Query params:
- `as=attachment=1` — set `Content-Disposition: attachment` for force-download.

Response:
- 200, body = file bytes, `Content-Type` from `entry.mime_type` (default `application/octet-stream`).
- 403 if user doesn't have read access to the source.
- 404 if entry not found, or entry is a directory.
- 502 if scanner subprocess failed.

### `GET /api/entries/{id}/preview`

Query params: none.

Response (JSON):
```json
{
  "encoding": "utf-8",
  "text": "the first ~64KB of UTF-8-decoded content",
  "truncated": true,
  "byte_size_total": 102400,
  "binary": false
}
```

If the file looks binary (NUL bytes in the first 4KB, or charset detection fails), returns:
```json
{ "encoding": null, "text": null, "truncated": false, "byte_size_total": 102400, "binary": true }
```

200 in both cases. 403/404 same as `/content`.

---

## Task 1 — Scanner CLI fetch subcommand

**Files:**
- Create: `scanner/cmd/akashic-scanner/fetch.go`
- Modify: `scanner/cmd/akashic-scanner/main.go`

Surface:

```
akashic-scanner fetch \
    --type=ssh|smb|nfs|s3|local \
    --host=… --user=… [--port=…] [--share=…] \
    [--known-hosts=…] [--key=…] \
    [--bucket=… --region=… --endpoint=…] \
    --path=/absolute/path/in/source \
    --password-stdin
```

Reads creds from stdin (same `{"password","key_passphrase"}` JSON Phase B1 introduced). Resolves the connector for the given type, calls `Connect` then `ReadFile(path)`, then `io.Copy(os.Stdout, rc)`. Prints nothing else to stdout. Errors go to stderr with `step:reason` classification (`open: …` / `connect: …` / `auth: …`). Exit 0 on success, 1 on failure.

- [ ] **Step 1**: Implement `runFetch(args []string)` per the surface above.
- [ ] **Step 2**: Wire the dispatch in `main.go` (after `resolve-groups` and `test-connection` cases).
- [ ] **Step 3**: Unit tests skipped — the connectors already have read-path coverage (`local_test.go`, `ssh_acl_test.go`, etc.); the fetch wrapper is just `connector.Connect` + `connector.ReadFile` + `io.Copy`. Manual smoke against a local file is sufficient.

**Verify:** `akashic-scanner fetch --type=local --path=/etc/hostname --password-stdin <<< '{"password":""}'` prints the file contents to stdout.

---

## Task 2 — API content + preview service

**Files:**
- Create: `api/akashic/services/entry_content.py`
- Modify: `api/akashic/services/source_tester.py` (extract scanner helpers — see note)

Two helpers in entry_content.py:

```python
def open_local_path(path: str) -> tuple[BinaryIO, int]:
    """Returns (file-handle, total_size). Raises FileNotFoundError /
    PermissionError as you'd expect."""

def stream_via_scanner(
    source: Source,
    relative_path: str,
) -> tuple[Iterator[bytes], int | None]:
    """Spawns 'akashic-scanner fetch …' for the given source+path, returns
    (chunked stdout iterator, content_length_if_known).
    Raises ContentFetchFailed on spawn / non-zero exit."""
```

`stream_via_scanner` constructs argv from `source.connection_config` similarly to how source_tester does it. To avoid duplication, factor `_scanner_binary_path` and the password-stdin payload helper into `services/scanner_helpers.py` and have both source_tester and entry_content import it.

Concurrency: a module-level `asyncio.Semaphore(int(os.environ.get("AKASHIC_FETCH_CONCURRENCY", "10")))` gates `stream_via_scanner`.

Path-traversal defense: before passing `entry.path` to the scanner, normalize the path and re-check it's a sub-path of `source.connection_config["path"]` (for local) or otherwise just a non-`..`-containing path for remote sources.

- [ ] **Step 1**: Extract scanner helpers into `services/scanner_helpers.py`. Update source_tester to use them.
- [ ] **Step 2**: Implement `entry_content.py` with the two helpers and the semaphore.
- [ ] **Step 3**: Path-traversal tests — given a source `connection_config["path"]="/srv/data"` and an entry whose `path="/srv/../etc/passwd"`, the helper rejects with 400.

---

## Task 3 — API endpoints

**Files:**
- Create: `api/akashic/routers/entry_content.py`
- Modify: `api/akashic/main.py`

`GET /api/entries/{id}/content`:

```python
@router.get("/{entry_id}/content")
async def get_content(
    entry_id: uuid.UUID,
    as_: str | None = Query(None, alias="as"),
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    entry = await db.get(Entry, entry_id)
    if not entry or entry.kind != "file":
        raise HTTPException(404)
    await check_source_access(entry.source_id, user, db, "read")
    source = await db.get(Source, entry.source_id)

    disposition = "attachment" if as_ == "attachment=1" else "inline"
    media_type = entry.mime_type or "application/octet-stream"
    headers = {"Content-Disposition": f'{disposition}; filename="{entry.name}"'}

    if source.type == "local":
        return FileResponse(entry.path, media_type=media_type, headers=headers)

    chunks, total = await stream_via_scanner(source, entry.path)
    if total is not None:
        headers["Content-Length"] = str(total)
    return StreamingResponse(chunks, media_type=media_type, headers=headers)
```

`GET /api/entries/{id}/preview`:

```python
@router.get("/{entry_id}/preview")
async def get_preview(...):
    # Same RBAC + entry/source lookup.
    # Read first 64KB.
    # Charset-detect via chardet (existing dep) OR a simple heuristic:
    #   - If first 4KB has NUL bytes → binary.
    #   - Else try .decode("utf-8") → success → encoding="utf-8".
    #   - Else .decode("latin-1") with charset = "latin-1".
    # Return PreviewResponse JSON.
```

Cap the preview at `PREVIEW_MAX_BYTES = 64 * 1024`.

- [ ] **Step 1**: Implement both endpoints.
- [ ] **Step 2**: Register router in main.py.
- [ ] **Step 3**: Tests:
  - Local file content end-to-end (`tmp_path` source).
  - Local preview returns text + encoding.
  - Local preview of binary returns `binary: true`.
  - 404 for entry not found.
  - 404 for entry that's a directory.
  - 403 for user without source access.
  - Path traversal rejected.

**Verify:** `curl /api/entries/$ID/content > out` → file contents downloaded; `curl /api/entries/$ID/preview` returns JSON.

---

## Task 4 — Frontend ContentTab

**Files:**
- Create: `web/src/components/entry-detail/ContentTab.tsx`
- Create: `web/src/hooks/useEntryPreview.ts`
- Modify: `web/src/components/EntryDetail.tsx`

`useEntryPreview.ts`:

```ts
export interface EntryPreview {
  encoding: string | null;
  text: string | null;
  truncated: boolean;
  byte_size_total: number;
  binary: boolean;
}

export function useEntryPreview(entryId: string | null) {
  return useQuery<EntryPreview>({
    queryKey: ["entry-preview", entryId],
    queryFn: () => api.get<EntryPreview>(`/entries/${entryId}/preview`),
    enabled: !!entryId,
    staleTime: Infinity,
  });
}
```

`ContentTab.tsx`:

- Shows for `kind === "file"` only.
- If `mime_type` starts with `text/`, or is `application/json` / `application/xml` / `application/yaml` / `application/javascript`: fetch preview through the auth'd JSON endpoint, render text in a monospace block. Show byte-size + truncated badge.
- Otherwise: a Download button. Fetches the content via auth'd `api.get` as a Blob, then triggers a browser download via `URL.createObjectURL(blob)` + a synthetic `<a download>` click.

Why no inline image/PDF preview: the `<img>`/`<embed>` tags can't carry the `Authorization: Bearer …` header that the rest of the API uses, so they'd 401. Same-origin cookie auth would solve this but is its own feature; deferred.

`EntryDetail.tsx`: add a `<Section title="Content">` immediately after the Identity section, mounting `<ContentTab entry={entry} />`.

- [ ] **Step 1**: Implement the hook + the component.
- [ ] **Step 2**: Wire into EntryDetail.
- [ ] **Step 3**: Smoke test in browser: text file shows preview, image shows inline, PDF embeds, binary shows download button.

---

## Task 5 — Browse row View / Download

**Files:**
- Modify: `web/src/pages/Browse.tsx`

Add per-row overflow menu for `kind === "file"` rows:

- **View** → opens the drawer with that entry selected (existing flow).
- **Download** → `window.open('/api/entries/${id}/content?as=attachment=1')` triggers download.

- [ ] **Step 1**: Add the menu (use a simple disclosure button — no new primitive needed).
- [ ] **Step 2**: Smoke in browser.

---

## Verification (end-to-end)

1. **Backend tests**: `docker compose run --rm api pytest tests/test_entry_content_endpoint.py -v` — all pass.
2. **Local content**: create a local source with a known text file, scan it, then `curl http://api:8000/api/entries/$ID/content` returns the file's content.
3. **Local preview**: `curl /api/entries/$ID/preview` returns JSON with `text` populated and `encoding="utf-8"`.
4. **Binary preview**: same on a `.png` returns `binary: true`.
5. **Path traversal**: forge an entry with `path="/etc/passwd"` against a source rooted at `/tmp` → 400.
6. **Frontend dashboard**:
   - From Browse, click a file row → drawer opens → Content section shows preview.
   - From Browse row overflow → Download → file downloads with the right filename.
   - Image file: `<img>` renders.
   - PDF: `<embed>` renders or browser fallback.
   - Binary: only Download button.
7. **Concurrency cap**: hammer the endpoint with 20 concurrent requests for an SMB-source entry → at most 10 scanner subprocesses simultaneously (visible in `ps aux | grep akashic-scanner`); rest queue.
8. **Auth**: a user without RBAC for the source gets 403.

---

## Out of scope

- **Range requests for non-local sources.** Local has it free via FileResponse. Non-local would need a `--range=start-end` flag on the scanner fetch subcommand and seek-aware ReadFile in each connector. Defer.
- **Editing.** Read + download only.
- **Inline image/PDF preview.** Requires session-cookie auth or signed-URL endpoints since `<img>`/`<embed>` can't carry the `Authorization: Bearer` header. B2 ships with text-inline + download-everything-else; inline media preview lands in a follow-up that adds either a same-site cookie path or a `GET /api/entries/{id}/content-url` short-lived-token endpoint.
- **Bucket/share content fetch.** The scanner subprocess assumes a path. We don't fetch bucket-level metadata via this endpoint.
