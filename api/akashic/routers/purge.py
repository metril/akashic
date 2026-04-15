import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import require_admin
from akashic.database import get_db
from akashic.models.directory import Directory
from akashic.models.file import File
from akashic.models.user import User
from akashic.models.webhook import PurgeLog

router = APIRouter(prefix="/api/purge", tags=["purge"])


@router.post("/source/{source_id}")
async def purge_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    files_result = await db.execute(select(File).where(File.source_id == source_id))
    files = files_result.scalars().all()
    files_count = len(files)

    dirs_result = await db.execute(select(Directory).where(Directory.source_id == source_id))
    dirs = dirs_result.scalars().all()
    dirs_count = len(dirs)

    await db.execute(delete(File).where(File.source_id == source_id))
    await db.execute(delete(Directory).where(Directory.source_id == source_id))

    log = PurgeLog(
        purge_type="source",
        target=str(source_id),
        records_removed=files_count + dirs_count,
        performed_by=admin.id,
    )
    db.add(log)
    await db.commit()

    return {
        "source_id": str(source_id),
        "files_removed": files_count,
        "directories_removed": dirs_count,
        "total_removed": files_count + dirs_count,
    }


@router.post("/deleted")
async def purge_deleted(
    older_than_days: int = Query(default=30, ge=1),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    threshold = datetime.now(timezone.utc) - timedelta(days=older_than_days)

    files_result = await db.execute(
        select(File).where(File.is_deleted == True, File.deleted_at <= threshold)
    )
    files = files_result.scalars().all()
    files_count = len(files)

    await db.execute(
        delete(File).where(File.is_deleted == True, File.deleted_at <= threshold)
    )

    log = PurgeLog(
        purge_type="deleted",
        target=f"older_than_{older_than_days}_days",
        records_removed=files_count,
        performed_by=admin.id,
    )
    db.add(log)
    await db.commit()

    return {
        "files_removed": files_count,
        "threshold_days": older_than_days,
    }
