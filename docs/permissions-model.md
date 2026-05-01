# Permissions model

How Akashic decides what a logged-in user can see, end-to-end. Read
this if you're configuring an OIDC IdP, debugging "user X says they
can't see file Y", or extending the access-control surface.

## The pre-trim guarantee

Akashic indexes file ACLs at scan time and projects them into a
canonical token vocabulary. At query time, the API filters every list
endpoint (Browse, Search, Analytics aggregates) against the caller's
token set **before** results are returned to the SPA. The web layer
never receives entries the user can't see.

This is the SharePoint pre-trim model. Comparable products that miss
this end up with one of two failure modes:

- **Post-trim** (filter in the UI) — the API leaks file existence, and
  any tool that talks to the API directly bypasses the trim.
- **Permission joins at render time** — every directory listing
  becomes O(N principals × M files), and a real ACL evaluation in the
  browser is impossible.

Akashic does the projection once, at scan time, into the array columns
described below. The runtime cost of "can this user read this file?"
is one Postgres array-overlap (`&&`) on a GIN index per request — fast
enough that we apply it on every Browse listing without thinking about
it.

## Token vocabulary

Every principal that can hold a permission is represented as one of:

| Token | Meaning | Example |
|---|---|---|
| `posix:uid:N` | POSIX user id | `posix:uid:1001` |
| `posix:gid:N` | POSIX group id | `posix:gid:100` |
| `sid:S-…` | Windows / AD security identifier | `sid:S-1-5-21-1234-5678-9012-1001` |
| `nfsv4:NAME` | NFSv4 user principal | `nfsv4:alice@EXAMPLE.COM` |
| `nfsv4:GROUP:NAME` | NFSv4 group principal | `nfsv4:GROUP:engineers@EXAMPLE.COM` |
| `s3:user:ID` | S3 canonical user id | `s3:user:abc...` |
| `*` | Anyone (world-readable) | `*` |
| `auth` | Any authenticated principal | `auth` |

The vocabulary is fixed at
[`api/akashic/services/acl_denorm.py`](../api/akashic/services/acl_denorm.py).
Adding a new principal kind requires touching that file (the projection
function), the array columns (no schema change — the column type is
`TEXT[]`), and the binding-to-token translator at
[`api/akashic/services/access_query.py`](../api/akashic/services/access_query.py).

## The projection: `entries.viewable_by_*`

Every entry has three array columns, GIN-indexed:

```
viewable_by_read   TEXT[]   — tokens that grant read on this entry
viewable_by_write  TEXT[]   — tokens that grant write
viewable_by_delete TEXT[]   — tokens that grant delete (NFSv4 / NTFS only)
```

Populated at ingest by `compute_viewable_buckets(acl, mode, uid, gid)`
in [`api/akashic/services/ingest.py`](../api/akashic/services/ingest.py).
The same function feeds Meilisearch's filterable fields — single
source of truth, both sinks always agree.

### POSIX `delete` is intentionally empty

POSIX delete depends on the parent directory's mode, not the file's.
Computing it per-entry would be wrong. NFSv4 and NTFS ACLs do encode
delete-on-the-entry semantics; those columns are populated for those
sources.

### How the projection handles wildcards

- A POSIX `o+r` mode bit produces `*` in `viewable_by_read`.
- An NTFS ACE granting Authenticated Users produces `auth`.
- An ACE granting Everyone (S-1-1-0) produces `*`.

The Phase-7 dashboard's "public-readable files" tile counts entries
where `'*' = ANY(viewable_by_read)` — that's the operational signal
admins want.

## How the user's token set is computed

For each logged-in user, `user_principal_tokens(user, db)` walks every
`FsBinding` row and emits the tokens. A binding is one of:

- `posix_uid` — the user's POSIX identity on a specific source. Emits
  `posix:uid:<identifier>` plus `posix:gid:<g>` for every cached
  group.
- `sid` — the user's AD SID on a specific source. Emits `sid:<...>`
  plus `sid:<group>` for every cached group SID.
