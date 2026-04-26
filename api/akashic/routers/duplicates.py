from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user, get_permitted_source_ids
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.user import User

router = APIRouter(prefix="/api/duplicates", tags=["duplicates"])


@router.get("")
async def list_duplicates(
    min_size: int | None = None,
    offset: int = 0,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    base_filter = [
        Entry.kind == "file",
        Entry.is_deleted == False,  # noqa: E712
        Entry.content_hash.isnot(None),
    ]

    allowed = await get_permitted_source_ids(user, db)
    if allowed is not None:
        if not allowed:
            return []
        base_filter.append(Entry.source_id.in_(allowed))

    stmt = (
        select(
            Entry.content_hash,
            func.count(Entry.id).label("count"),
            func.sum(Entry.size_bytes).label("total_size"),
            func.min(Entry.size_bytes).label("file_size"),
        )
        .where(*base_filter)
        .group_by(Entry.content_hash)
        .having(func.count(Entry.id) > 1)
    )
    if min_size:
        stmt = stmt.having(func.min(Entry.size_bytes) >= min_size)
    stmt = stmt.order_by(func.sum(Entry.size_bytes).desc()).offset(offset).limit(limit)

    result = await db.execute(stmt)
    rows = result.all()

    return [
        {
            "content_hash": row.content_hash,
            "count": row.count,
            "total_size": row.total_size,
            "file_size": row.file_size,
            "wasted_bytes": (row.count - 1) * row.file_size,
        }
        for row in rows
    ]


@router.get("/{content_hash}/files")
async def get_duplicate_files(
    content_hash: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(Entry).where(
        Entry.kind == "file",
        Entry.content_hash == content_hash,
        Entry.is_deleted == False,  # noqa: E712
    )

    allowed = await get_permitted_source_ids(user, db)
    if allowed is not None:
        if not allowed:
            return []
        stmt = stmt.where(Entry.source_id.in_(allowed))

    result = await db.execute(stmt)
    entries = result.scalars().all()
    return [
        {
            "id": str(e.id),
            "source_id": str(e.source_id),
            "path": e.path,
            "filename": e.name,
            "extension": e.extension,
            "size_bytes": e.size_bytes,
            "content_hash": e.content_hash,
            "mime_type": e.mime_type,
            "fs_modified_at": e.fs_modified_at.isoformat() if e.fs_modified_at else None,
            "first_seen_at": e.first_seen_at.isoformat(),
            "last_seen_at": e.last_seen_at.isoformat(),
            "is_deleted": e.is_deleted,
        }
        for e in entries
    ]
