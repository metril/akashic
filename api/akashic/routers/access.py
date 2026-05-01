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

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import require_admin
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.principals_cache import PrincipalsCache
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.acl import ACL
from akashic.services.acl_denorm import denormalize_acl
from akashic.services.audit import record_event
from akashic.services.search import get_meili_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/access", tags=["access"])


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


@router.get("")
async def access_query(
    request: Request,
    principal: str | None = Query(default=None),
    file: uuid.UUID | None = Query(default=None),
    right: str = Query(default="read", pattern="^(read|write|delete)$"),
    source_id: uuid.UUID | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Single endpoint, two modes.

    - `?principal=<token>` (and optional source_id/right): returns
      summary + per-source rollup + paginated sample of files this
      principal can {read|write|delete}. Powered by a Meilisearch
      filter on `viewable_by_<right>`.
    - `?file=<uuid>` (and optional right): returns the list of
      principals that have the requested right against the file,
      computed from the entry's stored ACL.

    Exactly one of `principal` / `file` is required. Both at once or
    neither produces a 400."""
    if (principal is None) == (file is None):
        raise HTTPException(
            status_code=400,
            detail="Pass exactly one of `principal` or `file`",
        )

    # Audit every blast-radius lookup. The values here (a wildcard
    # `*` principal returns the world-readable file list) are exactly
    # the kind admins later want to ask "who looked at that?" — and
    # the events table is already the right place.
    audit_payload: dict = {"right": right, "offset": offset, "limit": limit}
    if principal is not None:
        audit_payload["principal"] = principal
    if file is not None:
        audit_payload["file_id"] = str(file)
    if source_id is not None:
        audit_payload["source_id"] = str(source_id)

    if file is not None:
        result = await _file_to_principals(file, right, db)
    else:
        assert principal is not None
        result = await _principal_to_files(principal, right, source_id, offset, limit, db)

    await record_event(
        db=db, user=user,
        event_type="access_lookup",
        payload=audit_payload,
        request=request,
        source_id=source_id,
    )
    await db.commit()
    return result


async def _principal_to_files(
    principal: str,
    right: str,
    source_id: uuid.UUID | None,
    offset: int,
    limit: int,
    db: AsyncSession,
):
    field = _RIGHT_FIELDS[right]
    parts = [f'{field} = "{_meili_quote(principal)}"']
    if source_id is not None:
        parts.append(f'source_id = "{source_id}"')
    filterstr = " AND ".join(parts)

    client = await get_meili_client()
    index = await client.get_index("files")

    # Three queries: aggregate counts (size+count+source_count) via a
    # facet-distribution call, per-source rollup via the same call's
    # by_source map, and the paginated sample. Meili can't sum
    # size_bytes via facets alone, so we use search.facet_stats to get
    # min/max/sum on a numeric field.
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

    facet_resp = await index.search(
        "",
        filter=filterstr,
        limit=0,
        facets=["source_id", "size_bytes"],
    )

    # facet_distribution → {source_id: {<id>: count}}; facet_stats →
    # {size_bytes: {min, max, sum, ...}}. Both are present even when
    # the result set is empty (count just zeroes out).
    distribution = (
        getattr(facet_resp, "facet_distribution", None)
        or facet_resp.__dict__.get("facetDistribution")
        or {}
    )
    facet_stats = (
        getattr(facet_resp, "facet_stats", None)
        or facet_resp.__dict__.get("facetStats")
        or {}
    )
    by_source_dist: dict[str, int] = distribution.get("source_id") or {}
    size_stats = facet_stats.get("size_bytes") or {}

    total_size_bytes = int(size_stats.get("sum") or 0)
    file_count = int(getattr(facet_resp, "estimated_total_hits", 0)
                     or facet_resp.__dict__.get("estimatedTotalHits", 0)
                     or len(by_source_dist))
    # estimated_total_hits is a hit count not always tagged; fall back
    # to summing the per-source distribution which is exact for
    # non-paginated facet calls.
    if not file_count:
        file_count = sum(int(v) for v in by_source_dist.values())

    # Hydrate source names for the rollup.
    src_ids = [uuid.UUID(s) for s in by_source_dist.keys() if s]
    source_names: dict[str, str] = {}
    if src_ids:
        rows = (await db.execute(
            select(Source.id, Source.name).where(Source.id.in_(src_ids))
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

    name, domain = await _resolve_sid_name(db, principal)
    sample_hits = (
        getattr(sample, "hits", None)
        or sample.__dict__.get("hits", [])
        or []
    )

    next_offset_value: int | None = offset + limit
    if len(sample_hits) < limit:
        next_offset_value = None

    return {
        "principal": {
            "token": principal,
            "name": name,
            "domain": domain,
            "kind": _token_kind(principal),
        },
        "right": right,
        "summary": {
            "file_count": file_count,
            "total_size_bytes": total_size_bytes,
            "source_count": len(by_source),
        },
        "by_source": by_source,
        "sample": sample_hits,
        "next_offset": next_offset_value,
    }


# ── File → principals ──────────────────────────────────────────────────────


async def _file_to_principals(
    entry_id: uuid.UUID,
    right: str,
    db: AsyncSession,
):
    entry = (
        await db.execute(select(Entry).where(Entry.id == entry_id))
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")

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
