"""Admin "blast radius" lookups.

Two questions admins ask repeatedly: "what can principal X reach?" and
"who can reach file Y?". Both are admin-only because they expose the
full ACL projection across all sources without permission filtering.

Reuses Meilisearch's `viewable_by_*` filterable fields (already
populated by acl_denorm) for the principal→files direction; the
file→principals direction recomputes denormalize_acl on demand from
the entry's stored ACL/mode/uid/gid because the inverse isn't indexed.

Phase 5's personalized Browse filter ends up calling the same
principal→files query with the logged-in user's own SID set as the
token — see plan section "Sequencing pays off twice".
"""
from __future__ import annotations
from typing import Annotated

import logging
import uuid

from akashic.routing import ApiRouter
from fastapi import Depends, HTTPException, Query, Request
from pydantic import TypeAdapter
from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import Principal, check_source_access, require_scope
from akashic.auth.source_scope import ensure_source_allowed, meili_source_filter, restrict_sources
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.principals_cache import PrincipalsCache
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.access import FileAccessResponse, PrincipalAccessResponse
from akashic.schemas.acl import ACL
from akashic.schemas.common import encode_offset_cursor, offset_from_cursor
from akashic.services.acl_denorm import denormalize_acl
from akashic.services.access_query import viewable_clause
from akashic.services.audit import record_event
from akashic.services.directory.lookup import identity_result, load_identity, resolve_tokens
from akashic.services.search import get_meili_client

logger = logging.getLogger(__name__)

router = ApiRouter(prefix="/access", tags=["access"])


DENY_WARNING = (
    "Deny ACEs are not evaluated across this identity's groups: results are the union of what each "
    "listed token can reach. A file shown here may still be denied by a deny ACE on another of the "
    "person's identities. Sample rows with any deny ACE are flagged."
)

_RIGHT_FIELDS = {
    "read": "viewable_by_read",
    "write": "viewable_by_write",
    "delete": "viewable_by_delete",
}


