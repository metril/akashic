# Multi-model ACL capture, effective permissions, and ACL-aware search

**Date:** 2026-04-26
**Status:** Approved design
**Predecessor:** Project A — Unified Entry model + file/folder browser + permission capture

## Context

Project A landed POSIX ACL capture for the local connector only. Real-world deployments need ACL capture across all four supported connector flavors (local, NFS, SSH, SMB, S3) and across three different ACL data models (POSIX, NFSv4, NT). On top of that:

- **Effective permissions calculation** — given a captured ACL plus a principal identity, answer "what can this person do on this entry."
- **ACL-aware search** — Meilisearch results filtered to entries the searching user can actually access.
- **Cross-source identity unification** — a single dashboard user can map to multiple per-source identities (Linux uid here, Windows SID there).
- **Group-membership auto-resolution** — for POSIX, NT, and NFSv4, fetch the searching user's group memberships from the source's identity authority instead of requiring manual entry.
- **Structured audit log** — capture sensitive search-time events (search-as overrides) and identity-management actions in a queryable table.

The design is sequenced into 14 independently shippable phases. No deployed data exists, so schema changes drop tables and recreate via `Base.metadata.create_all()`.

## User-locked decisions

- **Storage shape:** discriminated union JSONB on `Entry.acl` and `EntryVersion.acl` with `type ∈ {posix, nfsv4, nt, s3}`. (Q1-A.)
- **S3 scope:** per-object ACL opt-in via source config + bucket-level metadata always captured on `Source`. (Q2-C.)
- **SSH capture strategy:** hybrid — full-tree dump on full scans, per-directory batch on incremental scans. (Q3-C.)
- **CIFS SID resolution:** in-band LSA lookup over SMB via DCE/RPC. (Q4-B.)
- **UI rendering:** single drawer section dispatched on `acl.type`; small "is this exposed?" banner on S3 entry drawers. (Q5a + Q5b-2.)
- **In scope:** POSIX default ACLs, effective permissions calculation, ACL-aware Meilisearch, cross-source identity unification, group-membership auto-resolution, structured audit log.

---

## Architecture

### Storage shapes

`Entry.acl` and `EntryVersion.acl` carry one of four shapes, distinguished by the `type` discriminator. Pydantic discriminated unions at the API boundary validate the inner shape per type.

```jsonc
// POSIX (ext4/xfs/zfs with POSIX ACLs)
{
  "type": "posix",
  "entries":         [{"tag": "user", "qualifier": "alice", "perms": "rwx"}, ...],
  "default_entries": [{"tag": "user_obj", "qualifier": "", "perms": "rwx"}, ...]   // null for files; null for dirs without default ACL
}

// NFSv4 (NFSv4 mounts, ZFS with nfsv4 ACLs)
{
  "type": "nfsv4",
  "entries": [
    {
      "principal": "alice@example.com",
      "ace_type":  "allow",                 // allow | deny | audit | alarm
      "flags":     ["file_inherit", "dir_inherit"],
      "mask":      ["read_data", "write_data", "append_data", "read_attributes"]
    }
  ]
}

// NT (CIFS/SMB)
{
  "type":    "nt",
  "owner":   {"sid": "S-1-5-21-...-1013", "name": "DOMAIN\\alice"},
  "group":   {"sid": "S-1-5-21-...-513",  "name": "DOMAIN\\Domain Users"},
  "control": ["dacl_present", "dacl_protected"],
  "entries": [
    {
      "sid":      "S-1-5-21-...-1013",
      "name":     "DOMAIN\\alice",          // empty if LSA lookup failed; renderer falls back to sid
      "ace_type": "allow",
      "flags":    ["object_inherit", "container_inherit", "inherited"],
      "mask":     ["read_data", "write_data", "read_ea", "synchronize"]
    }
  ]
}

// S3 per-object
{
  "type":   "s3",
  "owner":  {"id": "...canonical_user_id...", "display_name": "..."},
  "grants": [
    {
      "grantee_type": "canonical_user",     // canonical_user | group | amazon_customer_by_email
      "grantee_id":   "...",
      "grantee_name": "...",
      "permission":   "FULL_CONTROL"        // FULL_CONTROL | READ | WRITE | READ_ACP | WRITE_ACP
    }
  ]
}
```

### Source-level S3 metadata

New JSONB column `Source.security_metadata`:

```jsonc
{
  "captured_at": "2026-04-26T12:00:00Z",
  "bucket_acl":  { "owner": {...}, "grants": [...] },
  "bucket_policy_present": true,
  "bucket_policy": {...},                                   // full IAM doc
  "public_access_block": {
    "block_public_acls":       true,
    "ignore_public_acls":      true,
    "block_public_policy":     true,
    "restrict_public_buckets": true
  },
  "is_public_inferred": false                               // derived from PAB + policy + ACL
}
```

### Code organization

**Scanner (Go):**
- `scanner/internal/metadata/acl.go` — dispatcher (tries NFSv4 first, falls back to POSIX).
- `scanner/internal/metadata/acl_posix.go` — POSIX (renamed from existing `acl.go`).
- `scanner/internal/metadata/acl_nfsv4.go` — NFSv4 via `nfs4_getfacl`.
- `scanner/internal/metadata/acl_remote.go` — text parser shared between local single-file and SSH dump output.
- `scanner/internal/metadata/acl_nt.go` — NT via SMB security descriptor query.
- `scanner/internal/metadata/acl_s3.go` — S3 per-object via `GetObjectAcl`.
- `scanner/internal/metadata/well_known_sids.go` — static well-known SID table (no LSA dependency).
- `scanner/internal/metadata/sid_resolver.go` — LSARPC client wrapper + cache; layers on top of well-known table.
- `scanner/internal/metadata/sddl/` — binary security descriptor parser ([MS-DTYP] §2.4).
- `scanner/internal/lsarpc/` — DCE/RPC + LSARPC client over SMB named pipes.
- `scanner/internal/samr/` — DCE/RPC + SAMR client (Phase 14 — group resolution).

