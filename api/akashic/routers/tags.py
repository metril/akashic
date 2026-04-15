import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import check_source_access, get_current_user
from akashic.database import get_db
from akashic.models.tag import Tag, FileTag
from akashic.models.file import File
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


@router.post("/api/files/{file_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def apply_tag(
    file_id: uuid.UUID,
    tag_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    file_result = await db.execute(select(File).where(File.id == file_id))
    f = file_result.scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    await check_source_access(f.source_id, user, db)

    tag_result = await db.execute(select(Tag).where(Tag.id == tag_id))
    if not tag_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Tag not found")

    existing = await db.execute(
        select(FileTag).where(FileTag.file_id == file_id, FileTag.tag_id == tag_id)
    )
    if not existing.scalar_one_or_none():
        file_tag = FileTag(file_id=file_id, tag_id=tag_id, tagged_by=user.id)
        db.add(file_tag)
        await db.commit()


@router.delete("/api/files/{file_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_tag(
    file_id: uuid.UUID,
    tag_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    file_result = await db.execute(select(File).where(File.id == file_id))
    f = file_result.scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    await check_source_access(f.source_id, user, db)

    await db.execute(
        delete(FileTag).where(FileTag.file_id == file_id, FileTag.tag_id == tag_id)
    )
    await db.commit()