def _meili_quote(value: str) -> str:
    """Quote a token value for Meili's filter DSL. SIDs and POSIX
    identifiers are alphanumeric+`-`+`:`, which Meili already handles,
    but quoting defensively keeps things robust against token vocabulary
    growth."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


async def _resolve_sid_name(
    db: AsyncSession, token: str,
) -> tuple[str | None, str | None]:
    """Best-effort name lookup for a SID — returns (name, domain) or (None, None).

    Accepts either a raw SID (`S-1-5-…`) or the canonical token form
    (`sid:S-1-5-…`); strips the prefix when present. Uses any matching
    row from principals_cache regardless of source so a SID that
    resolved on share A is named when shown for share B. Doesn't
    trigger an LSARPC round-trip; admins use /principals/resolve
    explicitly when they want a fresh name."""
    if token.startswith("sid:"):
        sid = token[4:]
    elif token.startswith("S-"):
        sid = token
    else:
        return None, None
    row = (
        await db.execute(
            select(PrincipalsCache.name, PrincipalsCache.domain)
            .where(PrincipalsCache.sid == sid, PrincipalsCache.name.isnot(None))
            .limit(1)
        )
    ).first()
    if row is None:
        return None, None
    return row.name, row.domain


def _token_kind(token: str) -> str:
    """Classify a token in the canonical vocabulary so the UI can render
    'user' vs 'group' vs 'wildcard' icons without parsing strings."""
    if token == "*":
        return "wildcard"
    if token == "auth":
        return "wildcard"
    if token.startswith("posix:gid:"):
        return "group"
    if token.startswith("posix:uid:"):
        return "user"
    if token.startswith("nfsv4:GROUP:"):
        return "group"
    if token.startswith("nfsv4:"):
        return "user"
    if token.startswith("sid:"):
        return "user"  # SIDs default to user; can't disambiguate cheaply here
    return "unknown"


# ── Principal → files ──────────────────────────────────────────────────────


@router.get(
    "",
    response_model=PrincipalAccessResponse | FileAccessResponse,
    summary="Look up access (blast radius)",
    description=(
        "Answers one of two questions, selected by which parameter is given.\n"
        "`principal`: what can this principal reach? Returns a summary, a per-source rollup and a "
        "paginated sample of files the principal holds `right` on, from the search index.\n"
        "`identity`: what can this person or group reach? `identity` is a merge id from "
        "`GET /access/identities`. Returns the same shape for the union of every token of the "
        "identity (`tokens_used`, with `expansion` describing how complete the token set is), plus the "
        "resolved `identity`. Deny ACEs are not evaluated across the identity's tokens: `warnings` says so, and each "
        "sample row carries `has_deny_ace`.\n"
        "`file`: who can reach this file? Returns the principals holding `right` on the entry, "
        "computed from its stored ACL, mode, uid and gid.\n"
        "Requires scope `access:read`. Source-limited API tokens only see their permitted "
        "sources. Every successful lookup is recorded as an `access_lookup` audit event; failed lookups (4xx) are not.\n"
        "Errors: 400 unless exactly one of `principal`, `identity` and `file` is given; 404 if `identity` is unknown, "
        "if the `file` entry does not exist or is outside the token's sources, or if `source_id` names a source outside the "
        "token's sources (a `source_id` that does not exist at all simply yields empty results); 422 for a malformed "
        "`cursor` or invalid parameter."
    ),
)
async def access_query(
    request: Request,
    principal: str | None = Query(default=None, description="Principal token to look up, in the canonical token vocabulary (`sid:S-1-5-...`, `posix:uid:1000`, `posix:gid:100`, `nfsv4:...`, `*` for everyone). Mutually exclusive with `identity` and `file`.", examples=['sid:S-1-5-21-1-2-3-1001']),
    identity: str | None = Query(default=None, max_length=1000, description="Merge id of a person or group from `GET /access/identities`. Mutually exclusive with `principal` and `file`.", examples=['sid:S-1-5-21-1-2-3-1001']),
    file: uuid.UUID | None = Query(default=None, description="Entry id to list principals for. Mutually exclusive with `principal` and `identity`."),
    right: str = Query(default="read", pattern="^(read|write|delete)$", description="Right to evaluate: `read`, `write` or `delete`."),
    source_id: Annotated[uuid.UUID | None, Query(description="Restrict a `principal` lookup to this source.")] = None,
    cursor: str | None = Query(default=None, description="Opaque cursor from a previous `next_cursor` (principal mode only)."),
    limit: int = Query(default=20, ge=1, le=100, description="Page size for the sample of files (principal mode only)."),
    db: AsyncSession = Depends(get_db),
    caller: Principal = require_scope("access:read", source_scoped=True),
):
    """Single endpoint, three modes.

    - `?principal=<token>` (and optional source_id/right): returns
      summary + per-source rollup + paginated sample of files this
      principal can {read|write|delete}. Powered by a Meilisearch
      filter on `viewable_by_<right>`.
    - `?file=<uuid>` (and optional right): returns the list of
      principals that have the requested right against the file,
      computed from the entry's stored ACL.

    - `?identity=<merge id>`: the same as `principal`, over the union of
      every token of a merged directory identity.

    Exactly one of `principal` / `identity` / `file` is required;
    anything else produces a 400."""
    if sum(p is not None for p in (principal, identity, file)) != 1:
        raise HTTPException(
            status_code=400,
            detail="Pass exactly one of `principal`, `identity` or `file`",
        )

    # Audit every blast-radius lookup. The values here (a wildcard
    # `*` principal returns the world-readable file list) are exactly
    # the kind admins later want to ask "who looked at that?" — and
    # the events table is already the right place.
    offset = offset_from_cursor(cursor)
    audit_payload: dict = {"right": right, "offset": offset, "limit": limit}
    if principal is not None:
        audit_payload["principal"] = principal
    if identity is not None:
        audit_payload["identity"] = identity
    if file is not None:
        audit_payload["file_id"] = str(file)
    if source_id is not None:
        audit_payload["source_id"] = str(source_id)

    if source_id is not None:
        await check_source_access(source_id, caller, db)
    if file is not None:
        result = await _file_to_principals(file, right, caller, db)
    elif identity is not None:
        ident = await load_identity(db, identity)
        if ident is None:
            raise HTTPException(status_code=404, detail="Identity not found")
        resolved = await resolve_tokens(db, ident)
        result = await _principal_to_files(resolved.tokens, right, source_id, offset, limit, caller, db)
        result["principal"] = {
            "token": ident.id,
            "name": ident.display_name,
            "domain": None,
            "kind": "group" if ident.kind == "group" else "user",
        }
        result["identity"] = identity_result(ident)
        result["tokens_used"] = [
            {"token": u.token, "origin": u.origin, "name": u.name} for u in resolved.used
        ]
        result["expansion"] = {
            "status": resolved.status,
            "cached_at": resolved.cached_at,
            "detail": resolved.detail,
            "missing_identity_kinds": resolved.missing_identity_kinds,
        }
        result["warnings"] = [DENY_WARNING]
        audit_payload["requested_identity"] = identity
        audit_payload["identity"] = ident.id
        audit_payload["identity_name"] = ident.display_name
        audit_payload["token_count"] = len(resolved.tokens)
        audit_payload["expansion"] = resolved.status
    else:
        assert principal is not None
        result = await _principal_to_files([principal], right, source_id, offset, limit, caller, db)
        name, domain = await _resolve_sid_name(db, principal)
        result["principal"] = {
            "token": principal, "name": name, "domain": domain, "kind": _token_kind(principal),
        }

    await record_event(
        db=db, user=caller.user,
        event_type="access_lookup",
        payload=audit_payload,
        request=request,
        source_id=source_id,
    )
    await db.commit()
    return result


_DENY_ACE = literal_column("""'$.entries[*] ? (@.ace_type == "deny")'::jsonpath""")


async def _flag_deny_aces(db: AsyncSession, hits: list[dict]) -> None:
    """Set `has_deny_ace` on each sample hit from the entry's stored ACL; None when the entry no longer exists (stale index doc)."""
    ids = [uuid.UUID(h["id"]) for h in hits]
    flagged: dict[uuid.UUID, bool] = {}
    if ids:
        rows = await db.execute(
            select(Entry.id, func.jsonb_path_exists(Entry.acl, _DENY_ACE)).where(Entry.id.in_(ids))
        )
        flagged = {eid: bool(has) for eid, has in rows.all()}
    for h in hits:
        h["has_deny_ace"] = flagged.get(uuid.UUID(h["id"]))