**API (Python):**
- `api/akashic/schemas/acl.py` — Pydantic discriminated unions for the four ACL shapes.
- `api/akashic/services/effective_perms.py` — per-type evaluators.
- `api/akashic/services/acl_denorm.py` — denormalize ACL to principal identifiers for Meili indexing.
- `api/akashic/services/audit.py` — `record_event()` helper.
- `api/akashic/routers/{identities,audit}.py` — new endpoints.

**Frontend (TypeScript):**
- `web/src/components/acl/{ACLSection,PosixACL,NfsV4ACL,NtACL,S3ACL,ACLDiff,S3ExposureBanner,BucketSecurityCard,EffectivePermissions}.tsx`
- `web/src/lib/{aclLabels,aclDiff}.ts`
- `web/src/pages/{SettingsIdentities,AdminAudit}.tsx`

---

## Capture strategies per transport

### Local + NFS connector (POSIX or NFSv4 auto-detected)

NFS connector wraps Local — both share the code path.

**Tool detection per file:** try `nfs4_getfacl <path>` first; on non-zero exit or "not supported" stderr, fall back to `getfacl <path>`. Two atomic flags (`nfs4MissingFlag`, `getfaclMissingFlag`) short-circuit when a tool isn't installed at all.

**Default ACL capture (POSIX only):** drop `--skip-base --no-effective` from the `getfacl` invocation. Output carries access ACL followed by a default-ACL section (`default:` prefixed lines on directories). The parser splits on the `default:` prefix and routes ACEs into either `entries` or `default_entries`. Empty default section → `default_entries: null`. NFSv4 has no separate default-ACL concept (handled via flags), so `nfs4_getfacl` invocation is unchanged.

### SSH connector (Hybrid: full-tree dump + per-directory batch)

**On `Connect()`:** probe remote tools once via `ssh getfacl --version` and `ssh nfs4_getfacl --version`. Cache availability flags on the connector.

**Full scan path:**

1. Before walking, run a single exec:
   ```
   find <root> -exec nfs4_getfacl {} \; 2>/dev/null;
   find <root> -exec getfacl --absolute-names {} + 2>/dev/null
   ```
2. Stream output, parse into `map[string]*models.ACL` keyed by absolute path.
3. Hand the map to the walker; per-entry `CollectACL` becomes a map lookup.
4. NFSv4 results take precedence over POSIX when both exist for a path.

**Incremental scan path:** for each directory the walker enters, run one batched exec:

```
find <dir> -maxdepth 1 -mindepth 1 -exec nfs4_getfacl {} \; 2>/dev/null;
find <dir> -maxdepth 1 -mindepth 1 -exec getfacl --absolute-names {} + 2>/dev/null
```

Cache results until the next directory.

### SMB connector (NT ACLs)

**Security descriptor capture:**

- `go-smb2` doesn't expose `GetSecurityInfo` directly. We extend it via the lower-level IOCTL interface, or directly send an SMB2 `QUERY_INFO` request with `InfoType=SECURITY_INFO` and `AdditionalInfo=OWNER|GROUP|DACL`. Returns a binary NT security descriptor.
- `scanner/internal/metadata/sddl/parser.go` — parses the binary SD per [MS-DTYP] §2.4: header → owner SID → group SID → SACL (skipped) → DACL (parsed into ACEs). Table-driven tests with hand-crafted SD fixtures.
- `scanner/internal/metadata/acl_nt.go` — wraps the SMB query + parser, returns `*models.NtACL` with raw SIDs.

**SID resolution via LSARPC (Phase 9):**

- New package `scanner/internal/lsarpc/` — DCE/RPC over SMB named pipes (`\PIPE\lsarpc`).
- Implements minimum surface: `LsarOpenPolicy2`, `LsarLookupSids2`, `LsarClose`. Per [MS-LSAT].
- Underlying transport: SMB2 named-pipe IOCTLs (already supported by `go-smb2`). DCE/RPC bind/PDU packing handled in the new package.
- `scanner/internal/metadata/sid_resolver.go` wraps the LSARPC client with:
  - In-process cache `map[string]string` (SID → name) for the scan.
  - Well-known SID table (~50 entries) checked first.
  - Batch `LsarLookupSids2` calls (up to 1000 SIDs per call per protocol limits) with per-directory flush.
