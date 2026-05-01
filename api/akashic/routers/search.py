import json
import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import select, and_
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

router = APIRouter(prefix="/api/search", tags=["search"])

_SAFE_EXTENSION = re.compile(r"^[a-zA-Z0-9]{1,20}$")

PermissionFilter = Literal["all", "readable", "writable"]


def _escape_meili_value(s: str) -> str:
    """Escape backslash and double-quote for use inside a Meili filter string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


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
    source_id: uuid.UUID | None = None,
    extension: str | None = None,
    min_size: int | None = None,
    max_size: int | None = None,
    permission_filter: PermissionFilter | None = None,
    search_as: str | None = Query(default=None),
    offset: int = 0,
    limit: int = 20,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if extension and not _SAFE_EXTENSION.match(extension):
        raise HTTPException(status_code=400, detail="Invalid extension format")

    override = _parse_search_as(search_as)

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

    try:
        from akashic.services.search import search_files

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

        filter_str = " AND ".join(filters) if filters else None
        meili_results = await search_files(q, filters=filter_str, offset=offset, limit=limit)

        from akashic.schemas.search import SearchHit
        hits = [SearchHit(**h) if isinstance(h, dict) else h for h in (meili_results.hits or [])]

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

        return SearchResults(
            results=hits,
            total=meili_results.estimated_total_hits or 0,
            query=q,
        )
    except HTTPException:
        raise
    except Exception:
        # DB fallback — applies the same permission filter as the Meili
        # path via the `entries.viewable_by_*` columns (Phase 4). Before
        # those columns existed this branch was an escape hatch around the
        # filter; it isn't anymore.
        conditions = [
            Entry.kind == "file",
            Entry.is_deleted == False,  # noqa: E712
            Entry.name.ilike(f"%{q}%"),
        ]
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

        query_stmt = select(Entry).where(and_(*conditions)).offset(offset).limit(limit)
        result = await db.execute(query_stmt)
        entries = result.scalars().all()

        from sqlalchemy import func
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
