import logging
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import require_admin
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.models.webhook import PurgeLog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/purge", tags=["purge"])

_PURGE_CHUNK = 5000


async def _cleanup_meilisearch_for_source(source_id: str, db_url: str):
    """Background task: remove purged entries from Meilisearch."""
    try:
        from akashic.services.search import get_meili_client, INDEX_NAME
        client = await get_meili_client()
        index = await client.get_index(INDEX_NAME)
        await index.delete_documents_by_filter(f'source_id = "{source_id}"')
        logger.info("Meilisearch documents purged for source %s", source_id)
    except Exception as exc:
        logger.warning("Meilisearch cleanup failed for source %s: %s", source_id, exc)


@router.post("/source/{source_id}")
async def purge_source(
    source_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    source_result = await db.execute(select(Source).where(Source.id == source_id))
    if not source_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Source not found")

    file_count_result = await db.execute(
        select(func.count(Entry.id)).where(
            Entry.source_id == source_id, Entry.kind == "file"
        )
    )
    file_count = file_count_result.scalar() or 0

    dir_count_result = await db.execute(
        select(func.count(Entry.id)).where(
            Entry.source_id == source_id, Entry.kind == "directory"
        )
    )
    dir_count = dir_count_result.scalar() or 0

    await db.execute(delete(Entry).where(Entry.source_id == source_id))

    log = PurgeLog(
        purge_type="source",
        target=str(source_id),
        records_removed=file_count + dir_count,
        performed_by=admin.id,
    )
    db.add(log)
    await db.commit()

    from akashic.config import settings
    background_tasks.add_task(
        _cleanup_meilisearch_for_source, str(source_id), settings.database_url
    )

    return {
        "source_id": str(source_id),
        "files_removed": file_count,
        "directories_removed": dir_count,
        "total_removed": file_count + dir_count,
    }


@router.post("/deleted")
async def purge_deleted(
    older_than_days: int = Query(default=30, ge=1),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    threshold = datetime.now(timezone.utc) - timedelta(days=older_than_days)

    # Delete in id-keyed chunks so one huge purge never holds a single
    # giant transaction / materialises every id in memory. Each chunk
    # commits, then its file docs are removed from the search index.
    files_count = 0
    total_count = 0
    while True:
        chunk_ids = select(Entry.id).where(
            Entry.is_deleted == True,  # noqa: E712
            Entry.deleted_at <= threshold,
        ).limit(_PURGE_CHUNK)
        removed = (await db.execute(
            delete(Entry)
            .where(Entry.id.in_(chunk_ids))
            .returning(Entry.id, Entry.kind)
        )).all()
        await db.commit()
        if not removed:
            break
        total_count += len(removed)
        file_ids = [str(r.id) for r in removed if r.kind == "file"]
        files_count += len(file_ids)
        if file_ids:
            try:
                from akashic.services.search import delete_files_batch
                await delete_files_batch(file_ids)
            except Exception as exc:
                logger.warning("Meilisearch cleanup for deleted files failed: %s", exc)

    log = PurgeLog(
        purge_type="deleted",
        target=f"older_than_{older_than_days}_days",
        records_removed=total_count,
        performed_by=admin.id,
    )
    db.add(log)
    await db.commit()

    return {
        "files_removed": files_count,
        "total_removed": total_count,
        "threshold_days": older_than_days,
    }
