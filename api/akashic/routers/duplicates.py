from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.models.file import File
from akashic.models.user import User

router = APIRouter(prefix="/api/duplicates", tags=["duplicates"])


@router.get("")
async def list_duplicates(
    min_size: int | None = None,
    offset: int = 0,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = (
        select(
            File.content_hash,
            func.count(File.id).label("count"),
            func.sum(File.size_bytes).label("total_size"),
            func.min(File.size_bytes).label("file_size"),
        )
        .where(File.is_deleted == False, File.content_hash.isnot(None))
        .group_by(File.content_hash)
        .having(func.count(File.id) > 1)
    )
    if min_size:
        stmt = stmt.having(func.min(File.size_bytes) >= min_size)
    stmt = stmt.order_by(func.sum(File.size_bytes).desc()).offset(offset).limit(limit)

    result = await db.execute(stmt)
    rows = result.all()

    return [
        {
            "content_hash": row.content_hash,
            "count": row.count,
            "total_size": row.total_size,
            "file_size": row.file_size,
            "wasted_bytes": (row.count - 1) * row.file_size,
        }
        for row in rows
    ]


@router.get("/{content_hash}/files")
async def get_duplicate_files(
    content_hash: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(File).where(File.content_hash == content_hash, File.is_deleted == False)
    )
    return result.scalars().all()