- On any LSARPC error (server doesn't expose LSA, permission denied, RPC fault): name fields stay empty, raw SIDs persist. Entry capture never fails because of name resolution.

### S3 connector

**Bucket-level (always, per scan, Phase 6):**

New scanner phase before walking:

- `GetBucketAcl`
- `GetBucketPolicy` (404 → no policy)
- `GetPublicAccessBlock` (404 → no PAB)

Compute `is_public_inferred` from PAB + policy `Effect:Allow Principal:"*"` + bucket-ACL `AllUsers` group grants. Pack into `security_metadata` JSON, send as part of the scan-start ingest call (new field `source_security_metadata` on `POST /api/ingest/batch`).

**Per-object (opt-in via source config, Phase 7):**

- New source field `connection_config.capture_object_acls: bool` (default `false`).
- When enabled, walker calls `GetObjectAcl` for every object encountered. One extra API call per object.
- Result packed into `EntryRecord.Acl` as `{"type": "s3", ...}`.

---

## Ingest pipeline

### Schema and validation

Pydantic discriminated unions in `api/akashic/schemas/acl.py`:

```python
class PosixACL(BaseModel):
    type: Literal["posix"]
    entries: list[PosixACE]
    default_entries: list[PosixACE] | None = None

class NfsV4ACL(BaseModel):
    type: Literal["nfsv4"]
    entries: list[NfsV4ACE]

class NtACL(BaseModel):
    type: Literal["nt"]
    owner: NtPrincipal | None
    group: NtPrincipal | None
    control: list[str]
    entries: list[NtACE]

class S3ACL(BaseModel):
    type: Literal["s3"]
    owner: S3Owner | None
    grants: list[S3Grant]

ACL = Annotated[Union[PosixACL, NfsV4ACL, NtACL, S3ACL], Field(discriminator="type")]
```

`EntryIn`, `EntryResponse`, `EntryDetailResponse`, `EntryVersionResponse` all gain `acl: ACL | None`. `SourceResponse` gains `security_metadata: SourceSecurityMetadata | None`.

### Stable ACL comparison

`api/akashic/services/ingest.py` adds:

```python
def acl_equal(a: dict | None, b: dict | None) -> bool:
    if a is None or b is None:
        return a is b
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
```

`VERSIONED_FIELDS` comparison gets a special case for `acl` to use this helper, avoiding false-positive version creation due to JSONB key reordering across drivers.

### Bucket security ingest

`POST /api/ingest/batch` accepts optional `source_security_metadata` in the batch payload. When present (typically only on the first batch of a scan), it updates `Source.security_metadata`.

---

## Effective permissions calculation

### Architecture

Backend service `api/akashic/services/effective_perms.py` — pure functions, fully unit-testable per type.

```python
def compute_effective(
    acl: ACL | None,
    base_mode: int | None,
    base_uid: int | None,
    base_gid: int | None,
    principal: PrincipalRef,
    groups: list[str],
) -> EffectivePerms:
    ...
```

Returns:

```jsonc
{
  "rights": {
    "read":           {"granted": true,  "by": [{"ace_index": 2, "summary": "user:alice:r-x"}]},
    "write":          {"granted": false, "by": [{"ace_index": 0, "summary": "deny everyone WRITE"}]},
    "execute":        {"granted": true,  "by": [...]},
    "delete":         {"granted": false, "by": []},
    "change_perms":   {"granted": false, "by": []}
  },
  "evaluated_with": {
    "model":     "nt",
    "principal": {"type": "sid", "identifier": "S-1-5-21-...-1013", "name": "DOMAIN\\alice"},
    "groups":    [{"sid": "S-1-5-21-...-513", "name": "DOMAIN\\Domain Users"}, ...],
    "caveats":   ["Computation does not include IAM policies"]
  }
}
```

The `by` field is essential — every grant/deny is traced back to specific ACEs. Without that, users can't trust the result.

### Per-type evaluators

**POSIX** (per POSIX.1e §23.1.5):
1. If principal uid matches `base_uid` → use owner perms (mode bits 6-8).
2. Else if principal uid matches a `user:<name>:perms` ACE → use that, masked by `mask::`.
3. Else if any group in principal's groups matches `group_obj` or a `group:<name>:perms` ACE → union of matching, masked by `mask::`.
4. Else → other perms (mode bits 0-2).

POSIX yields `{read, write, execute}` only — fold `delete` and `change_perms` into write.

**NFSv4** (per RFC 7530 §6.2.1):
- For each requested right bit, walk ACEs in order.
- First ACE matching the principal (or matching a group the principal belongs to) AND addressing that right wins — `allow` grants, `deny` denies, no-match continues.
- Rights not addressed by any ACE are denied.

**NT** (Microsoft's documented algorithm):
- Walk ACEs in order. For each requested right:
  - If ACE matches principal/groups AND addresses the right: `allow` ACE grants, `deny` ACE denies. First match wins.
- Unaddressed rights denied.
- Inherited ACEs evaluated identically — already in the DACL since we capture post-inheritance.
- Owner field grants implicit `read_control + write_dacl` (caveat in `evaluated_with.caveats`).

**S3** (partial):
- Object ACL grants checked first (matching `grantee_id` or well-known group like `AllUsers`).
- Bucket policy `Effect:Allow Principal` matches checked next (limited Principal matching; `Condition` keys not evaluated — advisory only).
- `block_public_acls` / `restrict_public_buckets` from PAB override grants for `AllUsers` / `AuthenticatedUsers`.
- Caveat: `"S3 evaluation does not include IAM user/role policies; bucket policy condition keys not evaluated"`.

### API surface

`POST /api/entries/{id}/effective-permissions`:

```jsonc
// Request
{
  "principal": {"type": "sid", "identifier": "S-1-5-21-...-1013"},
  "groups":    [{"type": "sid", "identifier": "S-1-5-21-...-513"}],
  "principal_name_hint": "DOMAIN\\alice"
}
```

Response is the `EffectivePerms` shape above. RBAC: same `check_source_access(..., "read")` as the existing entry endpoint. No state mutation, no caching.

---

## Identity model and ACL-aware search

### Identity schema (cross-source unification)

```python
class FsPerson(Base):
    id:         UUID
    user_id:    UUID  # FK → users
    label:      str
    is_primary: bool
    created_at: datetime

class FsBinding(Base):
    id:                   UUID
    fs_person_id:         UUID  # FK
    source_id:            UUID  # FK
    identity_type:        str   # 'posix_uid' | 'sid' | 'nfsv4_principal' | 's3_canonical'
    identifier:           str
    groups:               list[str]   # JSONB
    groups_source:        str         # 'manual' | 'auto'
    groups_resolved_at:   datetime | None
    created_at:           datetime
    # Unique: (fs_person_id, source_id) — at most one binding per source per person
```

A `FsPerson` represents one real-world identity-set (e.g., "My Linux + Windows accounts"). A user can have multiple FsPersons (work vs personal). Bindings link a person to identifiers per source.

### Identifier vocabulary (used in Meili index)

Single string-key vocabulary across all ACL models:

- `*` — anyone (POSIX `other`, NT `Everyone`, NFSv4 `EVERYONE@`, S3 `AllUsers`)
- `auth` — any authenticated principal (NT `Authenticated Users`, S3 `AuthenticatedUsers`)
- `posix:uid:1000` / `posix:gid:100`
- `sid:S-1-5-21-...-1013`
- `nfsv4:alice@example.com` / `nfsv4:GROUP:eng@example.com`
- `s3:user:<canonical_id>`

### Denormalization

`api/akashic/services/acl_denorm.py`:

```python
def denormalize_acl(
    acl: ACL | None,
    base_mode: int | None,
    base_uid: int | None,
    base_gid: int | None,
) -> dict[str, list[str]]:
    """Returns {'read': [...], 'write': [...], 'delete': [...]} of principal identifier strings."""
```

Reuses the per-type evaluators from `effective_perms.py`. For each conceivable principal in the ACL, asks "does this principal have read?", emits the identifier if yes. NT/NFSv4 deny ACEs naturally exclude principals.

**Cross-model right mapping** — denormalization buckets each model's native rights into three common rights for the search filter:

| Common right | POSIX | NT mask bits | NFSv4 mask bits | S3 permission |
|---|---|---|---|---|
| **read** | `r` | `READ_DATA`, `LIST_DIRECTORY`, `GENERIC_READ`, `GENERIC_ALL` | `read_data`, `list_directory` | `READ`, `FULL_CONTROL` |
| **write** | `w` | `WRITE_DATA`, `ADD_FILE`, `APPEND_DATA`, `GENERIC_WRITE`, `GENERIC_ALL` | `write_data`, `append_data`, `add_file` | `WRITE`, `FULL_CONTROL` |
| **delete** | `w` on parent (skipped at denorm time — entry-local only) | `DELETE`, `DELETE_CHILD`, `GENERIC_ALL` | `delete`, `delete_child` | `WRITE` (S3 deletes via bucket WRITE) |

POSIX `delete` denormalization is intentionally not computed — it depends on the parent directory's ACL, which would require a join at index time. Operators wanting per-entry POSIX delete semantics can use the effective-permissions endpoint with explicit principal context.

### Indexing pipeline

`api/akashic/services/search.py`:
- `index_entry()` — when building the Meili document, call `denormalize_acl()` and add `viewable_by_read`, `viewable_by_write`, `viewable_by_delete` arrays.
- `setup_meilisearch()` — `filterable_attributes` gains the three `viewable_by_*` fields.
- Re-indexing on ACL change: existing pipeline re-indexes on any entry change. Explicit check: re-index when `acl_equal()` returns False.

### Query-time filter

`api/akashic/routers/search.py`:
- Resolve current user's bindings: `bindings = await load_user_bindings(user.id, source_id_filter)`.
- Build the principal filter set: each binding contributes its identifier + each group identifier + `*` + `auth`.
- Meili filter clause: `viewable_by_read IN [...]` AND with existing source-RBAC filter.

**New search request params:**
- `permission_filter: 'all' | 'readable' | 'writable'` — default `readable` when user has bindings, `all` when none.
- `search_as: SearchAsOverride | null` — power-user override `{type, identifier, groups}`. Audit-logged.
- `fs_person_id: UUID | null` — scope to a single FsPerson instead of aggregating.

---

## Group-membership auto-resolution

### Per ACL model

**POSIX** — via existing connector to the source:
- Local: `os/user.LookupId(uid)` then `(*user.User).GroupIds()`.
- SSH: `ssh user@host id -G <identifier>` — single exec per identity.
- NFS (wraps Local): same as Local.

**NT (CIFS)** — extends the LSARPC package (Phase 9) with **SAMR** (Security Account Manager Remote):
- New package `scanner/internal/samr/` — DCE/RPC + named-pipe (`\PIPE\samr`) transport, reusing `lsarpc/` primitives.
- Surface: `SamrConnect5`, `SamrOpenDomain`, `SamrLookupNamesInDomain`, `SamrOpenUser`, `SamrGetGroupsForUser`, `SamrClose`. Per [MS-SAMR].
- The user's SID + per-domain handle gets us their group SIDs. We resolve those SIDs to names via the existing LSARPC client.

**NFSv4** — LDAP:
- New optional source config for NFS sources: `ldap_url`, `ldap_bind_dn`, `ldap_bind_password`, `ldap_user_search_base`, `ldap_group_attr` (default `memberOf`).
- `github.com/go-ldap/ldap/v3` (mature, no cgo).
- `ResolveGroups()`: bind to LDAP, search `(uid=<principal>)`, read `memberOf`.
- If LDAP not configured: `ErrUnsupported`, fall back to manual entry.

**S3** — no analog. Returns `ErrUnsupported`.

### Connector interface

```go
type Connector interface {
    // ... existing methods
    ResolveGroups(ctx context.Context, identity Identity) ([]string, error)
}
```

### Cache schema

```python
class PrincipalGroupsCache(Base):
    id:             UUID
    source_id:      UUID  # FK
    identity_type:  str
    identifier:     str
    groups:         list[str]   # JSONB
    resolved_at:    datetime
    # PK: (source_id, identity_type, identifier)
```

- TTL: 24h. Stale entries trigger re-resolve on next access.
- Manual refresh button in UI.
- Bulk warm CLI: `python -m akashic.tools.warm_groups --source-id <X>`.

### API surface

- `POST /api/me/fs-persons/{id}/bindings/{bid}/resolve-groups` — triggers resolution, returns groups, updates cache.
- `GET /api/me/fs-persons/{id}` response includes `groups_resolved_at`, `groups_source` per binding.

---

## Structured audit log

### Schema

```python
class AuditEvent(Base):
    id:           UUID
    user_id:      UUID  # FK → users
    event_type:   str   # 'search_as_used' | 'identity_added' | ...
    occurred_at:  datetime
    source_id:    UUID | None
    request_ip:   str
    user_agent:   str
    payload:      dict  # JSONB — event-specific
```

### Captured events (initial set)

- `search_as_used` — payload `{query, search_as: {type, identifier, groups}, results_count, source_filter}`.
- `identity_added` / `identity_removed` — payload `{fs_person_id, fs_person_label}`.
- `binding_added` / `binding_removed` — payload `{fs_person_id, source_id, identity_type, identifier}`.
- `groups_auto_resolved` — payload `{binding_id, resolved_count, source: 'samr' | 'ldap' | 'nss'}`.

Other system actions are not in v1 — extensible schema lets them join later without migration.

### API and UI

- `GET /api/admin/audit?user_id=X&event_type=Y&from=Z&to=W&page=N` — admin-only.
- `GET /api/admin/audit/{id}` — single event.
- Admin page `/admin/audit` with date range picker, event-type multi-select, user picker, source picker.

### Logging mechanism

`api/akashic/services/audit.py` — `record_event(db, user, event_type, payload, request)` helper. Writes through the same SQLAlchemy session as the request. Failed audit writes log a warning but don't fail the user-facing operation.

### Retention

Default: keep forever. Add `audit_retention_days` config setting (default `0` = forever) and a daily scheduler job that deletes events older than the threshold when set.

---

## UI surfaces

### Drawer ACL section (per-type dispatch)

`web/src/components/acl/ACLSection.tsx`:

```tsx
const TITLE: Record<ACLType, string> = {
  posix: "POSIX ACL", nfsv4: "NFSv4 ACL", nt: "NT ACL", s3: "S3 ACL",
};
const RENDERER: Record<ACLType, React.FC<{acl: any}>> = {
  posix: PosixACL, nfsv4: NfsV4ACL, nt: NtACL, s3: S3ACL,
};
```

**Per-type renderers:**

- **`PosixACL.tsx`** — refactor of existing inline POSIX table from `EntryDetail.tsx`. Three columns: Tag · Qualifier · Perms. **Default ACL subsection** below the access ACL when `default_entries` is non-null (directories only).
- **`NfsV4ACL.tsx`** — table: Index · Principal · Type (allow/deny badge) · Flags (chip group) · Mask (chip group, friendly names from `aclLabels.ts`). Order preserved.
- **`NtACL.tsx`** — owner + group rows (`name || sid`, with SID muted small under name when both present). Control-flag chips. ACE table identical to NFSv4 layout. Inherited ACEs collapsed by default behind a "Show N inherited entries" toggle.
- **`S3ACL.tsx`** — owner row, then grants table: Grantee Type · Grantee · Permission. Empty state: "Bucket-owner enforced — see source for bucket policy".

**`web/src/lib/aclLabels.ts`** — friendly-name lookup tables:
- NT mask bits: `READ_DATA` → "Read", `GENERIC_ALL` → "Full Control", etc.
- NFSv4 mask bits: `read_data` → "Read", `append_data` → "Append", etc.
- ACE flag friendly names for both.
- Falls back to raw value (uppercased) when not in table.

### S3 exposure banner

`web/src/components/acl/S3ExposureBanner.tsx` — shown above ACL section when source type is S3 and security_metadata is populated.

| State | Trigger | Look |
|---|---|---|
| **Public** | `is_public_inferred === true` | Red banner, alert icon, "Bucket is publicly accessible. View bucket policy →" |
| **Restricted** | All four `public_access_block.*` true OR explicit deny-all policy | Green banner, lock icon, "Bucket public access blocked." |
| **Mixed/unknown** | Otherwise | Amber banner, info icon, "Bucket exposure: review configuration →" |

Compact (~48px tall). Link scrolls to source's `BucketSecurityCard`.

### Sources page bucket security card

`web/src/components/acl/BucketSecurityCard.tsx` — rendered on Sources page after source detail when `source.type === 's3'`:

1. Header — captured-at timestamp, "Refresh" button.
2. Public access block — 2×2 grid of the four PAB booleans as labeled badges.
3. Bucket policy — syntax-highlighted JSON viewer if present.
4. Bucket ACL — grants table matching `S3ACL.tsx` layout.

### Effective permissions card

`web/src/components/acl/EffectivePermissions.tsx` — between ACL section and version history:

1. Principal picker form — type dropdown (defaulted to match entry's ACL type), identifier input, "+ Add group" repeater.
2. Compute button (uses `useMutation`).
3. Result table — five rows (read / write / execute / delete / change_perms), each with green checkmark or red X, source ACE summaries inline as small muted text. Caveats badge at top.

### Version-history ACL diff

`web/src/lib/aclDiff.ts` — pure functions per type:

```ts
type ACLDiffItem =
  | { kind: 'type_changed'; from: ACLType; to: ACLType }
  | { kind: 'added';        summary: string }
  | { kind: 'removed';      summary: string }
  | { kind: 'modified';     summary: string }
  | { kind: 'owner_changed'; from: string; to: string }
  | { kind: 'group_changed'; from: string; to: string };

export function diffACL(prev: ACL | null, curr: ACL | null): ACLDiffItem[];
```

**Per-type strategies:**
- **POSIX** — keyed set diff on `(tag, qualifier)`. Runs twice (once for `entries`, once for `default_entries` with `[default]` prefix). Order doesn't matter.
- **NFSv4** — ordered list with LCS for inserts/removals; explicit reorder reporting since reordering deny ACEs changes effective access.
- **NT** — same as NFSv4. Plus first-class owner/group change items. Inherited ACEs collapsed under "Inherited changes (3)" toggle.
- **S3** — keyed by `(grantee_type, grantee_id, permission)`. Owner change first-class. Order doesn't matter.

`web/src/components/acl/ACLDiff.tsx` renders items with per-type icons (added=green +, removed=red −, modified=amber ~, type_changed=neutral ⇄, owner/group_changed=blue ↻). Replaces the literal `Changed: acl` label in the version-history section.

### Settings: identities

New page `/settings/identities` (`web/src/pages/SettingsIdentities.tsx`):

```
Identities
├─ My Work Account                    [primary] [edit] [delete]
│   ├─ home-nas:        uid 1000 (groups: 100, 1000) [resolved 2h ago] [refresh]
│   ├─ archive-server:  uid 1001 (groups: 100)       [manual]          [resolve]
│   └─ + Add binding
├─ My Home Account
│   └─ ...
└─ + Add identity
```

New "Settings" item at bottom of sidebar nav (above Sign out).

### Search page

`web/src/pages/Search.tsx`:
- New filter dropdown: "Show: [Files I can read ▾]" — options `Files I can read` / `Files I can write` / `All files I have source access to`.
- "Search as…" overflow toggle — opens an ephemeral form mirroring identity entry. Result count badge gains `(filtered as <principal>)` suffix.
- `fs_person_id` scope dropdown when user has multiple FsPersons.
- Per-result lock icon on entries with restrictive ACLs (only the searching user can read). Hover preview: "Visible to: alice, group:eng".
- S3 caveat banner when any S3 result appears in a filtered search.

### Admin: audit log

New page `/admin/audit` (`web/src/pages/AdminAudit.tsx`):
- Table: timestamp · user · event type · summary · details-expand.
- Filters: date range picker, event-type multi-select, user picker, source picker.
- Detail view: pretty-printed payload JSON.
- Nav item visible only to admins.

---

## Build sequence

Each phase is independently shippable. Order chosen so every phase delivers a verifiable vertical slice end-to-end.

### Phase 1 — Schema + ingest validation
- Wrap existing POSIX into `{type: "posix", entries: [...]}` at write time.
- Add `Source.security_metadata` JSONB column.
- New Pydantic discriminated-union schemas in `api/akashic/schemas/acl.py`.
- `EntryIn` / `EntryResponse` / `EntryDetailResponse` / `EntryVersionResponse` / `SourceResponse` updated.
- `acl_equal()` helper in `services/ingest.py`.
- **Verify:** existing scanner ingest still works (POSIX path round-trips through new wrapper).

### Phase 2 — Local POSIX wrapping + default ACL capture
- Update `scanner/internal/metadata/acl_posix.go` (rename of `acl.go`) to return wrapped `*models.ACL`.
- Drop `--skip-base --no-effective`; parse default-ACL section.
- New `scanner/internal/metadata/acl.go` dispatcher.
- **Verify:** `psql -c "SELECT acl FROM entries LIMIT 5"` shows wrapped shape including `default_entries` on directories.

### Phase 3 — Frontend: ACL dispatcher + POSIX renderer + labels
- New `web/src/components/acl/{ACLSection,PosixACL}.tsx`.
- New `web/src/lib/aclLabels.ts`.
- Replace inline POSIX section in `EntryDetail.tsx` with `<ACLSection>`.
- POSIX renderer includes default-ACL subsection.
- **Verify:** Playwright check — existing POSIX ACL drawer renders identically post-refactor; default ACL appears on directories with one set.

### Phase 4 — Local NFSv4 capture + renderer
- New `scanner/internal/metadata/acl_nfsv4.go` (shells out to `nfs4_getfacl`).
- Dispatcher tries NFSv4 first, falls back to POSIX.
- New `web/src/components/acl/NfsV4ACL.tsx`.
- **Verify:** scan against an NFSv4-capable mount (or skip on hosts without `nfs4_getfacl`); drawer shows NFSv4 table.

### Phase 5 — SSH hybrid ACL capture
- `scanner/internal/connector/ssh.go` — full-tree dump on full scans, per-directory batch on incremental.
- Tool detection on `Connect()`.
- New `scanner/internal/metadata/acl_remote.go` (shared text parser).
- **Verify:** SSH-source scan against a remote with mixed POSIX/NFSv4 ACLs returns wrapped data; latency profile shows single exec for full scan.

### Phase 6 — S3 bucket-level + Sources page card
- `scanner/internal/connector/s3.go` — `collectBucketSecurity()` runs once per scan.
- Bucket security shipped via `source_security_metadata` field on `POST /api/ingest/batch`.
- New `web/src/components/acl/BucketSecurityCard.tsx` rendered on Sources page when `type === 's3'`.
- **Verify:** scan an S3 source; Sources page shows PAB grid + bucket policy JSON.

### Phase 7 — S3 per-object ACL + renderer + exposure banner
- New source config field `capture_object_acls` (default `false`); plumbed from API → scanner config → connector.
- `scanner/internal/metadata/acl_s3.go` (per-object wrapper).
- New `web/src/components/acl/{S3ACL,S3ExposureBanner}.tsx`.
- Banner rendered atop `EntryDetail.tsx` when source is S3.
- **Verify:** enable flag on a source, re-scan; entry drawer shows S3 ACL section + correct exposure banner state.

### Phase 8 — CIFS NT ACL capture (raw SIDs + well-known table)
- New `scanner/internal/metadata/sddl/parser.go` + fixtures + tests.
- New `scanner/internal/metadata/well_known_sids.go` — static table for ~50 standard SIDs (Everyone, SYSTEM, BUILTIN\Administrators, etc.). No LSA dependency. Used here and reused by Phase 9.
- New `scanner/internal/metadata/acl_nt.go` — invokes well-known table for inline resolution.
- `scanner/internal/connector/smb.go` — wire SD query into `Walk` (SMB2 QUERY_INFO with SECURITY_INFO).
- New `web/src/components/acl/NtACL.tsx` — renders with raw SIDs + well-known names where matched.
- **Verify:** scan an SMB share; entry drawer shows NT ACL table with SID strings; well-known SIDs (Everyone, SYSTEM, Administrators) render with friendly names. Domain SIDs stay as `S-1-5-21-...`.

### Phase 9 — CIFS LSARPC SID resolution
- New `scanner/internal/lsarpc/` package: bind, PDU encode/decode, `LsarOpenPolicy2`, `LsarLookupSids2`, `LsarClose`.
- New `scanner/internal/metadata/sid_resolver.go` — wraps the LSARPC client. Reuses Phase 8's `well_known_sids.go` for the first-tier check, then LSA cache + per-directory batch flush.
- Wire into `acl_nt.go`.
- **Verify:** SMB scan against AD-joined share resolves DOMAIN\username on every ACE; LSA failure (server doesn't expose LSA, permission denied) falls back to raw SIDs without breaking entry capture.

### Phase 10 — Per-type version-history diff
- New `web/src/lib/aclDiff.ts` — `diffACL()` + per-type strategies + tests.
- New `web/src/components/acl/ACLDiff.tsx`.
- `EntryDetail.tsx` version-history section uses `<ACLDiff>` for ACL changes; other field labels unchanged.
- **Verify:** chmod / setfacl / `nfs4_setfacl` an entry between two scans; version history shows specific diff items.

### Phase 11 — Effective permissions
- Backend: `api/akashic/services/effective_perms.py` with per-type evaluators.
- New endpoint `POST /api/entries/{id}/effective-permissions`.
- Pure-function unit tests with table-driven fixtures per evaluator.
- New `web/src/components/acl/EffectivePermissions.tsx`.
- Integrated into `EntryDetail.tsx` between ACL section and version history.
- **Verify:** for an entry with a known POSIX ACL + a synthetic NT ACL, the computed result matches a hand-evaluated expectation; deny ACEs correctly suppress allow.

### Phase 12 — Identity model + bare-minimum search filter
- `fs_persons` + `fs_bindings` tables; CRUD endpoints; basic `SettingsIdentities` UI.
- `api/akashic/services/acl_denorm.py` reusing Phase 11 evaluators.
- Index-time enrichment + filterable-attribute config in `services/search.py`.
- Bulk re-index command `python -m akashic.tools.reindex_search`.
- Search query-time filter (no `search_as` yet).
- **Verify:** add identities for the test admin user; search filter narrows results to entries the identities can read.

### Phase 13 — `search_as` + structured audit log
- `audit_events` table + `services/audit.py`.
- `search_as` parameter on search endpoint with audit capture.
- Search UI overflow menu + result-count badge update.
- `/admin/audit` page + endpoints.
- **Verify:** trigger a `search_as` query; row appears in admin audit log with full payload.

### Phase 14 — Group-membership auto-resolution
- `principal_groups_cache` table.
- `Connector.ResolveGroups` interface + per-connector implementations:
  - Local/NFS — POSIX via stdlib `os/user`.
  - SSH — POSIX via remote `id -G`.
  - SMB — **new `scanner/internal/samr/` package** (SAMR over DCE/RPC). Single biggest item in this phase.
  - S3 — `ErrUnsupported`.
- LDAP config + `go-ldap/ldap/v3` integration for NFSv4 principals on NFS sources.
- Auto-resolve UI button + binding refresh affordance + `groups_auto_resolved` audit event.
- **Verify:** for each source type, click auto-resolve on a binding; groups appear and `groups_source = 'auto'`. SAMR path tested against a domain-joined share.

---

## Critical files

### Create

**Backend (Python):**
- `api/akashic/schemas/acl.py`
- `api/akashic/services/{effective_perms,acl_denorm,audit,group_resolver}.py`
- `api/akashic/routers/{identities,audit}.py`
- `api/akashic/models/{fs_person,fs_binding,principal_groups_cache,audit_event}.py`
- `api/akashic/tools/{reindex_search,warm_groups}.py`

**Scanner (Go):**
- `scanner/internal/metadata/{acl.go, acl_nfsv4.go, acl_remote.go, acl_nt.go, acl_s3.go, well_known_sids.go, sid_resolver.go}`
- `scanner/internal/metadata/sddl/{parser.go, parser_test.go, fixtures/}`
- `scanner/internal/lsarpc/{client.go, bind.go, pdu.go, lookup_sids.go, *_test.go}`
- `scanner/internal/samr/{client.go, lookup_user.go, get_groups.go, *_test.go}`

**Frontend (TypeScript):**
- `web/src/components/acl/{ACLSection.tsx, PosixACL.tsx, NfsV4ACL.tsx, NtACL.tsx, S3ACL.tsx, ACLDiff.tsx, S3ExposureBanner.tsx, BucketSecurityCard.tsx, EffectivePermissions.tsx}`
- `web/src/lib/{aclLabels.ts, aclDiff.ts}`
- `web/src/pages/{SettingsIdentities.tsx, AdminAudit.tsx}`

### Edit

**Backend:**
- `api/akashic/models/{entry.py, source.py, user.py}`
- `api/akashic/schemas/{entry.py, scan.py, source.py, user.py}`
- `api/akashic/services/{ingest.py, search.py}`
- `api/akashic/routers/{ingest.py, sources.py, search.py, entries.py}`
- `api/akashic/main.py` (router registrations)
- `api/akashic/config.py` (`audit_retention_days`)

**Scanner:**
- `scanner/internal/metadata/{collector.go, acl_posix.go (renamed)}`
- `scanner/internal/connector/{ssh.go, smb.go, s3.go, local.go, nfs.go}`
- `scanner/internal/connector/connector.go` (interface)
- `scanner/pkg/models/models.go`
- `scanner/cmd/akashic-scanner/main.go`
- `scanner/go.mod` (`go-ldap/ldap/v3`)

**Frontend:**
- `web/src/types/index.ts`
- `web/src/components/EntryDetail.tsx`
- `web/src/components/Layout.tsx` (Settings nav, Admin nav for admins)
- `web/src/App.tsx` (new routes)
- `web/src/pages/{Sources.tsx, Search.tsx}`

### Rename

- `scanner/internal/metadata/acl.go` → `scanner/internal/metadata/acl_posix.go` (with new `acl.go` becoming the dispatcher)

---

## Out of scope

- **SACL capture (audit ACEs)** — DACL only. SACL access requires `SE_SECURITY_NAME` privilege rarely available on the SMB session, and audit policies aren't part of "who can read this."
- **AD LDAP integration for NT** — LSA-over-SMB chosen (Q4-B). NFSv4 LDAP is in scope (different model).
- **IAM policy capture for S3** — out-of-band from S3 service. Bucket policies cover the most common "is this exposed" question.
- **ACL editing from UI** — read-only by design.

---

## Verification (end-to-end)

After all 14 phases:

1. **Schema bring-up:** `docker compose down -v && docker compose up -d` — fresh DB. `psql -c "\d entries"` shows ACL column. `\d fs_persons \d fs_bindings \d audit_events \d principal_groups_cache` show new tables.
2. **Local POSIX with default ACL:** `setfacl -d -m u:nobody:rx /tmp/test-dir`, scan, drawer shows access + default sections.
3. **NFSv4:** scan an NFSv4 mount; drawer shows NFSv4 ACL with proper flags/mask chips.
4. **SSH:** SSH-source scan against a remote Linux box with mixed ACL types; drawer shows wrapped data per file. Performance: a 10k-file full scan completes in single-digit seconds for ACL portion (single dump exec).
5. **SMB raw:** scan an SMB share; drawer shows NT ACL with raw SIDs + well-known names.
6. **SMB with LSA:** same scan against AD-joined share; ACEs render `DOMAIN\username`.
7. **S3 bucket-level:** scan an S3 bucket; Sources page shows PAB grid + policy JSON.
8. **S3 per-object:** enable opt-in flag; entry drawer shows per-object grants.
9. **S3 exposure banner:** create a public S3 bucket, scan; entry drawer shows red "Bucket is publicly accessible" banner.
10. **Effective permissions:** for an entry with `user:alice:rwx` POSIX ACE, query effective for `posix_uid=alice`; result shows read/write/execute granted with `by` referencing the ACE.
11. **Version-history diff:** `chmod 777 /tmp/file` between scans; version history shows `[mode] -rw-r--r-- → -rwxrwxrwx` and `[acl] Modified: user::r-x → rwx` (or equivalent).
12. **Identity registration:** in SettingsIdentities, create an FsPerson with bindings to two sources; verify in DB.
13. **Search filter:** with bindings registered, search for a term; results limited to entries the bindings can read. Without bindings: results unfiltered.
14. **search_as:** trigger a search-as query for a different SID; results match that principal's view; audit log row created with the override details.
15. **Group auto-resolve POSIX:** click resolve on a Local/SSH binding; groups populate from `id -G` output; `groups_source = 'auto'`.
16. **Group auto-resolve NT:** click resolve on an SMB binding against a domain controller; groups populate via SAMR.
17. **Group auto-resolve NFSv4:** with LDAP configured on the source, click resolve; groups populate from `memberOf`.
18. **Audit log:** `/admin/audit` shows recent events with filters working; non-admin users cannot access the page or endpoint.
19. **Permissions verification:** non-admin user can register their own identities, see their own audit-relevant actions in their drawer (if surfaced), but cannot access the admin audit page.
20. **Existing pages regress-clean:** Dashboard / Browse / Search / Sources / Duplicates / Analytics still render without console errors.
