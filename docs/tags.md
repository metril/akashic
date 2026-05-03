# Tags

Tags are short labels (e.g. `archive`, `fy26`, `phi-restricted`)
applied to files or directories. Tags applied to a directory
**inherit to every descendant**.

## Model

- A `Tag` is a unique name + optional colour + creator.
- An `EntryTag` row links a tag to an entry. If the link came
  from inheritance, `inherited_from_entry_id` points at the
  ancestor directory whose tag cascaded down.
- Tags are searchable (the Meili `tags` field is updated when
  tags change) and filterable via the `tag` chip predicate —
  see [search-and-browse.md](search-and-browse.md).

## Permissions

| Action | Required role |
|--------|---------------|
| List tags + read an entry's tags | Any authenticated user |
| Create catalogue entry, apply, remove | Admin |

## Inheritance semantics

Applying tag `X` to directory `D`:

1. Inserts `EntryTag(entry_id=D, tag=X)` (the *direct* row).
2. Inserts `EntryTag(entry_id=child, tag=X, inherited_from=D)`
   for every descendant of `D`, computed by a single path-based
   SQL `INSERT … SELECT`.

Removing tag `X` from directory `D` removes the direct row and
**cascades** to every inherited row pointing at `D`. Descendants
that were *also* directly tagged keep their direct rows.

Files added under `D` after the original apply do **not** inherit
unless a re-apply runs. If you've added files since the last tag
operation, run a re-apply to catch them — currently a manual
operation through the UI's bulk-apply.

## API

| Method + Path | Body / Query | Notes |
|---------------|--------------|-------|
| `POST /api/tags` | `{name, color?}` | Pre-create catalogue entry. Admin. Apply endpoints auto-create too. |
| `GET /api/tags` | — | List catalogue. |
| `GET /api/tags/usage` | — | Per-tag usage stats (direct + inherited counts). |
| `DELETE /api/tags/{tag_id}` | — | Delete catalogue entry + every applied/inherited row that referenced it by name. Admin. |
| `POST /api/entries/{entry_id}/tags` | `{tags: [...]}` | Apply one or more tags to an entry; cascades for directories. Admin. |
| `DELETE /api/entries/{entry_id}/tags/{tag}` | — | Remove a directly-applied tag. Inherited copies sourced from this entry cascade-delete. Admin. |
| `GET /api/entries/{entry_id}/tags` | — | List an entry's direct + inherited tags. |
| `POST /api/tags/bulk-apply` | `{entry_ids: [...], tags: [...]}` | Apply each tag to every entry. Admin. Used by the Search UI's "Tag selected (N)" action. |

## UI surfaces

| Where | What |
|-------|------|
| Search results | Per-row tag chips; bulk-apply via the **Tag selected (N)** button (admin); checkbox column shows up automatically when admin. |
| Browse | Tag chip filter on the result list. |
| Entry detail drawer | Lists direct + inherited tags side by side; admin can add/remove inline. |
| **Settings → Tags** | Catalogue management with usage stats. |

## Operational tips

- Tagging a million-descendant directory is a real (but bounded)
  cost — a single SQL insert plus a background Meili re-index for
  every affected file. The bulk-apply dialog warns you before
  submission when any selected entry is a directory.
- Catalogue deletion is **global** and cascading. Confirm twice.
- Tag names are free-form strings; pick a convention (kebab-case,
  prefixes like `org-`, `lifecycle-`, etc.) before opening
  tagging up to admins.
