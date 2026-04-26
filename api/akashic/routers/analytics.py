from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user, get_permitted_source_ids
from akashic.database import get_db
from akashic.models.file import File
from akashic.models.source import Source
from akashic.models.user import User

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


async def _source_filter(user: User, db: AsyncSession):
    """Return a list of SQLAlchemy WHERE clauses to scope queries to permitted sources."""
    allowed = await get_permitted_source_ids(user, db)
    if allowed is None:
        return []  # Admin — no filter
    if not allowed:
        return [False]  # No access to anything
    return [File.source_id.in_(allowed)]


@router.get("/storage-by-type")
async def get_storage_by_type(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    filters = [File.is_deleted == False]  # noqa: E712
    filters.extend(await _source_filter(user, db))

    stmt = (
        select(File.extension, func.count(File.id).label("count"), func.sum(File.size_bytes).label("total_size"))
        .where(*filters)
        .group_by(File.extension)
        .order_by(func.sum(File.size_bytes).desc())
    )
    result = await db.execute(stmt)
    return [{"extension": r.extension, "count": r.count, "total_size": r.total_size} for r in result.all()]


@router.get("/storage-by-source")
async def get_storage_by_source(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    filters = [File.is_deleted == False]  # noqa: E712
    filters.extend(await _source_filter(user, db))

    stmt = (
        select(
            File.source_id,
            Source.name.label("source_name"),
            func.count(File.id).label("count"),
            func.sum(File.size_bytes).label("total_size"),
        )
        .join(Source, Source.id == File.source_id)
        .where(*filters)
        .group_by(File.source_id, Source.name)
        .order_by(func.sum(File.size_bytes).desc())
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
    filters = [File.is_deleted == False, File.size_bytes.isnot(None)]  # noqa: E712
    filters.extend(await _source_filter(user, db))

    stmt = (
        select(File)
        .where(*filters)
        .order_by(File.size_bytes.desc())
        .limit(n)
    )
    result = await db.execute(stmt)
    files = result.scalars().all()
    return [
        {
            "id": str(f.id),
            "source_id": str(f.source_id),
            "path": f.path,
            "filename": f.filename,
            "size_bytes": f.size_bytes,
            "mime_type": f.mime_type,
        }
        for f in files
    ]
