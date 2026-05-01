import os
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import check_source_access, get_current_user
from akashic.config import settings
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.entry import BrowseEntry, BrowseResponse
from akashic.services.access_query import (
    user_has_any_bindings,
    user_principal_tokens,
    viewable_clause,
)
from akashic.services.audit import record_event
from akashic.services.filter_grammar import (
    SourcePred,
    parse as parse_filters,
    to_sqlalchemy as filters_to_sqlalchemy,
)

router = APIRouter(prefix="/api/browse", tags=["browse"])


def _normalize_path(path: str) -> str:
    """Normalize trailing/duplicate slashes; root stays as '/'."""
    if not path or path == "/":
        return "/"
    # Strip trailing slash unless the whole path is just '/'
    return path.rstrip("/") or "/"


async def _should_enforce_perms(user: User, show_all: bool, db: AsyncSession) -> bool:
    """Decide whether to apply the per-user ACL filter to this request.

    Three gates: the deployment-wide feature flag, the admin's explicit
    opt-out (`show_all=1`), and the "user has any bindings at all" check
    (a user with no FsBindings would see nothing — no filter for them
    until an admin attaches one).
    """
    if not settings.browse_enforce_perms:
        return False
    if user.role == "admin" and show_all:
        return False
    return await user_has_any_bindings(user, db)


@router.get("", response_model=BrowseResponse)
async def browse(
    source_id: uuid.UUID,
    path: str = Query(default="/"),
    sort: Literal["name", "size", "modified"] = "name",
    order: Literal["asc", "desc"] = "asc",
    show_all: bool = Query(default=False),
    filters: str | None = Query(default=None, description="base64url(json) predicate list"),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await check_source_access(source_id, user, db)

    # Phase-6 grammar predicates. Browse is single-source-scoped, so a
    # `source` predicate is a category error — those queries belong in
    # Search. 400 with a hint instead of silently dropping.
    try:
        grammar_preds = parse_filters(filters) if filters else []
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if any(isinstance(p, SourcePred) for p in grammar_preds):
        raise HTTPException(
            status_code=400,
            detail="Cross-source filters belong in Search; remove the `source` predicate or open Search.",
        )

    source_result = await db.execute(select(Source).where(Source.id == source_id))
    source = source_result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    norm_path = _normalize_path(path)
    parent_path_value = None if norm_path == "/" else (os.path.dirname(norm_path) or "/")
    is_root = norm_path == "/"

    sort_col_map = {
        "name": Entry.name,
        "size": Entry.size_bytes,
        "modified": Entry.fs_modified_at,
    }
    sort_col = sort_col_map[sort]

    base_filter = [
        Entry.source_id == source_id,
        Entry.parent_path == norm_path,
        Entry.is_deleted == False,  # noqa: E712
    ]

    enforce = await _should_enforce_perms(user, show_all, db)
    perm_filter = None
    if enforce:
        tokens = await user_principal_tokens(user, db)
        perm_filter = viewable_clause(tokens, "read")
        base_filter.append(perm_filter)

    if grammar_preds:
        base_filter.extend(filters_to_sqlalchemy(grammar_preds))

    stmt = (
        select(Entry)
        .where(and_(*base_filter))
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
        cc_filter = [
            Entry.source_id == source_id,
            Entry.parent_path.in_(dir_paths),
            Entry.is_deleted == False,  # noqa: E712
        ]
        if perm_filter is not None:
            cc_filter.append(perm_filter)
        cc_stmt = (
            select(Entry.parent_path, func.count(Entry.id))
            .where(and_(*cc_filter))
            .group_by(Entry.parent_path)
        )
        for parent, count in (await db.execute(cc_stmt)).all():
            child_counts[parent] = count

    if enforce:
        # Audit how often the filter actually hides things — gives admins
        # a way to see "user X looked at /finance and saw 0 of 50 items"
        # without per-row logging. Only fires when at least one entry was
        # hidden (the same query without the filter would return more).
        await _maybe_audit_filter(
            db=db, user=user, request=request,
            source_id=source_id, path=norm_path,
            visible_count=len(rows),
            base_filter_no_perm=[c for c in base_filter if c is not perm_filter],
        )

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


async def _maybe_audit_filter(
    *,
    db: AsyncSession,
    user: User,
    request: Request | None,
    source_id: uuid.UUID,
    path: str,
    visible_count: int,
    base_filter_no_perm: list,
) -> None:
    """Emit a `browse_filtered` audit event when the per-user ACL filter
    actually hid something. Skips no-op fires (would otherwise log every
    paginated browse to a public folder)."""
    total = (await db.execute(
        select(func.count(Entry.id)).where(and_(*base_filter_no_perm))
    )).scalar() or 0
    hidden = total - visible_count
    if hidden <= 0:
        return
    await record_event(
        db=db, user=user,
        event_type="browse_filtered",
        payload={"path": path, "visible": visible_count, "hidden": hidden},
        request=request,
        source_id=source_id,
    )


@router.get("/effective-counts")
async def effective_counts(
    source_id: uuid.UUID,
    path: str = Query(default="/"),
    show_all: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """How many entries under `path` the user can/can't see — feeds the
    Browse "X items hidden" footer. Cheap (two COUNT(*) queries with the
    same indexes Browse uses)."""
    await check_source_access(source_id, user, db)
    norm_path = _normalize_path(path)

    base_filter = [
        Entry.source_id == source_id,
        Entry.parent_path == norm_path,
        Entry.is_deleted == False,  # noqa: E712
    ]
    total = (await db.execute(
        select(func.count(Entry.id)).where(and_(*base_filter))
    )).scalar() or 0

    enforce = await _should_enforce_perms(user, show_all, db)
    if not enforce:
        return {"visible": total, "hidden": 0, "enforced": False}

    tokens = await user_principal_tokens(user, db)
    visible_filter = base_filter + [viewable_clause(tokens, "read")]
    visible = (await db.execute(
        select(func.count(Entry.id)).where(and_(*visible_filter))
    )).scalar() or 0
    return {"visible": visible, "hidden": total - visible, "enforced": True}
