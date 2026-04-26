from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user, get_permitted_source_ids
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


async def _source_filter(user: User, db: AsyncSession):
    """WHERE clauses scoping queries to permitted sources."""
    allowed = await get_permitted_source_ids(user, db)
    if allowed is None:
        return []  # Admin — no filter
    if not allowed:
        return [False]
    return [Entry.source_id.in_(allowed)]


_FILE_FILTERS = (Entry.kind == "file", Entry.is_deleted == False)  # noqa: E712


@router.get("/storage-by-type")
async def get_storage_by_type(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    filters = list(_FILE_FILTERS)
    filters.extend(await _source_filter(user, db))

    stmt = (
        select(
            Entry.extension,
            func.count(Entry.id).label("count"),
            func.sum(Entry.size_bytes).label("total_size"),
        )
        .where(*filters)
        .group_by(Entry.extension)
        .order_by(func.sum(Entry.size_bytes).desc())
    )
    result = await db.execute(stmt)
    return [
        {"extension": r.extension, "count": r.count, "total_size": r.total_size}
        for r in result.all()
    ]


@router.get("/storage-by-source")
async def get_storage_by_source(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    filters = list(_FILE_FILTERS)
    filters.extend(await _source_filter(user, db))

    stmt = (
        select(
            Entry.source_id,
            Source.name.label("source_name"),
            func.count(Entry.id).label("count"),
            func.sum(Entry.size_bytes).label("total_size"),
        )
        .join(Source, Source.id == Entry.source_id)
        .where(*filters)
        .group_by(Entry.source_id, Source.name)
        .order_by(func.sum(Entry.size_bytes).desc())
    )
    result = await db.execute(stmt)
    return [
        {
            "source_id": str(r.source_id),
            "source_name": r.source_name,
            "count": r.count,
            "total_size": r.total_size,
        }
        for r in result.all()
    ]


@router.get("/largest-files")
async def get_largest_files(
    n: int = Query(default=10, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    filters = [*_FILE_FILTERS, Entry.size_bytes.isnot(None)]
    filters.extend(await _source_filter(user, db))

    stmt = (
        select(Entry)
        .where(*filters)
        .order_by(Entry.size_bytes.desc())
        .limit(n)
    )
    result = await db.execute(stmt)
    entries = result.scalars().all()
    return [
        {
            "id": str(e.id),
            "source_id": str(e.source_id),
            "path": e.path,
            "filename": e.name,
            "size_bytes": e.size_bytes,
            "mime_type": e.mime_type,
        }
        for e in entries
    ]
