import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import check_source_access, get_current_user
from akashic.database import get_db
from akashic.models.directory import Directory
from akashic.models.file import File, FileEvent, FileVersion
from akashic.models.scan import Scan
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.scan import ScanBatchIn, ScanBatchResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


async def _index_files_to_meilisearch(file_ids: list[str], db_url: str):
    """Background task: index ingested files into Meilisearch."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from akashic.services.search import index_files_batch

    engine = create_async_engine(db_url)
    session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with session() as db:
            for file_id in file_ids:
                import uuid

                result = await db.execute(select(File).where(File.id == uuid.UUID(file_id)))
                f = result.scalar_one_or_none()
                if not f:
                    continue

                await index_files_batch([{
                    "id": str(f.id),
                    "source_id": str(f.source_id),
                    "path": f.path,
                    "filename": f.filename,
                    "extension": f.extension,
                    "mime_type": f.mime_type,
                    "size_bytes": f.size_bytes,
                    "fs_modified_at": int(f.fs_modified_at.timestamp()) if f.fs_modified_at else None,
                    "tags": [],
                }])
    except Exception as exc:
        logger.warning("Meilisearch indexing failed: %s", exc)
    finally:
        await engine.dispose()


def _enqueue_extraction_jobs(file_ids: list[str], redis_url: str):
    """Background task: enqueue text extraction jobs to Redis.

    This is a sync function so FastAPI dispatches it to a thread pool,
    avoiding blocking the event loop with synchronous Redis I/O.
    """
    try:
        from redis import Redis
        from rq import Queue

        conn = Redis.from_url(redis_url)
        q = Queue("extraction", connection=conn)
        for file_id in file_ids:
            q.enqueue("akashic.workers.extraction.process_file_extraction", file_id)
        logger.info("Enqueued %d extraction jobs", len(file_ids))
    except Exception as exc:
        logger.warning("Failed to enqueue extraction jobs: %s", exc)


async def _dispatch_scan_webhooks(scan_id: str, source_id: str, status: str, db_url: str):
    """Background task: dispatch webhooks for scan events."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from akashic.models.webhook import Webhook
    from akashic.services.webhooks import dispatch_webhook

    engine = create_async_engine(db_url)
    session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    event_type = f"scan.{status}"
    try:
        async with session() as db:
            result = await db.execute(
                select(Webhook).where(Webhook.event_type == event_type, Webhook.enabled == True)  # noqa: E712
            )
            webhooks = result.scalars().all()
            for webhook in webhooks:
                await dispatch_webhook(webhook, {
                    "event": event_type,
                    "scan_id": scan_id,
                    "source_id": source_id,
                })
    except Exception as exc:
        logger.warning("Webhook dispatch failed: %s", exc)
    finally:
        await engine.dispose()


