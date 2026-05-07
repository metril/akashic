import json
import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user, get_permitted_source_ids
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.user import User
from akashic.schemas.audit import SearchAsOverride
from akashic.schemas.search import SearchResults
from akashic.services.access_query import (
    override_tokens,
    user_has_any_bindings,
    user_principal_tokens,
    viewable_clause,
)
from akashic.services.audit import record_event
from akashic.services.filter_grammar import (
    has_meili_inexpressible_predicate,
    parse as parse_filters,
    to_meili,
    to_sqlalchemy as filters_to_sqlalchemy,
)

router = APIRouter(prefix="/api/search", tags=["search"])

_SAFE_EXTENSION = re.compile(r"^[a-zA-Z0-9]{1,20}$")

PermissionFilter = Literal["all", "readable", "writable"]
SearchMode = Literal["fuzzy", "glob", "regex"]
SortField = Literal["relevance", "name", "size", "mtime"]
SortOrder = Literal["asc", "desc"]


_SORT_MEILI_FIELD: dict[str, str] = {
    "name": "filename",
    "size": "size_bytes",
    "mtime": "fs_modified_at",
}


class _ForceSqlFallback(Exception):
    """Marker exception — raised when a query has predicates Meilisearch
    can't express, so the existing `except Exception` catches it and
    re-runs the request through the SQL fallback path."""


