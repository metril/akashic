from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.models.directory import Directory
from akashic.models.file import File, FileVersion
from akashic.models.scan import Scan
from akashic.models.user import User
from akashic.schemas.scan import ScanBatchIn, ScanBatchResponse

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.post("/batch", response_model=ScanBatchResponse)
async def ingest_batch(
    batch: ScanBatchIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)

    result = await db.execute(select(Scan).where(Scan.id == batch.scan_id))
    scan = result.scalar_one_or_none()
    if not scan:
        scan = Scan(
            id=batch.scan_id,
            source_id=batch.source_id,
            scan_type="incremental",
            status="running",
            started_at=now,
        )
        db.add(scan)

    files_processed = 0

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
                scan.files_new += 1

        files_processed += 1

    scan.files_found += files_processed

    if batch.is_final:
        scan.status = "completed"
        scan.completed_at = now

    await db.commit()

    return ScanBatchResponse(files_processed=files_processed, scan_id=batch.scan_id)
