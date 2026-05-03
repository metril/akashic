# Search and Browse

Both pages list files. **Search** is full-text + filter; **Browse**
is the directory-tree listing for a single source. They share the
filter chip grammar and the same per-user ACL filter model.

## Search

### Query basics

Free-text query box at the top. Matches against `filename`,
`path`, `content_text` (extracted by Tika at scan time), and
tags.

By default, results respect your effective ACL — you only see
files you'd actually be allowed to access. The default mode flips
based on whether you have `FsBindings` linking you to source-side
identities (see [authentication.md](authentication.md)):

| Permission filter | Default when | Behaviour |
|-------------------|--------------|-----------|
| **Files I can read** | You have FsBindings | Standard ACL filter |
| **Files I can write** | (manual) | Filter by `viewable_by_write` |
| **All files I have access to** | You have no FsBindings | No principal filter; just respects source-level access |

Switch with the dropdown at the top-left of the result list.

### Filter chips

Click any cell in a result row (extension badge, source name,
owner) to add a filter chip. Chips are AND-combined and live in
the URL (`?filters=base64url(json)`) so deep links survive
refresh.

The chip grammar is a small, symmetric set of predicates defined
in both [`web/src/lib/filterGrammar.ts`](../web/src/lib/filterGrammar.ts)
and [`api/akashic/services/filter_grammar.py`](../api/akashic/services/filter_grammar.py).

| Predicate | JSON form | Example use |
|-----------|-----------|-------------|
| extension | `{kind:"extension", value:"pdf"}` | files ending `.pdf` |
| source | `{kind:"source", value:"<uuid>"}` | files in this source |
| owner | `{kind:"owner", value:"alice"}` | files owned by `alice` |
| principal | `{kind:"principal", value:"S-1-5-21-...", right:"read"\|"write"\|"delete"}` | files where this principal has this right |
| mime | `{kind:"mime", value:"application/pdf"}` | by MIME type |
| size | `{kind:"size", op:"gte"\|"lte"\|"eq", value:<bytes>}` | size comparison |
| mtime | `{kind:"mtime", op:"gte"\|"lte", value:<epoch>}` | modification time |
| path | `{kind:"path", value:"/some/prefix"}` | path-prefix match (forces SQL fallback — Meilisearch can't express it) |
| tag | `{kind:"tag", value:"archive"}` | files (or descendants of dirs) tagged `archive`. See [tags.md](tags.md). |

The encoding is `base64url(JSON.stringify([...preds]))`, passed
as `?filters=` on both Search and Browse. Stale URLs (decoding to
invalid JSON or unknown predicate kinds) silently drop to
`filters=[]` rather than 400-ing.

### Infinite scroll

Results load 100 at a time as you scroll; an `IntersectionObserver`
on a sentinel at the bottom of the list triggers the next fetch
when it enters the viewport.

The footer shows one of:

- *Loading more…* — fetch in flight.
- *End of results (N)* — no more pages.
- *Showing top 100,000 of 100,000+ matches — refine your query
  for more* — Meilisearch's `pagination.maxTotalHits` cap reached.
  The cap is set by [`MAX_TOTAL_HITS`](../api/akashic/services/search.py)
  on the API; raise it there if your hardware can take the
  per-query memory.

### Tag bulk-apply (admin only)

Tick the checkbox next to one or more results, click **Tag
selected (N)**, enter comma-separated tag names. Hits
`POST /api/tags/bulk-apply` with `{entry_ids, tags}`. Tagging a
directory cascades to every descendant. See
[tags.md](tags.md) for the model.

### Search-as principal impersonation

The **Search as…** disclosure at the top opens a form that lets
you query as a specific principal — useful for "what does Alice
actually see?" debugging. Available to any authenticated user;
results are still bounded by your own source-level access (you
won't see results from sources you can't read regardless of
which principal you impersonate).

The principal can be one of:

| Type | Identifier | Groups |
|------|-----------|--------|
| `posix_uid` | Unix UID (string) | List of GIDs |
| `sid` | Windows / AD SID | List of group SIDs |
| `nfsv4_principal` | `user@REALM` | List of `group@REALM` |
| `s3_canonical` | S3 canonical user ID | (none) |

Every search-as query is recorded in the audit log
([admin.md](admin.md)) with the override payload and the result
count, so the use of this tool is itself auditable.

## Browse

The Browse tab is the directory-tree listing for a single
source — sort, filter chips, and infinite-scrolled
[react-virtual](https://tanstack.com/virtual/) rendering for
large folders. URL state owns navigation (`?source=` + `?path=`),
and the browse page uses cursor pagination
([`api/akashic/routers/browse.py`](../api/akashic/routers/browse.py))
so deep navigation in huge folders stays fast.

### ACL-filtered listing

By default Browse shows entries the current user can read — the
same filter as Search. Admins get a **Show all** toggle
(top-right) that disables the filter so they can see everything
in a folder regardless of access. Useful for triaging "I don't
see X but I think I should." The toggle persists in
`localStorage`.

The exact behaviour depends on the
[`BROWSE_ENFORCE_PERMS`](configuration.md#permissions--caching)
feature flag. Off by default — when off, Browse shows everything
the user has source-level access to (matching pre-Phase-5
behaviour). Flip on once your deployment has FsBindings set up
and the entry backfill has run.

### Sort

Server-side sort by name / size / modified. Sort order survives
URL navigation.

### Filter chips

Same grammar as Search — see the table above. Most useful in
Browse for the `path`, `extension`, `mime`, and `tag`
predicates; `principal` is more typically used in Search.

## See also

- [tags.md](tags.md) — what the `tag` predicate matches against
- [admin.md](admin.md) — audit log entries for search-as
- [permissions-model.md](permissions-model.md) — what
  "files I can read" actually means
