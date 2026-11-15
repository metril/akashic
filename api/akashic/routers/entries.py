import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import Principal, check_source_access, require_scope
from akashic.auth.source_scope import ensure_source_allowed, restrict_sources
from akashic.database import get_db
from akashic.models.entry import Entry, EntryVersion
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.entry import (
    EntryDetailResponse,
    EntryResponse,
    EntryTagAssignment,
    EntryVersionResponse,
    _EntrySourceRef,
)
from akashic.schemas.common import Page, offset_from_cursor, offset_page
from akashic.routers.browse import _should_enforce_perms
from akashic.services.access_query import (
    user_can_view,
    user_principal_tokens,
    viewable_clause,
)
from akashic.services.tag_inheritance import get_tags_for_entry

router = APIRouter(prefix="/entries", tags=["entries"])


@router.get("", response_model=Page[EntryResponse], summary="List entries")
async def list_entries(
    source_id: uuid.UUID | None = None,
    kind: str | None = None,
    extension: str | None = None,
    path_prefix: str | None = None,
    cursor: str | None = Query(default=None, description="Opaque cursor from a previous `next_cursor`."),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("files:read", source_scoped=True),
):
    user = principal.user
    offset = offset_from_cursor(cursor)
    if source_id:
        await check_source_access(source_id, principal, db)

    stmt = restrict_sources(select(Entry).where(Entry.is_deleted == False), principal)  # noqa: E712
    if source_id:
        stmt = stmt.where(Entry.source_id == source_id)
    if kind:
        stmt = stmt.where(Entry.kind == kind)
    if extension:
        stmt = stmt.where(Entry.extension == extension)
    if path_prefix:
        stmt = stmt.where(Entry.path.startswith(path_prefix))
    if await _should_enforce_perms(principal, True, db):
        stmt = stmt.where(viewable_clause(await user_principal_tokens(user, db), "read"))
    stmt = stmt.order_by(Entry.path).offset(offset).limit(limit + 1)
    result = await db.execute(stmt)
    return offset_page(list(result.scalars().all()), offset, limit)


@router.get("/by-hash/{content_hash}", response_model=list[EntryResponse])
async def get_entries_by_hash(
    content_hash: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("files:read", source_scoped=True),
):
    """All file entries sharing a given content_hash, scoped to permitted sources."""
    user = principal.user
    stmt = restrict_sources(select(Entry).where(
        Entry.content_hash == content_hash,
        Entry.kind == "file",
        Entry.is_deleted == False,  # noqa: E712
    ), principal)
    if await _should_enforce_perms(principal, True, db):
        stmt = stmt.where(viewable_clause(await user_principal_tokens(user, db), "read"))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{entry_id}", response_model=EntryDetailResponse)
async def get_entry(
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("files:read", source_scoped=True),
):
    user = principal.user
    result = await db.execute(select(Entry).where(Entry.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    # Per-source ACL gate. Orphaned entries (source deleted, entry
    # preserved via the v0.4.0 SET NULL FK) skip this — there's no
    # source row to gate on. Per-entry visibility still applies via
    # `user_can_view` below, so non-admins who legitimately had
    # access at scan time keep it.
    ensure_source_allowed(principal, entry.source_id, "Entry not found")
    if entry.source_id is not None:
        await check_source_access(entry.source_id, principal, db)
    # Same filter Browse applies — and 404 (not 403) when the user
    # can't see the entry. Defends against the existence-inference
    # attack ("if I get 403 here, the entry exists; if I get 404, it
    # doesn't").
    if not await user_can_view(entry, principal, db):
        raise HTTPException(status_code=404, detail="Entry not found")

    versions_result = await db.execute(
        select(EntryVersion)
        .where(EntryVersion.entry_id == entry_id)
        .order_by(EntryVersion.detected_at.desc())
    )
    versions = versions_result.scalars().all()

    source = None
    if entry.source_id is not None:
        source_result = await db.execute(select(Source).where(Source.id == entry.source_id))
        source = source_result.scalar_one_or_none()

    raw_tags = await get_tags_for_entry(db, entry_id=entry.id)
    tags = [EntryTagAssignment(**t) for t in raw_tags]

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
        domain_metadata=entry.domain_metadata,
        fs_created_at=entry.fs_created_at,
        fs_modified_at=entry.fs_modified_at,
        fs_accessed_at=entry.fs_accessed_at,
        first_seen_at=entry.first_seen_at,
        last_seen_at=entry.last_seen_at,
        is_deleted=entry.is_deleted,
        versions=[EntryVersionResponse.model_validate(v) for v in versions],
        source=_EntrySourceRef.model_validate(source) if source else None,
        tags=tags,
    )


@router.get("/{entry_id}/versions", response_model=list[EntryVersionResponse])
async def get_entry_versions(
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("files:read", source_scoped=True),
):
    user = principal.user
    entry_result = await db.execute(select(Entry).where(Entry.id == entry_id))
    entry = entry_result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    ensure_source_allowed(principal, entry.source_id, "Entry not found")
    if entry.source_id is not None:
        await check_source_access(entry.source_id, principal, db)
    if not await user_can_view(entry, principal, db):
        raise HTTPException(status_code=404, detail="Entry not found")

    result = await db.execute(
        select(EntryVersion)
        .where(EntryVersion.entry_id == entry_id)
        .order_by(EntryVersion.detected_at.desc())
    )
    return result.scalars().all()
