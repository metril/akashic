# Duplicates

Akashic detects duplicate files by content hash (SHA-256, computed
during scan) and surfaces them on the **Duplicates** tab.

## What you see

Groups of files that share the same SHA-256, sorted by wasted
bytes (`(count - 1) × file_size`). Expand a group to see every
file in it — paths, sources, sizes, modified dates. Each group's
header shows the per-file size, the count of copies, and the
total reclaimable space.

## Permissions

| Action | Required role |
|--------|---------------|
| View duplicate groups | Any authenticated user (results filtered by source access — you only see groups where you can read at least one of the copies) |
| Bulk-delete copies | Admin |

## Bulk-delete

Within a duplicate group, an admin selects which copy to **keep**;
all other copies are deleted by spawning the scanner's `delete`
subcommand against each entry. Each deletion is audit-logged
([admin.md](admin.md)) with the outcome — `duplicate_copy_deleted`
on success or `duplicate_copy_delete_failed` (with `step` +
`message`) on failure.

The scanner runs as the configured source identity; if that
identity doesn't have delete permission on the underlying file,
the audit row records why and the Duplicates UI surfaces the
failure inline. The keep-copy is never touched.

## Detection caveats

- Hashes are populated during normal scans. Files added between
  scans aren't grouped until the next scan completes.
- The `?min_size=` query param skips groups whose per-file size
  is below the threshold — useful to filter out hash-collision
  noise from empty/near-empty files. There is no default
  minimum; pass `?min_size=1024` to skip files under 1 KB.

## API

| Method + Path | Body / Query | Notes |
|---------------|--------------|-------|
| `GET /api/duplicates?min_size=…&offset=…&limit=…` | — | Group list. `limit` defaults to 50, capped at 200. |
| `GET /api/duplicates/{content_hash}/files` | — | Files in a single group. |
| `POST /api/duplicates/{content_hash}/delete-copies` | `{keep_entry_id}` | Admin bulk-delete. Returns per-file outcomes. |

## See also

- [storage-view.md](storage-view.md) — the Storage tab can surface
  reclaimable space from a different angle (size colouring on
  duplicate-heavy directories often jumps out).
- [admin.md](admin.md) — `duplicate_copy_*` audit events.
