import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import check_source_access, get_current_user, get_permitted_source_ids
from akashic.database import get_db
from akashic.models.directory import Directory
from akashic.models.user import User

router = APIRouter(prefix="/api/directories", tags=["directories"])


@router.get("")
async def list_directories(
    source_id: uuid.UUID | None = None,
    path_prefix: str | None = None,
    offset: int = 0,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if source_id:
        await check_source_access(source_id, user, db)

    stmt = select(Directory).where(Directory.is_deleted == False)  # noqa: E712
    if source_id:
        stmt = stmt.where(Directory.source_id == source_id)
    else:
        allowed = await get_permitted_source_ids(user, db)
        if allowed is not None:
            stmt = stmt.where(Directory.source_id.in_(allowed)) if allowed else stmt.where(False)
    if path_prefix:
        stmt = stmt.where(Directory.path.startswith(path_prefix))
    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()
