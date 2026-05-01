"""Storage Explorer endpoints — feed the WinDirStat-style treemap.

Three queries:

- GET /api/storage/sources — top-level treemap. One rectangle per
  source, sized by total bytes from the latest scan_snapshot.

- GET /api/storage/children — drill-down inside a source. Returns
  immediate children of `parent_path`. Kept for the cross-source view
  + as a cheap "preview one folder" probe.

- GET /api/storage/tree — recursive subtree expanded inside a node
  budget. The frontend renders this as a single squarified
  treemap of the entire tree (WinDirStat-style), every leaf as its
  own coloured rectangle. The CTE walks the visible subtree once,
  sorts by size, and Python folds the flat row list into a nested
  hierarchy.

Color modes (toggle in the UI):

- type   — extension or "directory"
- age    — hot/warm/cold based on fs_modified_at
- owner  — owner_name
- risk   — admin-only; flags `*` (public) / `auth` / restricted

Permission scoping:

- Source-level: check_source_access on every request.
- Per-entry (Phase 5): when BROWSE_ENFORCE_PERMS is on and the user
  isn't an admin (or admin without show_all=1), queries apply
  viewable_clause and roll hidden entries into a synthetic
  `<hidden>` bucket whose size is exposed only to admins.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, bindparam, func, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Text

from akashic.auth.dependencies import (
    check_source_access,
    get_current_user,
    get_permitted_source_ids,
)
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
_DEFAULT_TREE_NODES = 5000
_MAX_TREE_NODES = 20000


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


def _age_bucket(mtime: datetime | None, now: datetime) -> str:
    if mtime is None:
        return "unknown"
    age_days = (now - mtime).days
    if age_days < 30:
        return "hot"
    if age_days < 365:
        return "warm"
    return "cold"


def _color_key(entry: Any, mode: ColorMode, now: datetime, is_admin: bool) -> str:
    """One source of truth for the colour palette key. Used by both
    /children and /tree so the same row never gets a different colour
    depending on which endpoint surfaced it.

    `entry` is a row-like with at minimum: kind, extension, owner_name,
    fs_modified_at, viewable_by_read.
    """
    kind = entry.kind
    if mode == "type":
        return entry.extension or ("(none)" if kind == "file" else "directory")
    if mode == "age":
        return _age_bucket(entry.fs_modified_at, now)
    if mode == "owner":
        return entry.owner_name or "(unknown)"
    if mode == "risk":
        if not is_admin:
            # Risk colouring is admin-only — non-admins fall back to
            # type colouring so the page doesn't render every leaf grey.
            return entry.extension or ("(none)" if kind == "file" else "directory")
        tokens_read = entry.viewable_by_read or []
        if "*" in tokens_read:
            return "public"
        if "auth" in tokens_read:
            return "authenticated"
        return "restricted"
    return "unknown"


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
        children.append({
            "id": str(r.id),
            "kind": r.kind,
            "name": r.name,
            "path": r.path,
            "size_bytes": size,
            "file_count": (
                int(r.subtree_file_count) if r.kind == "directory" and r.subtree_file_count is not None
                else (1 if r.kind == "file" else 0)
            ),
            "color_key": _color_key(r, color_by, now, is_admin),
        })

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

    hidden_bucket = None
    if enforce and is_admin and perm_filter is not None:
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


# ── Recursive subtree: nested treemap data ────────────────────────────────


def _entry_size(row: Any) -> int:
    """Sizing rule consistent with the v1 /children endpoint: directories
    use subtree_size_bytes when populated, files use size_bytes."""
    if row.kind == "directory" and row.subtree_size_bytes is not None:
        return int(row.subtree_size_bytes)
    return int(row.size_bytes or 0)


def _build_nested_tree(
    rows: list[Any],
    root_path: str,
    color_by: ColorMode,
    now: datetime,
    is_admin: bool,
    parent_subtree_total: dict[str, int] | None = None,
) -> dict:
    """Take the flat row list returned by the recursive CTE and fold it
    into nested `children` arrays.

    Two structural rules:
      1. Drop rows whose parent_path is not itself in the result (orphans
         from budget pruning at an ancestor).
      2. For each surviving directory, if the sum of its surviving
         children's sizes is less than the directory's recorded
         subtree_size_bytes, account for the gap as a synthetic <other>
         leaf so size-totals stay honest.
    """
    by_path: dict[str, dict] = {}
    children_of: dict[str, list[dict]] = defaultdict(list)

    # First pass — build node objects and group by parent_path.
    for r in rows:
        node = {
            "id": str(r.id),
            "kind": r.kind,
            "name": r.name or "/",
            "path": r.path,
            "size_bytes": _entry_size(r),
            "color_key": _color_key(r, color_by, now, is_admin),
        }
        by_path[r.path] = node
        if r.path != root_path:
            children_of[r.parent_path].append(node)

    # Synthesise the root when no row materialises at root_path. Happens
    # when the connector emits top-level entries with `parent_path =
    # root_path` but no literal `path = root_path` row (SMB/SSH/S3 all
    # do this for their share root). The CTE's secondary anchor pulls
    # those first-level entries in; we need a parent node for them to
    # attach to.
    if root_path not in by_path:
        by_path[root_path] = {
            "kind": "directory", "name": "/", "path": root_path,
            "size_bytes": 0,  # filled from children sum below
        }

    # Second pass — attach children, drop orphans (their parent wasn't kept).
    for path, kids in children_of.items():
        parent = by_path.get(path)
        if parent is None:
            # Parent was pruned — these kids never appear in the tree.
            continue
        parent["children"] = sorted(
            kids, key=lambda n: n["size_bytes"], reverse=True,
        )

    # Backfill the synthesised root's size from its children — the CTE
    # never returned an aggregate for the synthetic node.
    synthetic_root = by_path.get(root_path)
    if synthetic_root is not None and synthetic_root.get("size_bytes") == 0:
        kids = synthetic_root.get("children", [])
        if kids:
            synthetic_root["size_bytes"] = sum(k["size_bytes"] for k in kids)

    # Third pass — for each directory whose children's sum < its own
    # subtree_size_bytes, synthesise an <other> child so the visible
    # rectangles stay proportional. Critical: without this the layout
    # under-paints big folders that got budget-truncated mid-way.
    for r in rows:
        if r.kind != "directory":
            continue
        node = by_path.get(r.path)
        if node is None:
            continue
        kids = node.get("children")
        if not kids:
            continue
        kids_sum = sum(k["size_bytes"] for k in kids)
        recorded = _entry_size(r)
        if recorded > kids_sum:
            node["children"].append({
                "kind": "other",
                "name": "<other>",
                "path": f"{r.path}/<other>",
                "size_bytes": recorded - kids_sum,
                "color_key": "other",
            })

    return by_path.get(root_path, {
        "kind": "directory", "name": "/", "path": root_path,
        "size_bytes": 0, "children": [],
    })


async def _fetch_tree_rows(
    db: AsyncSession,
    source_id: uuid.UUID,
    root_path: str,
    max_nodes: int,
    min_bytes: int,
    perm_tokens: list[str] | None,
) -> list[Any]:
    """Recursive CTE walking the subtree rooted at `root_path` for a
    single source. Walks via the indexed (source_id, parent_path) join,
    orders the materialised set by size DESC, takes top max_nodes.

    The perm trim is woven into the recursive step so hidden entries
    never appear in `walk` to begin with — Python doesn't have to
    re-check.
    """
    # The perm clause is templated as raw SQL because asyncpg + raw text
    # makes parameter binding for the array overlap clearest. Two
    # variants because the recursive step has a JOIN — qualifying the
    # column with `c.` avoids an AmbiguousColumnError.
    anchor_perm = ""
    rec_perm = ""
    perm_params: dict[str, Any] = {}
    if perm_tokens is not None:
        if not perm_tokens:
            # Fail-closed: empty token set matches nothing.
            anchor_perm = " AND false"
            rec_perm = " AND false"
        else:
            anchor_perm = " AND entries.viewable_by_read && :perm_tokens"
            rec_perm = " AND c.viewable_by_read && :perm_tokens"
            perm_params["perm_tokens"] = perm_tokens

    # Anchor strategy: prefer a literal `path = :root_path` row when one
    # exists. When it doesn't (the SMB / SSH / S3 connectors emit
    # top-level entries with `parent_path = '/'` and no synthesised
    # root), fall through to anchoring on `parent_path = :root_path` so
    # the de-facto first-level entries seed the recursion. The
    # synthesised root node is added in `_build_nested_tree`.
    sql = text(f"""
        WITH RECURSIVE walk AS (
            SELECT entries.id, entries.parent_path, entries.path,
                   entries.name, entries.kind,
                   entries.size_bytes, entries.subtree_size_bytes,
                   entries.extension, entries.owner_name,
                   entries.fs_modified_at, entries.viewable_by_read,
                   0 AS depth
            FROM entries
            WHERE entries.source_id = :source_id
              AND entries.is_deleted = false
              AND (
                  entries.path = :root_path
                  OR (
                      entries.parent_path = :root_path
                      AND NOT EXISTS (
                          SELECT 1 FROM entries r
                           WHERE r.source_id = :source_id
                             AND r.path = :root_path
                             AND r.is_deleted = false
                      )
                  )
              )
              {anchor_perm}
            UNION ALL
            SELECT c.id, c.parent_path, c.path, c.name, c.kind,
                   c.size_bytes, c.subtree_size_bytes,
                   c.extension, c.owner_name, c.fs_modified_at, c.viewable_by_read,
                   walk.depth + 1
            FROM walk
            JOIN entries c
              ON c.source_id = :source_id
             AND c.parent_path = walk.path
             AND c.is_deleted = false
             AND COALESCE(c.subtree_size_bytes, c.size_bytes, 0) >= :min_bytes
             {rec_perm}
            WHERE walk.kind = 'directory'
        )
        SELECT id, parent_path, path, name, kind,
               size_bytes, subtree_size_bytes,
               extension, owner_name, fs_modified_at, viewable_by_read,
               depth
        FROM walk
        ORDER BY (path = :root_path) DESC,
                 COALESCE(subtree_size_bytes, size_bytes, 0) DESC
        LIMIT :max_nodes
    """)
    if perm_tokens is not None and perm_tokens:
        sql = sql.bindparams(bindparam("perm_tokens", type_=ARRAY(Text)))
    params = {
        "source_id": source_id,
        "root_path": root_path,
        "max_nodes": max_nodes,
        "min_bytes": int(min_bytes),
    }
    params.update(perm_params)
    result = await db.execute(sql, params)
    return list(result.all())


@router.get("/tree")
async def get_tree(
    source_id: uuid.UUID = Query(...),
    path: str = Query(default="/"),
    max_nodes: int = Query(default=_DEFAULT_TREE_NODES, ge=1, le=_MAX_TREE_NODES),
    min_bytes: int = Query(default=0, ge=0),
    color_by: ColorMode = Query(default="type"),
    show_all: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Recursive subtree expansion bounded by node budget. The frontend
    feeds the returned `root` directly into d3-hierarchy and renders one
    nested squarified treemap of the entire visible estate.

    `truncated` is true when the budget was hit; per-parent <other>
    rectangles in the response account for the missing size."""
    await check_source_access(source_id, user, db)
    norm_path = _normalize_path(path)

    enforce = await _should_apply_perm_filter(user, show_all, db)
    perm_tokens: list[str] | None = None
    if enforce:
        perm_tokens = await user_principal_tokens(user, db)

    # Over-fetch by 1 so we can detect "we hit the budget exactly".
    fetch_limit = max_nodes + 1
    rows = await _fetch_tree_rows(
        db, source_id, norm_path, fetch_limit, min_bytes, perm_tokens,
    )

    truncated = len(rows) > max_nodes
    rows = rows[:max_nodes]

    if not rows:
        # Path doesn't exist or perm-filter dropped the root.
        return {
            "source_id": str(source_id),
            "path": norm_path,
            "color_by": color_by,
            "enforced": enforce,
            "node_count": 0,
            "truncated": False,
            "root": None,
        }

    now = datetime.now(timezone.utc)
    is_admin = user.role == "admin"
    root = _build_nested_tree(rows, norm_path, color_by, now, is_admin)

    return {
        "source_id": str(source_id),
        "path": norm_path,
        "color_by": color_by,
        "enforced": enforce,
        "node_count": len(rows),
        "truncated": truncated,
        "root": root,
    }
