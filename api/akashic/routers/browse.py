import base64
import json
import logging
import os
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import Integer, and_, cast, func, or_, select
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

logger = logging.getLogger(__name__)

# v0.4.11 — cursor pagination. Default page size matches the
# virtualizer's typical viewport (~30 visible rows × ~16 overscan)
# with comfortable headroom; cap at 5000 so a misbehaving client
# can't drag a 1M-row folder back in one shot.
_DEFAULT_PAGE_SIZE = 500
_MAX_PAGE_SIZE = 5000


def _encode_cursor(payload: dict[str, Any]) -> str:
    """JSON-encode + base64url so the cursor is safe in a URL query."""
    raw = json.dumps(payload, default=str).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> dict[str, Any]:
    """Inverse of _encode_cursor. Raises HTTPException(400) on garbage."""
    try:
        # Re-pad — base64.urlsafe_b64decode requires correct padding.
        pad = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + pad)
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.info("browse: bad cursor=%s err=%s", cursor[:32], exc)
        raise HTTPException(status_code=400, detail="invalid cursor")

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
    # v0.4.11 — cursor pagination + server-side substring filter.
    # Drops the previous "load entire folder" behaviour; large folders
    # now stream in pages and the typing-lag in the client filter input
    # disappears (server does the substring match against the indexed
    # name column).
    cursor: str | None = Query(default=None),
    limit: int = Query(default=_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    q: str | None = Query(default=None, description="case-insensitive substring filter on entry name"),
    # The first-page footer COUNT(*) can be expensive in 10M+-row
    # folders, especially with `q` engaged (no covering index). Make
    # it opt-in (review notable) — clients that need the badge pass
    # ?include_total=true; everyone else gets the page faster.
    include_total: bool = Query(default=False),
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

    # Server-side substring filter on entry name. ILIKE is the natural
    # form; index support is opportunistic — the existing
    # (source_id, parent_path) index narrows the candidate set first,
    # and at typical folder sizes the ILIKE pass over those candidates
    # is fast even without trgm.
    if q:
        base_filter.append(Entry.name.ilike(f"%{q}%"))

    # Cursor decode. The cursor encodes the last row's
    # (kind_is_dir, sort_value, id) tuple from the previous page.
    cursor_kind_is_file: int | None = None
    cursor_sort_val: Any = None
    cursor_id: uuid.UUID | None = None
    if cursor is not None:
        c = _decode_cursor(cursor)
        # Re-validate the cursor's sort/order match this request — a
        # cursor minted under sort=name|asc isn't valid against
        # sort=size|desc; bounce as 400 rather than silently returning
        # a misordered page.
        if c.get("sort") != sort or c.get("order") != order:
            raise HTTPException(
                status_code=400,
                detail="cursor was minted under a different sort/order; restart pagination",
            )
        cursor_kind_is_file = int(c["kf"])
        cursor_sort_val = c["s"]
        cursor_id = uuid.UUID(c["id"])

    # Stable ordering with seekable cursor. Three-tier comparison:
    # (kind_is_file, sort_col, Entry.id), matching the ORDER BY exactly.
    # Including `id` last gives a unique tiebreaker so duplicate sort
    # values don't skip rows. Cast bool to int so the cursor encodes a
    # number (not "true"/"false") and stays well-defined across drivers.
    #
    # NULL handling: size_bytes / fs_modified_at can be NULL on directories
    # and on entries the scanner hasn't fully populated yet. Postgres
    # default for ASC is NULLS LAST and for DESC is NULLS FIRST; pin to
    # NULLS LAST in both directions so NULLs always trail real values
    # (matches what the UI wants — "biggest" or "newest" listings should
    # never lead with rows whose sort key is unknown). The cursor
    # predicate then needs explicit IS NULL / IS NOT NULL branches
    # because `sort_col < val`, `sort_col > val`, and `sort_col == val`
    # all evaluate to NULL when either side is NULL, dropping those
    # rows from every page after the first NULL is encountered.
    kind_is_file = cast(Entry.kind != "directory", Integer)
    sort_clause = (
        sort_col.asc().nulls_last() if order == "asc" else sort_col.desc().nulls_last()
    )
    order_cols = [kind_is_file.asc(), sort_clause, Entry.id.asc()]
    stmt = select(Entry).where(and_(*base_filter)).order_by(*order_cols)

    if cursor_id is not None:
        # Three "tiers" we walk through in order: kind_is_file (dirs
        # first, then files), then sort_col (with NULLs LAST), then id.
        # The cursor lands at one specific (kind, sort, id) coordinate
        # and we want every row strictly after it.
        kind_advance = kind_is_file > cursor_kind_is_file
        if cursor_sort_val is None:
            # Cursor sat in the NULL tail. Same kind: only id-tiebreak
            # rows (no real-value rows can come after a NULL under
            # NULLS LAST).
            sort_or_id = and_(
                kind_is_file == cursor_kind_is_file,
                sort_col.is_(None),
                Entry.id > cursor_id,
            )
            stmt = stmt.where(or_(kind_advance, sort_or_id))
        else:
            same_sort_val = and_(
                kind_is_file == cursor_kind_is_file,
                sort_col == cursor_sort_val,
                Entry.id > cursor_id,
            )
            if order == "asc":
                next_sort = and_(
                    kind_is_file == cursor_kind_is_file,
                    sort_col > cursor_sort_val,
                )
                # NULLs come after every real value, so they're also
                # "after" the cursor's real-valued sort key.
                null_tail = and_(
                    kind_is_file == cursor_kind_is_file,
                    sort_col.is_(None),
                )
            else:
                next_sort = and_(
                    kind_is_file == cursor_kind_is_file,
                    sort_col < cursor_sort_val,
                )
                # Same logic for DESC + NULLS LAST.
                null_tail = and_(
                    kind_is_file == cursor_kind_is_file,
                    sort_col.is_(None),
                )
            stmt = stmt.where(or_(kind_advance, next_sort, same_sort_val, null_tail))

    # Over-fetch by 1 so we can detect "more pages exist."
    stmt = stmt.limit(limit + 1)
    fetched = (await db.execute(stmt)).scalars().all()
    has_more = len(fetched) > limit
    rows = fetched[:limit]

    # Build the next-page cursor from the LAST row of this page.
    next_cursor: str | None = None
    if has_more and rows:
        last = rows[-1]
        last_sort_val: Any
        if sort == "name":
            last_sort_val = last.name
        elif sort == "size":
            last_sort_val = last.size_bytes
        else:
            last_sort_val = last.fs_modified_at
        next_cursor = _encode_cursor({
            "kf": 0 if last.kind == "directory" else 1,
            "s": last_sort_val,
            "id": str(last.id),
            "sort": sort,
            "order": order,
        })

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

    # Total: only paid on the first page (cursor is None) AND when
    # the caller opted in via include_total=true — the COUNT can be a
    # full scan on 10M-row folders with no perf-friendly index when
    # `q` is set.
    total: int | None = None
    if cursor is None and include_total:
        total = (
            await db.execute(
                select(func.count(Entry.id)).where(and_(*base_filter))
            )
        ).scalar() or 0

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
        next_cursor=next_cursor,
        total=total,
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
