"""Storage Explorer endpoints — feed the WinDirStat-style treemap.

Two queries:

- GET /api/storage/sources — top-level treemap. One rectangle per
  source, sized by total bytes, colored by the chosen dimension.
  Computed from the latest scan_snapshot per source so it's instant
  (no fresh aggregate query).

- GET /api/storage/children — drill-down inside a source. Returns
  immediate children of `parent_path`, each with its precomputed
  subtree_size_bytes (file rows just have size_bytes). One indexed
  range scan per drill-down — no recursive walk at request time.

Color modes (toggle in the UI):

- type   — top extension under each rectangle
- age    — hot/warm/cold based on max fs_modified_at
- owner  — top owner_name under each rectangle
- risk   — admin-only; flags rectangles with `*` in their reachable
           viewable_by_read or unresolved SIDs in the subtree

Permission scoping:

- Source-level: check_source_access on every request.
- Per-entry (Phase 5): when BROWSE_ENFORCE_PERMS is on and the user
  isn't an admin (or admin without show_all=1), the children query
  applies viewable_clause and rolls hidden entries into a synthetic
  `<hidden>` bucket whose size is exposed only to admins.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import check_source_access, get_current_user, get_permitted_source_ids
from akashic.config import settings
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.scan_snapshot import ScanSnapshot
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services.access_query import (
    user_has_any_bindings,
    user_principal_tokens,
    viewable_clause,
)

router = APIRouter(prefix="/api/storage", tags=["storage"])


ColorMode = Literal["type", "age", "owner", "risk"]
_DEFAULT_LIMIT = 200


def _normalize_path(path: str) -> str:
    if not path or path == "/":
        return "/"
    return path.rstrip("/") or "/"


async def _should_apply_perm_filter(
    user: User, show_all: bool, db: AsyncSession,
) -> bool:
    if not settings.browse_enforce_perms:
        return False
    if user.role == "admin" and show_all:
        return False
    return await user_has_any_bindings(user, db)


# ── Top-level: one rectangle per source ────────────────────────────────────


@router.get("/sources")
async def list_sources(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Cross-source treemap. One row per source the caller can see,
    sized by total bytes from the latest scan_snapshot."""
    allowed = await get_permitted_source_ids(user, db)
    src_stmt = select(Source.id, Source.name, Source.type)
    if allowed is not None:
        if not allowed:
            return {"sources": []}
        src_stmt = src_stmt.where(Source.id.in_(allowed))
    sources = (await db.execute(src_stmt)).all()
    if not sources:
        return {"sources": []}

    # Latest snapshot per source (DISTINCT ON + the existing
    # (source_id, taken_at DESC) index — cheap).
    snap_stmt = (
        select(ScanSnapshot)
        .where(ScanSnapshot.source_id.in_([s.id for s in sources]))
        .order_by(ScanSnapshot.source_id, ScanSnapshot.taken_at.desc())
        .distinct(ScanSnapshot.source_id)
    )
    latest = {
        snap.source_id: snap
        for snap in (await db.execute(snap_stmt)).scalars().all()
    }

    out = []
    for s in sources:
        snap = latest.get(s.id)
        out.append({
            "source_id": str(s.id),
            "source_name": s.name,
            "source_type": s.type,
            "size_bytes": int(snap.total_size_bytes or 0) if snap else 0,
            "file_count": int(snap.file_count or 0) if snap else 0,
            "directory_count": int(snap.directory_count or 0) if snap else 0,
            "taken_at": snap.taken_at.isoformat() if snap else None,
        })
    out.sort(key=lambda r: r["size_bytes"], reverse=True)
    return {"sources": out}


# ── Drill-down: immediate children of parent_path ──────────────────────────


def _age_bucket(mtime: datetime | None, now: datetime) -> str:
    if mtime is None:
        return "unknown"
    age_days = (now - mtime).days
    if age_days < 30:
        return "hot"
    if age_days < 365:
        return "warm"
    return "cold"


