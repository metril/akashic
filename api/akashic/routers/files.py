import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import check_source_access, get_current_user
from akashic.database import get_db
from akashic.models.file import File, FileVersion
from akashic.models.user import User
from akashic.schemas.file import FileResponse

router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("", response_model=list[FileResponse])
async def list_files(
    source_id: uuid.UUID | None = None,
    extension: str | None = None,
    path_prefix: str | None = None,
    offset: int = 0,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if source_id:
        await check_source_access(source_id, user, db)
    stmt = select(File).where(File.is_deleted == False)
    if source_id:
        stmt = stmt.where(File.source_id == source_id)
    if extension:
        stmt = stmt.where(File.extension == extension)
    if path_prefix:
        stmt = stmt.where(File.path.startswith(path_prefix))
    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{file_id}", response_model=FileResponse)
async def get_file(
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(File).where(File.id == file_id))
    f = result.scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    await check_source_access(f.source_id, user, db)
    return f


@router.get("/{file_id}/versions")
async def get_file_versions(
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Verify access to the file's source
    file_result = await db.execute(select(File).where(File.id == file_id))
    f = file_result.scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    await check_source_access(f.source_id, user, db)

    result = await db.execute(
        select(FileVersion).where(FileVersion.file_id == file_id).order_by(FileVersion.detected_at.desc())
    )
    return result.scalars().all()


@router.get("/{file_id}/locations")
async def get_file_locations(
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    file_result = await db.execute(select(File).where(File.id == file_id))
    f = file_result.scalar_one_or_none()
    if not f or not f.content_hash:
        raise HTTPException(status_code=404, detail="File not found or no hash")
    await check_source_access(f.source_id, user, db)

    # Only return locations from sources the user has access to
    all_files_result = await db.execute(
        select(File).where(File.content_hash == f.content_hash, File.is_deleted == False)  # noqa: E712
    )
    all_files = all_files_result.scalars().all()

    # Filter to sources the user can access (admins see all)
    if user.role == "admin":
        return all_files

    from akashic.models.user import SourcePermission
    perms_result = await db.execute(
        select(SourcePermission.source_id).where(SourcePermission.user_id == user.id)
    )
    allowed_sources = {row[0] for row in perms_result.all()}
    return [f for f in all_files if f.source_id in allowed_sources]
