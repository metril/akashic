import os
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import check_source_access, get_current_user
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.entry import BrowseEntry, BrowseResponse

router = APIRouter(prefix="/api/browse", tags=["browse"])


def _normalize_path(path: str) -> str:
    """Normalize trailing/duplicate slashes; root stays as '/'."""
    if not path or path == "/":
        return "/"
    # Strip trailing slash unless the whole path is just '/'
    return path.rstrip("/") or "/"


@router.get("", response_model=BrowseResponse)
async def browse(
    source_id: uuid.UUID,
    path: str = Query(default="/"),
    sort: Literal["name", "size", "modified"] = "name",
    order: Literal["asc", "desc"] = "asc",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await check_source_access(source_id, user, db)

    source_result = await db.execute(select(Source).where(Source.id == source_id))
    source = source_result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    norm_path = _normalize_path(path)
    parent_path_value = None if norm_path == "/" else (os.path.dirname(norm_path) or "/")
    is_root = norm_path == "/"

    # Subquery: child counts per directory under norm_path.
    # For each entry whose parent_path == norm_path AND kind == 'directory',
    # we need the count of entries whose parent_path == that entry's path.
    sort_col_map = {
        "name": Entry.name,
        "size": Entry.size_bytes,
        "modified": Entry.fs_modified_at,
    }
    sort_col = sort_col_map[sort]

    stmt = (
        select(Entry)
        .where(
            Entry.source_id == source_id,
            Entry.parent_path == norm_path,
            Entry.is_deleted == False,  # noqa: E712
        )
        # Directories first, then sorted column.
        .order_by(
            (Entry.kind != "directory").asc(),
            sort_col.asc() if order == "asc" else sort_col.desc(),
        )
    )
    rows = (await db.execute(stmt)).scalars().all()

    # Single grouped child-count query for any directory child.
    dir_paths = [r.path for r in rows if r.kind == "directory"]
    child_counts: dict[str, int] = {}
    if dir_paths:
        cc_stmt = (
            select(Entry.parent_path, func.count(Entry.id))
            .where(
                Entry.source_id == source_id,
                Entry.parent_path.in_(dir_paths),
                Entry.is_deleted == False,  # noqa: E712
            )
            .group_by(Entry.parent_path)
        )
        for parent, count in (await db.execute(cc_stmt)).all():
            child_counts[parent] = count

    return BrowseResponse(
        source_id=source_id,
        source_name=source.name,
        path=norm_path,
        parent_path=parent_path_value,
        is_root=is_root,
        entries=[
            BrowseEntry(
                id=r.id,
                kind=r.kind,
                name=r.name,
                path=r.path,
                extension=r.extension,
                size_bytes=r.size_bytes,
                mime_type=r.mime_type,
                content_hash=r.content_hash,
                mode=r.mode,
                owner_name=r.owner_name,
                group_name=r.group_name,
                fs_modified_at=r.fs_modified_at,
                child_count=child_counts.get(r.path) if r.kind == "directory" else None,
            )
            for r in rows
        ],
    )