async def _principal_to_files(
    tokens: list[str],
    right: str,
    source_id: uuid.UUID | None,
    offset: int,
    limit: int,
    caller: Principal,
    db: AsyncSession,
):
    field = _RIGHT_FIELDS[right]
    quoted = ", ".join(f'"{_meili_quote(t)}"' for t in tokens)
    parts = [f"{field} IN [{quoted}]"]
    if source_id is not None:
        parts.append(f'source_id = "{source_id}"')
    limit_clause = meili_source_filter(caller)
    if limit_clause is not None:
        parts.append(limit_clause)
    filterstr = " AND ".join(parts)

    client = await get_meili_client()
    index = await client.get_index("files")

    # Aggregates come from Postgres: Meili facetStats only returns
    # min/max (no sum). Same semantics as the Meili filter: files only
    # (the index holds files), non-deleted, token in viewable_by_<right>
    # (GIN-backed `&&`), optional source, caller's source restriction.
    agg = (
        select(
            Entry.source_id,
            func.count().label("n"),
            func.coalesce(func.sum(Entry.size_bytes), 0).label("bytes"),
        )
        .where(
            Entry.kind == "file",
            Entry.is_deleted.is_(False),
            viewable_clause(tokens, right),
        )
        .group_by(Entry.source_id)
    )
    if source_id is not None:
        agg = agg.where(Entry.source_id == source_id)
    agg = restrict_sources(agg, caller)
    agg_rows = [r for r in (await db.execute(agg)).all() if r.source_id is not None]
    by_source_dist: dict[str, int] = {str(r.source_id): int(r.n) for r in agg_rows}
    file_count = sum(by_source_dist.values())
    total_size_bytes = sum(int(r.bytes) for r in agg_rows)

    if tokens:
        sample = await index.search(
            "",
            filter=filterstr,
            offset=offset,
            limit=limit,
            attributes_to_retrieve=[
                "id", "source_id", "path", "filename", "size_bytes",
                "owner_name", "fs_modified_at",
            ],
        )
        sample_hits = (
            getattr(sample, "hits", None)
            or sample.__dict__.get("hits", [])
            or []
        )
    else:
        sample_hits = []  # no tokens: nothing is reachable, and `IN []` is not valid Meili
    await _flag_deny_aces(db, sample_hits)

    # Hydrate source names for the rollup.
    src_ids = list(by_source_dist.keys())
    source_names: dict[str, str] = {}
    if src_ids:
        rows = (await db.execute(
            select(Source.id, Source.name).where(Source.id.in_([uuid.UUID(x) for x in src_ids]))
        )).all()
        for sid_, name in rows:
            source_names[str(sid_)] = name

    by_source = sorted(
        [
            {
                "source_id": sid_,
                "source_name": source_names.get(sid_, sid_[:8]),
                "file_count": int(cnt),
            }
            for sid_, cnt in by_source_dist.items()
        ],
        key=lambda r: r["file_count"],
        reverse=True,
    )

    next_cursor = encode_offset_cursor(offset + limit) if len(sample_hits) >= limit else None

    return {
        "right": right,
        "summary": {
            "file_count": file_count,
            "total_size_bytes": total_size_bytes,
            "source_count": len(by_source),
        },
        "by_source": by_source,
        "sample": sample_hits,
        "next_cursor": next_cursor,
        "limit": limit,
    }