@router.post("/batch", response_model=ScanBatchResponse)
async def ingest_batch(
    batch: ScanBatchIn,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await check_source_access(batch.source_id, user, db, required_level="write")
    now = datetime.now(timezone.utc)

    result = await db.execute(select(Scan).where(Scan.id == batch.scan_id))
    scan = result.scalar_one_or_none()
    if scan:
        # Verify scan belongs to the claimed source
        if scan.source_id != batch.source_id:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="scan_id does not belong to this source")
    else:
        scan = Scan(
            id=batch.scan_id,
            source_id=batch.source_id,
            scan_type="incremental",
            status="running",
            started_at=now,
        )
        db.add(scan)

    files_processed = 0
    new_file_ids: list[str] = []

    for entry in batch.files:
        if entry.is_dir:
            stmt = insert(Directory).values(
                source_id=batch.source_id,
                path=entry.path,
                name=entry.filename,
                last_seen_at=now,
            ).on_conflict_do_update(
                constraint="uq_directories_source_path",
                set_={"last_seen_at": now, "is_deleted": False},
            )
            await db.execute(stmt)
        else:
            existing_result = await db.execute(
                select(File).where(File.source_id == batch.source_id, File.path == entry.path)
            )
            existing = existing_result.scalar_one_or_none()

            if existing:
                old_hash = existing.content_hash
                existing.filename = entry.filename
                existing.extension = entry.extension
                existing.size_bytes = entry.size_bytes
                existing.mime_type = entry.mime_type
                existing.content_hash = entry.content_hash
                existing.permissions = entry.permissions
                existing.owner = entry.owner
                existing.file_group = entry.file_group
                existing.fs_created_at = entry.fs_created_at
                existing.fs_modified_at = entry.fs_modified_at
                existing.fs_accessed_at = entry.fs_accessed_at
                existing.last_seen_at = now
                existing.is_deleted = False
                existing.deleted_at = None

                if old_hash and entry.content_hash and old_hash != entry.content_hash:
                    version = FileVersion(
                        file_id=existing.id,
                        content_hash=entry.content_hash,
                        size_bytes=entry.size_bytes,
                        scan_id=batch.scan_id,
                    )
                    db.add(version)
                    scan.files_changed += 1
                # Re-index changed files
                new_file_ids.append(str(existing.id))
            else:
                new_file = File(
                    source_id=batch.source_id,
                    path=entry.path,
                    filename=entry.filename,
                    extension=entry.extension,
                    size_bytes=entry.size_bytes,
                    mime_type=entry.mime_type,
                    content_hash=entry.content_hash,
                    permissions=entry.permissions,
                    owner=entry.owner,
                    file_group=entry.file_group,
                    fs_created_at=entry.fs_created_at,
                    fs_modified_at=entry.fs_modified_at,
                    fs_accessed_at=entry.fs_accessed_at,
                    first_seen_at=now,
                    last_seen_at=now,
                )
                db.add(new_file)
                await db.flush()  # Get the ID for indexing
                new_file_ids.append(str(new_file.id))
                scan.files_new += 1

        files_processed += 1

    scan.files_found += files_processed

    if batch.is_final:
        scan.status = "completed"
        scan.completed_at = now

        # Update source.last_scan_at
        source_result = await db.execute(select(Source).where(Source.id == batch.source_id))
        source = source_result.scalar_one_or_none()
        if source:
            source.last_scan_at = now
            source.status = "online"

        # Mark files not seen in this scan as deleted
        if scan.started_at:
            stale_result = await db.execute(
                select(File).where(
                    File.source_id == batch.source_id,
                    File.is_deleted == False,  # noqa: E712
                    File.last_seen_at < scan.started_at,
                )
            )
            stale_files = stale_result.scalars().all()
            for stale in stale_files:
                stale.is_deleted = True
                stale.deleted_at = now
                scan.files_deleted += 1

                if stale.content_hash:
                    new_location = await db.execute(
                        select(File).where(
                            File.content_hash == stale.content_hash,
                            File.is_deleted == False,  # noqa: E712
                            File.last_seen_at >= scan.started_at,
                            File.id != stale.id,
                        ).limit(1)
                    )
                    new_file_record = new_location.scalar_one_or_none()
                    if new_file_record:
                        event = FileEvent(
                            event_type="moved",
                            content_hash=stale.content_hash,
                            old_source_id=stale.source_id,
                            old_path=stale.path,
                            new_source_id=new_file_record.source_id,
                            new_path=new_file_record.path,
                            scan_id=batch.scan_id,
                        )
                        db.add(event)

    await db.commit()

    # Background tasks: index to Meilisearch, enqueue extraction, dispatch webhooks
    from akashic.config import settings

    if new_file_ids:
        background_tasks.add_task(_index_files_to_meilisearch, new_file_ids, settings.database_url)
        background_tasks.add_task(_enqueue_extraction_jobs, new_file_ids, settings.redis_url)

    if batch.is_final:
        background_tasks.add_task(
            _dispatch_scan_webhooks,
            str(batch.scan_id), str(batch.source_id), scan.status, settings.database_url,
        )

    return ScanBatchResponse(files_processed=files_processed, scan_id=batch.scan_id)
