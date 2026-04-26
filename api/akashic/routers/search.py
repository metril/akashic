import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user, get_permitted_source_ids
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.user import User
from akashic.schemas.search import SearchResults

router = APIRouter(prefix="/api/search", tags=["search"])

# Only allow safe alphanumeric extensions
_SAFE_EXTENSION = re.compile(r"^[a-zA-Z0-9]{1,20}$")


@router.get("", response_model=SearchResults)
async def search(
    q: str = Query(default=""),
    source_id: uuid.UUID | None = None,
    extension: str | None = None,
    min_size: int | None = None,
    max_size: int | None = None,
    offset: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Validate extension before any search path
    if extension and not _SAFE_EXTENSION.match(extension):
        raise HTTPException(status_code=400, detail="Invalid extension format")

    # RBAC: scope to permitted sources for non-admin users
    allowed_source_ids = await get_permitted_source_ids(user, db)
    if allowed_source_ids is not None:
        if not allowed_source_ids:
            return SearchResults(results=[], total=0, query=q)
        if source_id and source_id not in allowed_source_ids:
            raise HTTPException(status_code=403, detail="No access to this source")

    try:
        from akashic.services.search import search_files

        filters = []
        if source_id:
            filters.append(f'source_id = "{source_id}"')
        elif allowed_source_ids is not None:
            sid_filter = " OR ".join(f'source_id = "{sid}"' for sid in allowed_source_ids)
            filters.append(f"({sid_filter})")
        if extension:
            filters.append(f'extension = "{extension}"')
        if min_size is not None:
            filters.append(f"size_bytes >= {min_size}")
        if max_size is not None:
            filters.append(f"size_bytes <= {max_size}")

        filter_str = " AND ".join(filters) if filters else None
        meili_results = await search_files(q, filters=filter_str, offset=offset, limit=limit)

        from akashic.schemas.search import SearchHit
        hits = [SearchHit(**h) if isinstance(h, dict) else h for h in (meili_results.hits or [])]
        return SearchResults(
            results=hits,
            total=meili_results.estimated_total_hits or 0,
            query=q,
        )
    except HTTPException:
        raise
    except Exception:
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
        return SearchResults(results=hits, total=total, query=q)