def _escape_meili_value(s: str) -> str:
    """Escape backslash and double-quote for use inside a Meili filter string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


async def _enrich_hits_with_dups(
    db: AsyncSession,
    hits: list,
    allowed_source_ids: set | None,
    scope_source_id: uuid.UUID | None = None,
) -> None:
    """Populate ``content_hash`` + ``dup_count`` on every hit.

    Two cheap queries (both hit the existing `ix_entries_active_content_hash`
    partial composite from migration 0021):

    1. Look up content_hash for each returned entry id.
    2. Count occurrences per hash within the user's currently-visible
       source set.

    dup_count is then ``count - 1`` (subtract the row itself). Hits with
    no content_hash (directories, unfinished scans) skip both fields.

    Source-scope precedence (review I12):
    - ``scope_source_id`` (the caller's ``source_id`` query param) wins
      if set — without this, filtering search to one source still
      yielded a "+N copies" badge counting dups outside that source,
      which the user can't see in the result list. Now the badge
      consistently reports dups within the same source filter the
      results are bounded by.
    - Otherwise fall back to ``allowed_source_ids`` (non-admin user's
      permitted set) — admin with no source filter sees a global count.
    """
    if not hits:
        return
    hit_ids = [h.id for h in hits]
    rows = (
        await db.execute(
            select(Entry.id, Entry.content_hash).where(Entry.id.in_(hit_ids))
        )
    ).all()
    hash_by_id: dict = {r.id: r.content_hash for r in rows if r.content_hash}
    if not hash_by_id:
        return

    hashes = list(set(hash_by_id.values()))
    conditions = [
        Entry.content_hash.in_(hashes),
        Entry.is_deleted == False,  # noqa: E712
        Entry.kind == "file",
        Entry.source_id.is_not(None),
    ]
    if scope_source_id is not None:
        conditions.append(Entry.source_id == scope_source_id)
    elif allowed_source_ids is not None:
        conditions.append(Entry.source_id.in_(allowed_source_ids))
    count_rows = (
        await db.execute(
            select(Entry.content_hash, func.count(Entry.id))
            .where(and_(*conditions))
            .group_by(Entry.content_hash)
        )
    ).all()
    count_by_hash = {r[0]: int(r[1]) for r in count_rows}
    for h in hits:
        ch = hash_by_id.get(h.id)
        if ch:
            h.content_hash = ch
            h.dup_count = max(0, count_by_hash.get(ch, 1) - 1)


def _parse_search_as(raw: str | None) -> SearchAsOverride | None:
    if raw is None:
        return None
    try:
        return SearchAsOverride.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid search_as: {exc}")


@router.get("", response_model=SearchResults)
async def search(
    q: str = Query(default=""),
    mode: SearchMode = Query(default="fuzzy"),
    source_id: uuid.UUID | None = None,
    extension: str | None = None,
    min_size: int | None = None,
    max_size: int | None = None,
    permission_filter: PermissionFilter | None = None,
    search_as: str | None = Query(default=None),
    filters: str | None = Query(default=None, description="base64url(json) predicate list"),
    sort: SortField = Query(default="relevance"),
    order: SortOrder = Query(default="desc"),
    # v0.4.14 — Search page is now infinite-scroll, so the page size
    # default should be something a viewport actually fills. Cap at 500
    # so one bad client can't ask for the entire index in a single
    # round-trip.
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if extension and not _SAFE_EXTENSION.match(extension):
        raise HTTPException(status_code=400, detail="Invalid extension format")

    # v0.5.11 — validate regex up front so a parser error returns 400
    # cleanly instead of getting swallowed by the bare `except Exception:`
    # below and falling through to the SQL path where postgres would
    # raise a different error.
    if mode == "regex" and q.strip():
        from akashic.services.search import validate_regex
        try:
            validate_regex(q)
        except re.error as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid regex: {exc.msg} at position {exc.pos}",
            )

    # Phase-6 grammar predicates ride alongside the legacy individual
    # query params. Both styles are AND'd so a query string with both
    # `extension=pdf` and a base64url-encoded extension predicate is the
    # same trim applied twice — harmless. Stale URLs decode to [].
    try:
        grammar_preds = parse_filters(filters) if filters else []
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    override = _parse_search_as(search_as)
    if override is not None and user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="search_as is admin-only",
        )

    allowed_source_ids = await get_permitted_source_ids(user, db)
    if allowed_source_ids is not None:
        if not allowed_source_ids:
            return SearchResults(results=[], total=0, query=q)
        if source_id and source_id not in allowed_source_ids:
            raise HTTPException(status_code=403, detail="No access to this source")

    # Default policy: has bindings → 'readable'; no bindings → 'all'
    if permission_filter is None:
        if override is not None:
            permission_filter = "readable"
        else:
            permission_filter = "readable" if await user_has_any_bindings(user, db) else "all"

    # Force the SQL path when Meili can't express the query:
    #  - path/grammar predicates Meili doesn't support (today: PathPred)
    #  - non-fuzzy modes (glob/regex need exact pattern matching)
    force_sql = (
        has_meili_inexpressible_predicate(grammar_preds)
        or mode != "fuzzy"
    )

    try:
        if force_sql:
            raise _ForceSqlFallback()
        from akashic.services.search import (
            DOMAIN_METADATA_FACET_KEYS,
            _domain_metadata_doc_field,
            search_files,
        )

        filters: list[str] = []
        if source_id:
            filters.append(f'source_id = "{source_id}"')
        elif allowed_source_ids is not None:
            sid_filter = " OR ".join(f'source_id = "{s}"' for s in allowed_source_ids)
            filters.append(f"({sid_filter})")
        if extension:
            filters.append(f'extension = "{extension}"')
        if min_size is not None:
            filters.append(f"size_bytes >= {min_size}")
        if max_size is not None:
            filters.append(f"size_bytes <= {max_size}")

        if permission_filter in ("readable", "writable"):
            if override is not None:
                tokens = override_tokens(override)
            else:
                tokens = await user_principal_tokens(user, db)
            field = "viewable_by_read" if permission_filter == "readable" else "viewable_by_write"
            tok_clause = " OR ".join(f'{field} = "{_escape_meili_value(t)}"' for t in tokens)
            filters.append(f"({tok_clause})")

        if grammar_preds:
            grammar_str = to_meili(grammar_preds)
            if grammar_str:
                filters.append(f"({grammar_str})")

        # v0.5.10 — exclude orphaned docs (source deleted with
        # purge_entries=False leaves source_id=NULL in postgres and
        # Meili). Without this filter every search that returned an
        # orphan 500'd via SearchHit's UUID validation, and even after
        # making the schema lenient, surfacing orphans gave the user
        # results they couldn't navigate to.
        filters.append("source_id IS NOT NULL")
        filter_str = " AND ".join(filters) if filters else None
        # v0.6.0 — always request domain_metadata facets on the Meili
        # path. When no entries in the result set carry that key the
        # distribution comes back empty/missing and the UI elides the
        # panel; the request itself is cheap.
        # v0.20.0 — also request the three "result shape" facets
        # (source / mime / extension). They were filterable already; this
        # exposes the per-bucket counts so the UI can render facet chips
        # with hit counts instead of an empty <Select> dropdown.
        facet_fields = [
            "source_id",
            "mime_type",
            "extension",
            *(_domain_metadata_doc_field(k) for k in DOMAIN_METADATA_FACET_KEYS),
        ]
        # v0.20.0 — explicit sort override. relevance leaves the param
        # unset so Meili applies its default ranking rules (typo, words,
        # proximity, attribute, sort, exactness).
        meili_sort = (
            [f"{_SORT_MEILI_FIELD[sort]}:{order}"] if sort != "relevance" else None
        )
        meili_results = await search_files(
            q, filters=filter_str, sort=meili_sort,
            offset=offset, limit=limit,
            facets=facet_fields,
        )

        from akashic.schemas.search import SearchHit
        hits = [SearchHit(**h) if isinstance(h, dict) else h for h in (meili_results.hits or [])]

        # v0.20.0 — attach content_hash + dup_count so the row UI can
        # surface a "+N copies" badge linking into /duplicates.
        await _enrich_hits_with_dups(db, hits, allowed_source_ids, scope_source_id=source_id)

        if override is not None:
            await record_event(
                db=db, user=user,
                event_type="search_as_used",
                payload={
                    "query": q,
                    "search_as": override.model_dump(),
                    "results_count": len(hits),
                    "source_filter": str(source_id) if source_id else None,
                },
                request=request,
                source_id=source_id,
            )

        # Project the meili facet distribution back to the dotted public
        # form (`domain_metadata.correspondent`) so frontend code talks
        # in product terms, not in the underscore-flattened wire shape.
        # v0.20.0 — top-level facets (source_id / mime_type / extension)
        # pass through verbatim; the UI looks them up by their wire name.
        facet_distribution: dict[str, dict[str, int]] | None = None
        raw_facets = getattr(meili_results, "facet_distribution", None) or {}
        if raw_facets:
            projected: dict[str, dict[str, int]] = {}
            for key in ("source_id", "mime_type", "extension"):
                bucket = raw_facets.get(key)
                if bucket:
                    projected[key] = {str(k): int(v) for k, v in bucket.items()}
            for key in DOMAIN_METADATA_FACET_KEYS:
                bucket = raw_facets.get(_domain_metadata_doc_field(key))
                if bucket:
                    projected[f"domain_metadata.{key}"] = {
                        str(k): int(v) for k, v in bucket.items()
                    }
            if projected:
                facet_distribution = projected

        return SearchResults(
            results=hits,
            total=meili_results.estimated_total_hits or 0,
            query=q,
            facet_distribution=facet_distribution,
        )
    except HTTPException:
        raise
    except Exception:
        # DB fallback — applies the same permission filter as the Meili
        # path via the `entries.viewable_by_*` columns (Phase 4). Before
        # those columns existed this branch was an escape hatch around the
        # filter; it isn't anymore.
        from akashic.services.search import glob_to_sql_like

        conditions = [
            Entry.kind == "file",
            Entry.is_deleted == False,  # noqa: E712
            # v0.5.10 — match the Meili-path filter: orphaned entries
            # (source_id=NULL after a non-purging delete) stay invisible
            # to search until they're reattached.
            Entry.source_id.is_not(None),
        ]
        # v0.5.11 — query-string match dispatches by mode. Empty q in any
        # mode means "filter only" (no name/path constraint), matching
        # fuzzy's existing semantic of `ilike("%%")` (which collapses to
        # "match anything") so filter-only searches keep working.
        if q.strip():
            if mode == "fuzzy":
                conditions.append(Entry.name.ilike(f"%{q}%"))
            elif mode == "glob":
                like_pattern = glob_to_sql_like(q)
                target_col = Entry.path if "/" in q else Entry.name
                conditions.append(target_col.ilike(like_pattern, escape="\\"))
            elif mode == "regex":
                # validate_regex was already called up top; pattern is
                # known good. Postgres `~` is POSIX regex on `path`.
                conditions.append(Entry.path.op("~")(q))
        if source_id:
            conditions.append(Entry.source_id == source_id)
        elif allowed_source_ids is not None:
            conditions.append(Entry.source_id.in_(allowed_source_ids))
        if extension:
            conditions.append(Entry.extension == extension)
        if min_size is not None:
            conditions.append(Entry.size_bytes >= min_size)
        if max_size is not None:
            conditions.append(Entry.size_bytes <= max_size)
        if permission_filter in ("readable", "writable"):
            tokens = (
                override_tokens(override)
                if override is not None
                else await user_principal_tokens(user, db)
            )
            right = "read" if permission_filter == "readable" else "write"
            conditions.append(viewable_clause(tokens, right))

        if grammar_preds:
            conditions.extend(filters_to_sqlalchemy(grammar_preds))

        # v0.20.0 — match the Meili sort knob on the fallback path so
        # users get the same ordering whether or not the request fell
        # back. relevance has no SQL analogue (Postgres has no equivalent
        # to Meili's ranking pipeline), so it implicitly orders by
        # primary key for deterministic pagination.
        query_stmt = select(Entry).where(and_(*conditions))
        if sort == "name":
            col = Entry.name
        elif sort == "size":
            col = Entry.size_bytes
        elif sort == "mtime":
            col = Entry.fs_modified_at
        else:
            col = None
        if col is not None:
            query_stmt = query_stmt.order_by(col.desc() if order == "desc" else col.asc())
        query_stmt = query_stmt.offset(offset).limit(limit)
        result = await db.execute(query_stmt)
        entries = result.scalars().all()

        from akashic.schemas.search import SearchHit
        count_stmt = select(func.count(Entry.id)).where(and_(*conditions))
        count_result = await db.execute(count_stmt)
        total = count_result.scalar() or 0

        hits = [
            SearchHit(
                id=e.id, source_id=e.source_id, path=e.path,
                filename=e.name, extension=e.extension,
                mime_type=e.mime_type, size_bytes=e.size_bytes,
                fs_modified_at=int(e.fs_modified_at.timestamp()) if e.fs_modified_at else None,
            )
            for e in entries
        ]

        # v0.20.0 — same content_hash + dup_count enrichment as the
        # Meili path. SQL fallback already has the entries in scope; we
        # could short-cut by reading e.content_hash directly, but using
        # the helper keeps both paths producing identical hit shapes.
        await _enrich_hits_with_dups(db, hits, allowed_source_ids, scope_source_id=source_id)

        if override is not None:
            await record_event(
                db=db, user=user,
                event_type="search_as_used",
                payload={
                    "query": q,
                    "search_as": override.model_dump(),
                    "results_count": len(hits),
                    "source_filter": str(source_id) if source_id else None,
                },
                request=request,
                source_id=source_id,
            )

        return SearchResults(results=hits, total=total, query=q)
