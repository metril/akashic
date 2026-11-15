"""Tag catalogue + applied-tag management.

Two distinct surfaces in one router:

  - **Catalogue** (`/api/v1/tags`) — the set of named tags. Listing is
    open to any authenticated user (tags are useful curation signals
    for everyone); creation auto-happens on apply, but the explicit
    POST is kept for admins who want to set a colour at create time.
    Deletion is admin-only.

  - **Apply / remove / bulk-apply** (`/api/v1/entries/{id}/tags`,
    `/api/v1/tags/bulk-apply`) — admin-only mutations. Tagging a
    directory materialises inheritance onto every descendant via
    services/tag_inheritance.py.

The Phase-C plan called for a separate `tags_apply.py` router; collapsing
both surfaces into this single file avoids two routers writing to the
same tables and keeps related auth rules visible together.
"""
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import Principal, require_scope
from akashic.auth.source_scope import ensure_source_allowed, restrict_sources
from akashic.config import settings
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.tag import Tag, EntryTag
from akashic.models.user import User
from akashic.schemas.tag import TagCreate, TagResponse
from akashic.services.tag_inheritance import (
    apply_tag,
    get_tags_for_entry,
    remove_tag,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tags"])


# ── Background tasks ──────────────────────────────────────────────────────


async def _reindex_entries(entry_ids: list[str], db_url: str) -> None:
    """Re-emit Meili docs for entries whose tag set changed.

    Keyed by id-as-string so the BackgroundTasks args are JSON-friendly
    (mirrors the pattern in routers/ingest.py).
    """
    if not entry_ids:
        return
    # Reuse the ingest router's process-cached engine pool
    # (review D-I2). Pre-fix this created + disposed an engine per
    # bulk-tag operation, churning DB connections.
    from akashic.routers.ingest import _bg_session
    from akashic.services.search import build_entry_doc, update_files_partial

    session = _bg_session(db_url)
    try:
        async with session() as db:
            res = await db.execute(
                select(Entry).where(
                    Entry.id.in_([uuid.UUID(i) for i in entry_ids]),
                    Entry.kind == "file",
                    Entry.is_deleted == False,  # noqa: E712
                )
            )
            docs = []
            for entry in res.scalars().all():
                doc = await _build_doc_with_tags(entry, db)
                docs.append(doc)
            if docs:
                # v0.30.0 — partial update so a tag re-index doesn't
                # wipe the doc's extracted content_text (which
                # build_entry_doc doesn't emit). See meili_indexer.
                await update_files_partial(docs)
    except Exception as exc:
        logger.warning("Tag re-index failed: %s", exc)


async def _build_doc_with_tags(entry: Entry, db: AsyncSession) -> dict:
    """build_entry_doc + a fresh tag-fetch for this entry. Splits out so
    we don't widen build_entry_doc's signature (the function is called
    from many places that don't have an AsyncSession handy)."""
    from akashic.services.search import build_entry_doc
    from akashic.services.tag_inheritance import get_tags_for_entries

    doc = build_entry_doc(entry)
    tag_map = await get_tags_for_entries(db, entry_ids=[entry.id])
    doc["tags"] = tag_map.get(entry.id, [])
    return doc


# ── Catalogue ─────────────────────────────────────────────────────────────


@router.post("/tags", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
async def create_tag(
    data: TagCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("tags:write", source_scoped=True),
):
    """Pre-create a catalogue entry (lets admins set a colour up front).
    Apply endpoints also auto-create — this is the explicit path."""
    principal.ensure_unrestricted()  # the tag catalogue is global
    user = principal.user
    existing = await db.execute(select(Tag).where(Tag.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Tag already exists")
    tag = Tag(name=data.name, color=data.color, created_by=user.id)
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag


@router.get("/tags", response_model=list[TagResponse])
async def list_tags(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("tags:read", source_scoped=True),
):
    stmt = select(Tag).order_by(Tag.name)
    if principal.source_limited:
        # Only tags applied inside the token's sources, so other sources' tag names don't leak.
        used = restrict_sources(
            select(EntryTag.tag).join(Entry, Entry.id == EntryTag.entry_id), principal,
        )
        stmt = stmt.where(Tag.name.in_(used))
    result = await db.execute(stmt)
    return result.scalars().all()


class TagUsageResponse(BaseModel):
    name: str
    color: str | None
    direct_count: int
    inherited_count: int


@router.get("/tags/usage", response_model=list[TagUsageResponse])
async def list_tag_usage(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("tags:read", source_scoped=True),
):
    """Per-tag usage split into direct vs inherited applications.
    Backs the Settings → Tags page.
    """
    from sqlalchemy import text

    result = await db.execute(
        text(
            """
            SELECT
              t.name,
              t.color,
              COALESCE(SUM(CASE WHEN et.inherited_from_entry_id IS NULL THEN 1 ELSE 0 END), 0) AS direct_count,
              COALESCE(SUM(CASE WHEN et.inherited_from_entry_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS inherited_count
            FROM tags t
            LEFT JOIN entry_tags et ON et.tag = t.name
              AND (CAST(:ids AS uuid[]) IS NULL OR et.entry_id IN (
                    SELECT e.id FROM entries e WHERE e.source_id = ANY(CAST(:ids AS uuid[]))))
            GROUP BY t.id, t.name, t.color
            ORDER BY t.name
            """
        ),
        {"ids": sorted(principal.allowed_source_ids) if principal.source_limited else None},
    )
    return [
        TagUsageResponse(
            name=row.name,
            color=row.color,
            direct_count=int(row.direct_count or 0),
            inherited_count=int(row.inherited_count or 0),
        )
        for row in result.fetchall()
    ]


@router.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag_catalogue(
    tag_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("tags:write", source_scoped=True),
):
    """Delete a catalogue entry and every applied/inherited row that
    referenced it by name. Cascade is global — admin-only."""
    principal.ensure_unrestricted()  # the tag catalogue is global
    tag_result = await db.execute(select(Tag).where(Tag.id == tag_id))
    tag = tag_result.scalar_one_or_none()
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    # Wipe entry_tags rows by name (the join column is denormalised).
    await db.execute(delete(EntryTag).where(EntryTag.tag == tag.name))
    await db.delete(tag)
    await db.commit()


# ── Apply / remove ────────────────────────────────────────────────────────


class _TagApplyBody(BaseModel):
    tags: list[str] = Field(min_length=1)


async def _ensure_catalogue_entries(
    db: AsyncSession, *, names: list[str], user_id: uuid.UUID, principal: Principal,
) -> None:
    """Auto-create catalogue rows for tag names referenced by an apply
    call. Cheap on the common case (the row exists) — Postgres skips
    via ON CONFLICT."""
    from sqlalchemy import text

    if principal.source_limited:
        # A limited token may not grow the global catalogue (see create_tag).
        known = set((await db.execute(select(Tag.name).where(Tag.name.in_(names)))).scalars().all())
        if set(names) - known:
            principal.ensure_unrestricted()
    for name in names:
        await db.execute(
            text(
                """
                INSERT INTO tags (id, name, color, created_by)
                VALUES (gen_random_uuid(), :name, NULL, :user_id)
                ON CONFLICT (name) DO NOTHING
                """
            ),
            {"name": name, "user_id": user_id},
        )


@router.post(
    "/entries/{entry_id}/tags",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def apply_tags_to_entry(
    entry_id: uuid.UUID,
    body: _TagApplyBody,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("tags:write", source_scoped=True),
):
    """Apply one or more tags (by name) to an entry.

    For directory entries, every descendant gets an inherited row. The
    Meili re-index for affected file rows runs in the background;
    inherited tag application on a million-descendant directory is a
    real but bounded cost the admin opts into.
    """
    user = principal.user
    entry_res = await db.execute(select(Entry).where(Entry.id == entry_id))
    entry = entry_res.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    ensure_source_allowed(principal, entry.source_id, "Entry not found")

    await _ensure_catalogue_entries(db, names=body.tags, user_id=user.id, principal=principal)

    affected: set[uuid.UUID] = set()
    for tag in body.tags:
        ids = await apply_tag(db, entry_id=entry_id, tag=tag, user_id=user.id)
        affected.update(ids)
    await db.commit()

    background_tasks.add_task(
        _reindex_entries,
        [str(i) for i in affected],
        settings.database_url,
    )


@router.delete(
    "/entries/{entry_id}/tags/{tag}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_tag_from_entry(
    entry_id: uuid.UUID,
    tag: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("tags:write", source_scoped=True),
):
    """Remove a directly-applied tag from an entry. Inherited copies
    sourced from this entry cascade-delete; descendants that were also
    directly tagged keep their direct rows."""
    entry = (await db.execute(select(Entry).where(Entry.id == entry_id))).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    ensure_source_allowed(principal, entry.source_id, "Entry not found")

    affected = await remove_tag(db, entry_id=entry_id, tag=tag)
    await db.commit()

    background_tasks.add_task(
        _reindex_entries,
        [str(i) for i in affected],
        settings.database_url,
    )


@router.get("/entries/{entry_id}/tags", response_model=list[dict])
async def get_entry_tags(
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("tags:read", source_scoped=True),
):
    """Tags on this entry, grouped by origin (direct vs inherited).
    Open to any authenticated user — read-only."""
    user = principal.user
    entry = (await db.execute(select(Entry).where(Entry.id == entry_id))).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    ensure_source_allowed(principal, entry.source_id, "Entry not found")
    return await get_tags_for_entry(db, entry_id=entry_id)


# ── Bulk apply ────────────────────────────────────────────────────────────


class _BulkApplyBody(BaseModel):
    entry_ids: list[uuid.UUID] = Field(min_length=1)
    tags: list[str] = Field(min_length=1)


@router.post("/tags/bulk-apply", status_code=status.HTTP_204_NO_CONTENT)
async def bulk_apply(
    body: _BulkApplyBody,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("tags:write", source_scoped=True),
):
    """Apply each of `tags` to every entry in `entry_ids`. Used by the
    Search UI's "Tag selected (N)" action.

    Directory entries in the set inherit-down as usual; the dialog
    warns the admin before submission when any selected entry is a
    directory."""
    user = principal.user
    res = await db.execute(
        restrict_sources(select(Entry).where(Entry.id.in_(body.entry_ids)), principal)
    )
    entries = list(res.scalars().all())
    if len(entries) != len(set(body.entry_ids)):
        raise HTTPException(status_code=404, detail="Some entries not found")

    await _ensure_catalogue_entries(db, names=body.tags, user_id=user.id, principal=principal)

    affected: set[uuid.UUID] = set()
    for entry in entries:
        for tag in body.tags:
            ids = await apply_tag(db, entry_id=entry.id, tag=tag, user_id=user.id)
            affected.update(ids)
    await db.commit()

    background_tasks.add_task(
        _reindex_entries,
        [str(i) for i in affected],
        settings.database_url,
    )