# ── File → principals ──────────────────────────────────────────────────────


async def _file_to_principals(
    entry_id: uuid.UUID,
    right: str,
    caller: Principal,
    db: AsyncSession,
):
    entry = (
        await db.execute(select(Entry).where(Entry.id == entry_id))
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    ensure_source_allowed(caller, entry.source_id, "Entry not found")

    # denormalize_acl wants a typed ACL or None.
    acl_obj = None
    if entry.acl:
        try:
            acl_obj = TypeAdapter(ACL).validate_python(entry.acl)
        except Exception:  # noqa: BLE001
            logger.warning(
                "entry %s has malformed ACL JSONB; treating as None", entry.id,
            )
            acl_obj = None

    buckets = denormalize_acl(
        acl=acl_obj,
        base_mode=entry.mode,
        base_uid=entry.uid,
        base_gid=entry.gid,
    )
    tokens = list(buckets[right])

    # Hydrate friendly names for SID tokens. Wildcards/POSIX entries
    # don't need a lookup; they self-describe.
    sids_to_resolve = [
        t.split("sid:", 1)[1] for t in tokens if t.startswith("sid:")
    ]
    name_map: dict[str, tuple[str | None, str | None]] = {}
    if sids_to_resolve:
        rows = (await db.execute(
            select(PrincipalsCache.sid, PrincipalsCache.name, PrincipalsCache.domain)
            .where(
                PrincipalsCache.sid.in_(sids_to_resolve),
                PrincipalsCache.name.isnot(None),
            )
        )).all()
        for sid, name, domain in rows:
            name_map[sid] = (name, domain)

    out: list[dict] = []
    for t in tokens:
        item: dict = {
            "token": t,
            "kind": _token_kind(t),
            "source": "acl",
        }
        if t.startswith("sid:"):
            sid = t[4:]
            name, domain = name_map.get(sid, (None, None))
            item["name"] = name
            item["domain"] = domain
        out.append(item)

    return {
        "entry_id": str(entry.id),
        "path": entry.path,
        "filename": entry.name,
        "right": right,
        "principals": out,
    }