- `nfsv4_principal` — emits `nfsv4:<...>` and `nfsv4:GROUP:<...>`.
- `s3_canonical` — emits `s3:user:<id>`.

Every user implicitly carries `*` and `auth` in their token set
(everyone is "anyone" and every authenticated session is
"authenticated"). The Browse / Search filter is a plain Postgres array
overlap:

```sql
WHERE viewable_by_read && ARRAY['posix:uid:1001', 'posix:gid:100', '*', 'auth']::text[]
```

A user with no bindings keeps see-all behaviour until an admin
attaches one — the feature flag is a deployment switch, not a per-user
lockout. See `BROWSE_ENFORCE_PERMS` below.

## Per-source `principal_domain` config

When OIDC issues a SID claim, Akashic needs to know which source(s)
the SID applies to. AD federation is multi-domain in real deployments;
without scoping you'd attach a domain-A SID to a domain-B share.

Each source's `connection_config` accepts a `principal_domain` key —
the SID prefix shared by all principals on that source (e.g.
`S-1-5-21-1234-5678-9012`). The OIDC bridge matches each extracted
SID against every source's prefix and only auto-binds where they
agree. SIDs that match no source go into `fs_unbound_identities` so
admins can attach them manually.

## Feature flag: `BROWSE_ENFORCE_PERMS`

Default `false`. When off, Browse and entry-by-id endpoints behave
exactly like before (anyone with source access sees everything). When
on:

- Browse listings apply the array-overlap trim.
- `/api/entries/<id>` returns 404 (not 403) for entries the caller
  can't see — denies existence inference.
- `/api/browse/effective-counts` returns the visible/hidden split for
  the chosen folder so the SPA can render the "X items hidden" footer.

Admins always pass the trim by default. They can add `?show_all=1` on
Browse to opt back out for debug ("what does user X see in this
folder, and what's actually there?").

The flag exists because flipping the trim on across an existing
deployment would suddenly hide files from existing users until admins
get around to setting up bindings. Staged rollout: deploy the columns,
backfill, attach bindings, *then* flip the flag.

## End-to-end flow

1. **Scan**: connector emits files with their ACLs. Ingest writes
   `entries.viewable_by_*` via `compute_viewable_buckets`.
2. **Search index**: Meilisearch's filterable fields hold the same
   token arrays.
3. **Login**: OIDC callback extracts identity claims. Bridge auto-
   provisions `FsBinding` rows for matching sources;
   non-matching identities go to `fs_unbound_identities` (audited).
4. **Query**: client request hits an authenticated endpoint. Router
   resolves the user's tokens via `user_principal_tokens`. Browse / DB
   fallback / Analytics use `viewable_clause` for SQL; the Search
   Meilisearch path uses an identical filter on the same tokens.
5. **Render**: SPA never sees entries the user can't read.

## Audit events

Every consequential decision is recorded in `audit_events`. Search the
table for these `event_type` values:

- `oidc_login_success` — successful OIDC callback.
- `oidc_unbound_identity_created` — first time an OIDC claim went
  unmatched. Subsequent matches refresh silently.
- `browse_filtered` — a Browse listing was non-empty before the trim
  and shorter after; payload includes `path`, `visible`, `hidden`.
- `access_lookup` — an admin queried the blast-radius endpoint;
  payload includes the principal/file argument and right.
- `search_as_used` — admin searched with `search_as=` override.

## Refresh tokens

Login endpoints (local, OIDC, LDAP) mint a refresh-token chain
(`refresh_tokens` table) and set `akashic_refresh` as an HttpOnly
cookie scoped to `/api/auth`. The web client transparently calls
`/api/auth/refresh` on a 401, rotating the chain. A token presented
twice (replay) revokes the entire chain — both legitimate and
attacker sessions die. Explicit `/api/auth/logout` revokes outright.

Tunables: `access_token_expire_minutes` (default 60),
`refresh_token_expire_days` (default 30).

## See also

- [oidc-authentik.md](oidc-authentik.md) — Authentik + AD federation
  step-by-step.
- [migrations.md](migrations.md) — how schema changes ship.