@router.get("/children")
async def list_children(
    source_id: uuid.UUID,
    path: str = Query(default="/"),
    color_by: ColorMode = Query(default="type"),
    show_all: bool = Query(default=False),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Returns the top `limit` children of `path` in `source_id`,
    each with the size + color-dimension aggregate. A synthetic
    `<other>` rectangle accounts for the long tail when more than
    `limit` children exist."""
    await check_source_access(source_id, user, db)
    norm_path = _normalize_path(path)

    base_filter = [
        Entry.source_id == source_id,
        Entry.parent_path == norm_path,
        Entry.is_deleted == False,  # noqa: E712
    ]

    enforce = await _should_apply_perm_filter(user, show_all, db)
    perm_filter = None
    if enforce:
        tokens = await user_principal_tokens(user, db)
        perm_filter = viewable_clause(tokens, "read")
        base_filter.append(perm_filter)

    # Files use size_bytes; directories use subtree_size_bytes (NULL on
    # rows from a deployment that hasn't run the rollup yet — those
    # render as zero-sized rectangles, which is the correct visual
    # signal).
    rows = (await db.execute(
        select(
            Entry.id,
            Entry.kind,
            Entry.name,
            Entry.path,
            Entry.extension,
            Entry.owner_name,
            Entry.size_bytes,
            Entry.subtree_size_bytes,
            Entry.subtree_file_count,
            Entry.fs_modified_at,
            Entry.viewable_by_read,
        )
        .where(and_(*base_filter))
        .order_by(
            func.coalesce(Entry.subtree_size_bytes, Entry.size_bytes, 0).desc().nulls_last(),
        )
        .limit(limit + 1)
    )).all()

    has_overflow = len(rows) > limit
    rows = rows[:limit]
    now = datetime.now(timezone.utc)
    is_admin = user.role == "admin"

    children: list[dict] = []
    for r in rows:
        size = int(
            r.subtree_size_bytes
            if r.kind == "directory" and r.subtree_size_bytes is not None
            else (r.size_bytes or 0)
        )
        item = {
            "id": str(r.id),
            "kind": r.kind,
            "name": r.name,
            "path": r.path,
            "size_bytes": size,
            "file_count": (
                int(r.subtree_file_count) if r.kind == "directory" and r.subtree_file_count is not None
                else (1 if r.kind == "file" else 0)
            ),
        }
        if color_by == "type":
            item["color_key"] = r.extension or ("(none)" if r.kind == "file" else "directory")
        elif color_by == "age":
            item["color_key"] = _age_bucket(r.fs_modified_at, now)
        elif color_by == "owner":
            item["color_key"] = r.owner_name or "(unknown)"
        elif color_by == "risk":
            if not is_admin:
                # Risk coloring is admin-only — non-admins fall back
                # to type coloring without the page nagging them.
                item["color_key"] = r.extension or "directory"
            else:
                tokens_read = r.viewable_by_read or []
                if "*" in tokens_read:
                    item["color_key"] = "public"
                elif "auth" in tokens_read:
                    item["color_key"] = "authenticated"
                else:
                    item["color_key"] = "restricted"
        children.append(item)

    # Long-tail rollup. Sum everything past `limit` into one synthetic
    # "<other>" rectangle so the treemap stays bounded in cardinality
    # without losing the size accounting.
    other = None
    if has_overflow:
        tail_stmt = (
            select(
                func.count(Entry.id).label("n"),
                func.sum(
                    func.coalesce(Entry.subtree_size_bytes, Entry.size_bytes, 0)
                ).label("bytes"),
            )
            .where(and_(*base_filter))
            .where(
                Entry.id.notin_([r.id for r in rows])
                if rows else True
            )
        )
        tail = (await db.execute(tail_stmt)).first()
        other = {
            "kind": "other",
            "name": "<other>",
            "size_bytes": int(tail.bytes or 0) if tail else 0,
            "child_count": int(tail.n or 0) if tail else 0,
        }

    # Hidden bucket (admin-only when perm-filter is on) — gives admins
    # visibility into "this folder has stuff you can't see at all".
    hidden_bucket = None
    if enforce and is_admin and perm_filter is not None:
        # A version of base_filter with everything except the perm filter,
        # so we can compute "rows the perm filter dropped".
        unfiltered = [c for c in base_filter if c is not perm_filter]
        unfiltered_total = (await db.execute(
            select(
                func.count(Entry.id).label("n"),
                func.sum(
                    func.coalesce(Entry.subtree_size_bytes, Entry.size_bytes, 0)
                ).label("bytes"),
            ).where(and_(*unfiltered))
        )).first()
        visible_total = (await db.execute(
            select(
                func.count(Entry.id).label("n"),
                func.sum(
                    func.coalesce(Entry.subtree_size_bytes, Entry.size_bytes, 0)
                ).label("bytes"),
            ).where(and_(*base_filter))
        )).first()
        hidden_n = int(unfiltered_total.n or 0) - int(visible_total.n or 0)
        hidden_bytes = int(unfiltered_total.bytes or 0) - int(visible_total.bytes or 0)
        if hidden_n > 0:
            hidden_bucket = {
                "kind": "hidden",
                "name": "<hidden>",
                "size_bytes": hidden_bytes,
                "child_count": hidden_n,
            }

    return {
        "source_id": str(source_id),
        "path": norm_path,
        "color_by": color_by,
        "children": children,
        "other": other,
        "hidden": hidden_bucket,
        "enforced": enforce,
    }
