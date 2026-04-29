import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import check_source_access, get_current_user, require_admin
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.tag import Tag, EntryTag
from akashic.models.user import User
from akashic.schemas.tag import TagCreate, TagResponse

router = APIRouter(tags=["tags"])


@router.post("/api/tags", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
async def create_tag(
    data: TagCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tag = Tag(name=data.name, color=data.color, created_by=user.id)
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag


@router.get("/api/tags", response_model=list[TagResponse])
async def list_tags(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(Tag).order_by(Tag.name))
    return result.scalars().all()


@router.delete("/api/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Deletes a tag and cascades to remove any entry-tag links.

    Admin-only — matches the pattern for sources.delete + every other
    destructive mutation in the API. A tag like 'PII' or 'do-not-share'
    can be applied across many entries, so cascade-delete is destructive
    and global; viewer/editor users shouldn't be able to invoke it.
    """
    tag_result = await db.execute(select(Tag).where(Tag.id == tag_id))
    tag = tag_result.scalar_one_or_none()
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    # Remove the link rows first — Tag has no cascade configured.
    await db.execute(delete(EntryTag).where(EntryTag.tag_id == tag_id))
    await db.delete(tag)
    await db.commit()


@router.post("/api/entries/{entry_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def apply_tag(
    entry_id: uuid.UUID,
    tag_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    entry_result = await db.execute(select(Entry).where(Entry.id == entry_id))
    entry = entry_result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    await check_source_access(entry.source_id, user, db)

    tag_result = await db.execute(select(Tag).where(Tag.id == tag_id))
    if not tag_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Tag not found")

    existing = await db.execute(
        select(EntryTag).where(EntryTag.entry_id == entry_id, EntryTag.tag_id == tag_id)
    )
    if not existing.scalar_one_or_none():
        db.add(EntryTag(entry_id=entry_id, tag_id=tag_id, tagged_by=user.id))
        await db.commit()


@router.delete("/api/entries/{entry_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_tag(
    entry_id: uuid.UUID,
    tag_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    entry_result = await db.execute(select(Entry).where(Entry.id == entry_id))
    entry = entry_result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    await check_source_access(entry.source_id, user, db)

    await db.execute(
        delete(EntryTag).where(EntryTag.entry_id == entry_id, EntryTag.tag_id == tag_id)
    )
    await db.commit()
