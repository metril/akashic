# Admin tools

This doc covers admin-only features that don't fit the per-area
docs: the audit log, the effective-permissions evaluator, plus
pointer-summaries to admin gates that live elsewhere.

## Audit log

Read-only event stream of permission-relevant actions. Available
to admins at `GET /api/v1/admin/audit` and rendered in the UI's
**Admin → Audit** page.

### Event types

| Event type | Fired by | Payload includes |
|------------|----------|------------------|
| `search_as_used` | Search-as form | query, override (type+identifier+groups), results_count, source_filter |
| `identity_added` | OIDC/LDAP login provisioning | user_id, identity_type, identifier, source_id, confidence |
| `identity_removed` | Settings → Users | same fields |
| `binding_added` | OIDC/LDAP login provisioning | user_id, source_id, identity, confidence |
| `binding_removed` | Settings → Users | same |
| `groups_auto_resolved` | Group resolver service | user_id, source_id, count, strategy |
| `user_role_changed` | Admin → Users, `PATCH /users/{id}`, OIDC/LDAP group mapping | target_user_id, username, old, new, pinned, via |
| `duplicate_copy_deleted` | Duplicates bulk-delete | entry_id, content_hash, scanner_outcome |
| `duplicate_copy_delete_failed` | same | entry_id, content_hash, step, message |

### Query

```
GET /api/v1/admin/audit?user_id=<uuid>&event_type=search_as_used
                    &source_id=<uuid>&from=2026-05-01T00:00:00Z
                    &to=2026-05-31T23:59:59Z
                    &page=1&page_size=50
```

