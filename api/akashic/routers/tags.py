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
from typing import Annotated
import logging
import uuid

from akashic.routing import ApiRouter
from fastapi import BackgroundTasks, Depends, HTTPException, Path, status
from akashic.schemas.base import ApiModel
from pydantic import Field
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

router = ApiRouter(tags=["tags"])


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


@router.post(
    "/tags",
    response_model=TagResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tag",
    description=(
        "Adds a tag to the catalogue with an optional colour. Tags are also created automatically the "
        "first time they are applied; this is the explicit path for setting a colour up front.\n"
        "Requires scope `tags:write`. Not available to source-limited tokens, since the catalogue is "
        "global. Errors: 409 if the tag exists; 422 for a malformed body."
    ),
)
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


@router.get(
    "/tags",
    response_model=list[TagResponse],
    summary="List tags",
    description=(
        "The tag catalogue ordered by name. A source-limited token sees only tags used on its "
        "permitted sources. Not paginated.\n"
        "Requires scope `tags:read`."
    ),
)
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


class EntryTagOut(ApiModel):
    tag: str = Field(description="Tag name.")
    inherited: bool = Field(description="True when the tag comes from a tagged ancestor directory rather than being applied to this entry.")
    inherited_from_path: str | None = Field(description="Path of the ancestor directory the tag is inherited from; null for a direct tag.")

    model_config = {"json_schema_extra": {'examples': [{'tag': 'invoice', 'inherited': True, 'inherited_from_path': '/projects'}]}}


class TagUsageResponse(ApiModel):
    name: str = Field(description="Tag name.")
    color: str | None = Field(description="Display colour, if set.")
    direct_count: int = Field(description="Entries tagged directly.")
    inherited_count: int = Field(description="Entries that inherit the tag from a tagged directory.")


@router.get(
    "/tags/usage",
    response_model=list[TagUsageResponse],
    summary="Tag usage",
    description=(
        "For each tag, how many entries carry it directly and how many only inherit it from a tagged "
        "directory. A source-limited token counts only its permitted sources. Not paginated.\n"
        "Requires scope `tags:read`."
    ),
)
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


@router.delete(
    "/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a tag",
    description=(
        "Deletes the tag from the catalogue and removes every direct and inherited application of it, "
        "across all sources. Irreversible.\n"
        "Requires scope `tags:write`. Not available to source-limited tokens, since the effect is "
        "global. Errors: 404 if the tag does not exist."
    ),
)
async def delete_tag_catalogue(
    tag_id: Annotated[uuid.UUID, Path(description="Tag to delete.")],
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


class _TagApplyBody(ApiModel):
    tags: list[str] = Field(min_length=1, description="Tag names to apply (at least one).")

    model_config = {"json_schema_extra": {'examples': [{'tags': ['invoice', '2026']}]}}


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
    summary="Tag an entry",
    description=(
        "Applies one or more tags, by name, directly to an entry; missing tags are added to the "
        "catalogue. Tagging a directory makes every descendant inherit the tag. The search index is "
        "updated in the background.\n"
        "Requires scope `tags:write`. A source-limited token may apply only tags that already exist, "
        "and only on entries in its sources. Errors: 404 if the entry does not exist or is outside "
        "the token's sources; 422 for an empty tag list."
    ),
)
async def apply_tags_to_entry(
    entry_id: Annotated[uuid.UUID, Path(description="Entry to tag.")],
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
    summary="Remove a tag from an entry",
    description=(
        "Removes a directly applied tag. Copies inherited from this entry are removed from its "
        "descendants, but descendants that were tagged directly keep theirs. The search index is "
        "updated in the background.\n"
        "Requires scope `tags:write`. Errors: 404 if the entry does not exist or is outside the "
        "token's sources."
    ),
)
async def remove_tag_from_entry(
    entry_id: Annotated[uuid.UUID, Path(description="Entry to remove the tag from.")],
    tag: Annotated[str, Path(description="Tag name.", examples=['invoice'])],
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


@router.get(
    "/entries/{entry_id}/tags",
    response_model=list[EntryTagOut],
    summary="List an entry's tags",
    description=(
        "The tags on an entry as a list of `{tag, inherited, inherited_from_path}`: direct tags "
        "first, then inherited ones, each with the path of the directory it came from. A tag applied "
        "both directly and by inheritance appears twice.\n"
        "Requires scope `tags:read`. Source-limited API tokens only see entries of their permitted "
        "sources. Errors: 404 if the entry does not exist or is outside the token's sources."
    ),
)
async def get_entry_tags(
    entry_id: Annotated[uuid.UUID, Path(description="Entry id.")],
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


class _BulkApplyBody(ApiModel):
    entry_ids: list[uuid.UUID] = Field(min_length=1, description="Entries to tag (at least one).")
    tags: list[str] = Field(min_length=1, description="Tag names to apply to each entry (at least one).")

    model_config = {"json_schema_extra": {'examples': [{'entry_ids': ['3b1f0c9e-5d0a-4e7c-8a41-9a6b2f5c7d10'], 'tags': ['invoice']}]}}


@router.post(
    "/tags/bulk-apply",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Tag many entries",
    description=(
        "Applies every listed tag to every listed entry, as for single-entry tagging (directories "
        "propagate to descendants). All-or-nothing: if any entry is unknown or outside the caller's "
        "sources, nothing is applied. The search index is updated in the background.\n"
        "Requires scope `tags:write`. A source-limited token may apply only existing tags. Errors: "
        "404 if some entries do not exist or are outside the token's sources; 422 for empty lists."
    ),
)
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
