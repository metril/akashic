import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.models.file import File
from akashic.models.user import User
from akashic.schemas.search import SearchResults

router = APIRouter(prefix="/api/search", tags=["search"])

# Only allow safe alphanumeric extensions
_SAFE_EXTENSION = re.compile(r"^[a-zA-Z0-9]{1,20}$")


@router.get("", response_model=SearchResults)
async def search(
    q: str = Query(..., min_length=1),
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

    try:
        from akashic.services.search import search_files

        filters = []
        if source_id:
            filters.append(f'source_id = "{source_id}"')
        if extension:
            filters.append(f'extension = "{extension}"')
        if min_size is not None:
            filters.append(f"size_bytes >= {min_size}")
        if max_size is not None:
            filters.append(f"size_bytes <= {max_size}")

        filter_str = " AND ".join(filters) if filters else None
        meili_results = await search_files(q, filters=filter_str, offset=offset, limit=limit)

        return SearchResults(
            results=meili_results.hits,
            total=meili_results.estimated_total_hits or 0,
            query=q,
        )
    except HTTPException:
        raise
    except Exception:
        conditions = [File.is_deleted == False, File.filename.ilike(f"%{q}%")]
        if source_id:
            conditions.append(File.source_id == source_id)
        if extension:
            conditions.append(File.extension == extension)
        if min_size is not None:
            conditions.append(File.size_bytes >= min_size)
        if max_size is not None:
            conditions.append(File.size_bytes <= max_size)

        query_stmt = select(File).where(and_(*conditions)).offset(offset).limit(limit)
        result = await db.execute(query_stmt)
        files = result.scalars().all()

        from sqlalchemy import func
        count_stmt = select(func.count(File.id)).where(and_(*conditions))
        count_result = await db.execute(count_stmt)
        total = count_result.scalar() or 0

        return SearchResults(results=files, total=total, query=q)