| Param | Default | Notes |
|-------|---------|-------|
| `user_id` | — | UUID. Filter to one user. |
| `event_type` | — | Filter to one type (see table above). |
| `source_id` | — | UUID. Filter to one source. |
| `from` / `to` | — | ISO 8601 datetimes. `from` is aliased on the wire (it's a Python keyword). Naïve datetimes are interpreted as UTC. |
| `page` | `1` | 1-indexed. |
| `page_size` | `50` | Capped at 200. |

### Response

```json
{
  "items": [
    {
      "id": "<uuid>",
      "user_id": "<uuid>",
      "event_type": "search_as_used",
      "occurred_at": "2026-05-03T12:34:56Z",
      "source_id": null,
      "request_ip": "10.0.0.4",
      "user_agent": "Mozilla/...",
      "payload": { ... typed per event_type ... }
    }
  ],
  "total": 1234,
  "page": 1,
  "page_size": 50
}
```

`GET /api/v1/admin/audit/{event_id}` returns a single event by id.

### Retention

Default: events kept forever
([`AUDIT_RETENTION_DAYS=0`](configuration.md#audit)). Set the
env var to N to enable a daily prune of events older than N
days. Useful for deployments under retention policies, or just
for keeping the audit table from growing unboundedly.

## Effective permissions evaluator

`POST /api/v1/entries/{entry_id}/effective-permissions` answers
"can this principal do X on this entry?" against the entry's
full ACL, its base POSIX mode (`uid` / `gid` / `mode`), and any
source-level security metadata (e.g. an S3 bucket policy).

### Request

```json
{
  "principal": {
    "type": "sid",
    "identifier": "S-1-5-21-…",
    "name": "alice"
  },
  "groups": [
    {"type": "sid", "identifier": "S-1-5-21-…-513", "name": "Domain Users"}
  ],
  "principal_name_hint": "alice"
}
```

Principal `type` is one of: `posix_uid`, `sid`, `nfsv4_principal`,
`s3_canonical`. `groups` is a list of the same shape. `name` and
`principal_name_hint` are optional and only used for richer
human-readable explanations in the response.

### Response

```json
{
  "rights": {
    "read":          {"granted": true,  "by": [{"ace_index": 0, "summary": "Allow read to Domain Users"}]},
    "write":         {"granted": false, "by": []},
    "execute":       {"granted": true,  "by": [{"ace_index": -1, "summary": "POSIX base mode owner perms"}]},
    "delete":        {"granted": false, "by": []},
    "change_perms":  {"granted": false, "by": []}
  },
  "evaluated_with": {
    "model": "nt",
    "principal": {"type": "sid", "identifier": "S-1-5-21-…", "name": "alice"},
    "groups":    [{"type": "sid", "identifier": "S-1-5-21-…-513", "name": "Domain Users"}],
    "caveats":   []
  }
}
```

Each right has a `granted` flag plus a `by` list of `ACEReference`s
explaining *which* ACE granted (or denied) it. `ace_index = -1`
means a synthetic ACE — typically the POSIX base mode's
owner/group/other permissions, which don't exist in the on-disk
ACL list.

`evaluated_with.model` reports which permission model decided the
result: `posix`, `nfsv4`, `nt`, `s3`, or `none` (no ACL — fell
back to source-level access).

`caveats` lists assumptions that weakened the determination
(e.g. "S3 bucket policy not fetched — ACL-only evaluation").

### When to use it

Useful for debugging "Alice can't see file X but I think she
should" — feed Alice's principal + groups, get the bit-level
verdict, and diff against what the entry's ACL actually says. The
**Search-as** form ([search-and-browse.md](search-and-browse.md))
is a similar tool but operates over the *whole result set*, not
a single entry.

The entry-detail drawer (right-side panel) calls this endpoint
when expanding the **Effective permissions** row, so you usually
don't need to hit it directly.

## Mirror links

An Immich or Paperless-ngx source indexes the app's API; a
`local`, `nfs`, `smb` or `s3` source scanning the app's storage
sees the same bytes again. A **mirror link** maps the app source
onto the on-disk backing source so each file appears once: the app
entry is primary, the on-disk files are its physical backing.

Mirrors are configured on the **app source** (Sources → Immich or
Paperless → **Files on disk**, in both the add and edit forms, or
the CLI below), where the library is defined. `mirrors` is a list
of mappings in the app source's `connection_config`; the backing
source's type decides the rules. Disk sources show a read-only
**Mirrored by** list.

```json
{"mirrors": [{"source_id": "<backing-source-uuid>",
              "remote_prefix": "/data",
              "local_prefix": "/host/mnt/immich/data"}]}
```

- `mirrors` is accepted only on `immich` and `paperless` sources;
  any other type gets 400, including an empty list. `mirror_of` on
  a backing source is rejected with 400.
- **Immich**: `remote_prefix` is where Immich sees its files
  (`originalPath` is reported as `/data/...`); `local_prefix` is
  the same directory in the backing source. Originals link by
  path; `thumbs/` and `encoded-video/` files link by asset id;
  `<original>.xmp` links as `sidecar`.
- **Paperless-ngx**: filenames are relative, so `remote_prefix` is
  empty: `{"source_id": "<backing-uuid>", "remote_prefix": "",
  "local_prefix": "/host/mnt/paperless/media/documents"}`.
  `originals/<media_filename>` links as `original`,
  `archive/<archive_media_filename>` as `archive`,
  `thumbnails/<id>.webp|.png` as `thumbnail`; non-root document
  versions link to the root document as `version`.
- **s3**: the rule follows the backing type; `local_prefix` is a
  key prefix with no leading slash.
- Invalid mappings (missing backing, wrong backing type, malformed
  prefix, duplicate backing) are rejected with 400/404.
- **Archived assets**: Immich archived assets are only indexed
  with `include_archived`; enable it on mirrored Immich sources or
  their files stay unlinked (and visible).
- **External libraries** outside `/data`: add another mapping on
  the same app source, with its own prefixes. A backing file links
  to at most one app entry; when several apps cover it, priority
  is app name, then list order.
- Saving relinks only the backings whose mapping changed.
  Renaming an app with mirrors relinks its backings. Deleting a
  backing source strips it from every app's `mirrors` (audited).

Files that do not match any rule stay unlinked and fully visible.

### CLI

```
akashic sources mirror set <app> --backing <src> [--remote-prefix /data] --local-prefix /host/mnt/immich/data
akashic sources mirror clear <app> [--backing <src>]
akashic sources mirror show <app>
```

### When links are computed

The link pass runs after every completed scan of either side, when
a mapping changes (in the background), and when a backing source
is deleted (its mapping is stripped from the apps, then a background relink). It
is idempotent and clears links whose target no longer matches.
Trashed or deleted assets leave their files unlinked.

Effects elsewhere: [search-and-browse.md](search-and-browse.md)
(hiding, `link` filter, badge), [storage-view.md](storage-view.md)
(logical views count 0 bytes), [duplicates.md](duplicates.md).

## Other admin gates (cross-references)

| Feature | Required role | Where | Doc |
|---------|---------------|-------|-----|
| Risk colour mode | Admin (UI gate) | Storage page → colour-mode toggle | [storage-view.md](storage-view.md) |
| Tag create / apply / remove | Admin (API gate) | Search results checkbox + Settings → Tags | [tags.md](tags.md) |
| Duplicate bulk-delete | Admin (API gate) | Duplicates page | [duplicates.md](duplicates.md) |
| Show-all in Browse | Admin (UI gate) | Browse → top-right toggle | [search-and-browse.md](search-and-browse.md) |
| Search-as principal impersonation | Any user (bounded by source access) | Search page → **Search as…** | [search-and-browse.md](search-and-browse.md) |
| First-user-is-admin | — | Initial registration at `/api/v1/users/register` | [authentication.md](authentication.md) |
| Scanner provisioning (token / discovery / manual) | Admin | Settings → Scanners | [README.md](../README.md#scanners) |
| Webhook visibility (admin sees all sources) | Admin's webhooks fire on all sources | API only — no UI yet | [webhooks.md](webhooks.md) |
