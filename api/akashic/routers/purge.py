import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, delete, func
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
    # Count first without loading rows into memory
    files_count_result = await db.execute(
        select(func.count(File.id)).where(File.source_id == source_id)
    )
    files_count = files_count_result.scalar() or 0

    dirs_count_result = await db.execute(
        select(func.count(Directory.id)).where(Directory.source_id == source_id)
    )
    dirs_count = dirs_count_result.scalar() or 0

    # Delete directly without loading into Python
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

    # Count first without loading rows into memory
    files_count_result = await db.execute(
        select(func.count(File.id)).where(
            File.is_deleted == True, File.deleted_at <= threshold  # noqa: E712
        )
    )
    files_count = files_count_result.scalar() or 0

    # Delete directly
    await db.execute(
        delete(File).where(
            File.is_deleted == True, File.deleted_at <= threshold  # noqa: E712
        )
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
