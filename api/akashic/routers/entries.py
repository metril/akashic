import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import (
    check_source_access,
    get_current_user,
    get_permitted_source_ids,
)
from akashic.database import get_db
from akashic.models.entry import Entry, EntryVersion
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.entry import (
    EntryDetailResponse,
    EntryResponse,
    EntryVersionResponse,
    _EntrySourceRef,
)
from akashic.services.access_query import user_can_view

router = APIRouter(prefix="/api/entries", tags=["entries"])


@router.get("", response_model=list[EntryResponse])
async def list_entries(
    source_id: uuid.UUID | None = None,
    kind: str | None = None,
    extension: str | None = None,
    path_prefix: str | None = None,
    offset: int = 0,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if source_id:
        await check_source_access(source_id, user, db)

    stmt = select(Entry).where(Entry.is_deleted == False)  # noqa: E712
    if source_id:
        stmt = stmt.where(Entry.source_id == source_id)
    else:
        allowed = await get_permitted_source_ids(user, db)
        if allowed is not None:
            if not allowed:
                return []
            stmt = stmt.where(Entry.source_id.in_(allowed))
    if kind:
        stmt = stmt.where(Entry.kind == kind)
    if extension:
        stmt = stmt.where(Entry.extension == extension)
    if path_prefix:
        stmt = stmt.where(Entry.path.startswith(path_prefix))
    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/by-hash/{content_hash}", response_model=list[EntryResponse])
async def get_entries_by_hash(
    content_hash: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """All file entries sharing a given content_hash, scoped to permitted sources."""
    stmt = select(Entry).where(
        Entry.content_hash == content_hash,
        Entry.kind == "file",
        Entry.is_deleted == False,  # noqa: E712
    )
    allowed = await get_permitted_source_ids(user, db)
    if allowed is not None:
        if not allowed:
            return []
        stmt = stmt.where(Entry.source_id.in_(allowed))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{entry_id}", response_model=EntryDetailResponse)
async def get_entry(
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(Entry).where(Entry.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    await check_source_access(entry.source_id, user, db)
    # Same filter Browse applies — and 404 (not 403) when the user
    # can't see the entry. SharePoint-correct: not-found denies the
    # existence-inference attack ("if I get 403 here, the entry
    # exists; if I get 404, it doesn't").
    if not await user_can_view(entry, user, db):
        raise HTTPException(status_code=404, detail="Entry not found")

    versions_result = await db.execute(
        select(EntryVersion)
        .where(EntryVersion.entry_id == entry_id)
        .order_by(EntryVersion.detected_at.desc())
    )
    versions = versions_result.scalars().all()

    source_result = await db.execute(select(Source).where(Source.id == entry.source_id))
    source = source_result.scalar_one_or_none()

    return EntryDetailResponse(
        id=entry.id,
        source_id=entry.source_id,
        kind=entry.kind,
        parent_path=entry.parent_path,
        path=entry.path,
        name=entry.name,
        extension=entry.extension,
        size_bytes=entry.size_bytes,
        mime_type=entry.mime_type,
        content_hash=entry.content_hash,
        mode=entry.mode,
        uid=entry.uid,
        gid=entry.gid,
        owner_name=entry.owner_name,
        group_name=entry.group_name,
        acl=entry.acl,
        xattrs=entry.xattrs,
        fs_created_at=entry.fs_created_at,
        fs_modified_at=entry.fs_modified_at,
        fs_accessed_at=entry.fs_accessed_at,
        first_seen_at=entry.first_seen_at,
        last_seen_at=entry.last_seen_at,
        is_deleted=entry.is_deleted,
        versions=[EntryVersionResponse.model_validate(v) for v in versions],
        source=_EntrySourceRef.model_validate(source) if source else None,
    )


@router.get("/{entry_id}/versions", response_model=list[EntryVersionResponse])
async def get_entry_versions(
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    entry_result = await db.execute(select(Entry).where(Entry.id == entry_id))
    entry = entry_result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    await check_source_access(entry.source_id, user, db)
    if not await user_can_view(entry, user, db):
        raise HTTPException(status_code=404, detail="Entry not found")

    result = await db.execute(
        select(EntryVersion)
        .where(EntryVersion.entry_id == entry_id)
        .order_by(EntryVersion.detected_at.desc())
    )
    return result.scalars().all()
