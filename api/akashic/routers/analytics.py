from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.models.file import File
from akashic.models.user import User
from akashic.services.analytics import storage_by_type, storage_by_source

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/storage-by-type")
async def get_storage_by_type(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await storage_by_type(db)


@router.get("/storage-by-source")
async def get_storage_by_source(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await storage_by_source(db)


@router.get("/largest-files")
async def get_largest_files(
    n: int = Query(default=10, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = (
        select(File)
        .where(File.is_deleted == False, File.size_bytes.isnot(None))
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
