from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.file import File


async def storage_by_type(db: AsyncSession):
    stmt = (
        select(File.extension, func.count(File.id).label("count"), func.sum(File.size_bytes).label("total_size"))
        .where(File.is_deleted == False)
        .group_by(File.extension)
        .order_by(func.sum(File.size_bytes).desc())
    )
    result = await db.execute(stmt)
    return [{"extension": r.extension, "count": r.count, "total_size": r.total_size} for r in result.all()]


async def storage_by_source(db: AsyncSession):
    stmt = (
        select(File.source_id, func.count(File.id).label("count"), func.sum(File.size_bytes).label("total_size"))
        .where(File.is_deleted == False)
        .group_by(File.source_id)
        .order_by(func.sum(File.size_bytes).desc())
    )
    result = await db.execute(stmt)
    return [{"source_id": str(r.source_id), "count": r.count, "total_size": r.total_size} for r in result.all()]
