from typing import Annotated
import logging
import uuid

from akashic.routing import ApiRouter
from fastapi import Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import Principal, require_scope
from akashic.auth.source_scope import restrict_sources
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.common import Page, offset_from_cursor, offset_page
from akashic.routers.browse import _should_enforce_perms
from akashic.services.access_query import user_principal_tokens, viewable_clause
from akashic.services.audit import record_event
from akashic.services.duplicate_delete import delete_copy
from akashic.services.search import delete_file_from_index

logger = logging.getLogger(__name__)

router = ApiRouter(prefix="/duplicates", tags=["duplicates"])


class DuplicateGroup(BaseModel):
    """A set of files sharing one content hash."""

    content_hash: str = Field(description="Shared content hash.")
    count: int = Field(description="Number of copies.")
    total_size: int = Field(description="Combined size of all copies, in bytes.")
    file_size: int = Field(description="Size of a single copy, in bytes.")
    wasted_bytes: int = Field(description="Bytes reclaimable by keeping one copy.")


class DuplicateFile(BaseModel):
    """One copy within a duplicate group."""

    id: uuid.UUID = Field(description="Entry id.")
    source_id: uuid.UUID = Field(description="Owning source id.")
    path: str = Field(description="Full path.")
    filename: str = Field(description="Base name.")
    extension: str | None = Field(description="Extension without the dot.")
    size_bytes: int | None = Field(description="Size in bytes.")
    content_hash: str | None = Field(description="Content hash.")
    mime_type: str | None = Field(description="Detected MIME type.")
    fs_modified_at: str | None = Field(description="Filesystem modified time, ISO 8601.")
    first_seen_at: str = Field(description="First time a scan saw the entry, ISO 8601.")
    last_seen_at: str = Field(description="Most recent scan sighting, ISO 8601.")
    is_deleted: bool = Field(description="Whether the entry is soft-deleted.")


@router.get(
    "",
    response_model=Page[DuplicateGroup],
    summary="List duplicate groups",
    description=(
        "Groups of non-deleted files that share a content hash (two or more copies), largest "
        "combined size first. `wasted_bytes` is what keeping a single copy would reclaim. Files "
        "hidden by per-user ACL filtering are not counted.\n"
        "Requires scope `files:read`. Source-limited API tokens only see their permitted sources. "
        "Errors: 422 for a malformed `cursor`."
    ),
)
async def list_duplicates(
    min_size: Annotated[int | None, Query(description="Only groups whose single-copy size is at least this many bytes.", examples=[1048576])] = None,
    cursor: str | None = Query(default=None, description="Opaque cursor from a previous `next_cursor`."),
    limit: int = Query(default=50, le=200, description="Page size."),
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("files:read", source_scoped=True),
):
    user = principal.user
    offset = offset_from_cursor(cursor)
    base_filter = [
        Entry.kind == "file",
        Entry.is_deleted == False,  # noqa: E712
        Entry.content_hash.isnot(None),
    ]
    if await _should_enforce_perms(principal, False, db):
        base_filter.append(viewable_clause(await user_principal_tokens(user, db), "read"))

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
    stmt = restrict_sources(stmt, principal)
    stmt = stmt.order_by(func.sum(Entry.size_bytes).desc()).offset(offset).limit(limit + 1)

    result = await db.execute(stmt)
    rows = result.all()

    groups = [
        {
            "content_hash": row.content_hash,
            "count": row.count,
            "total_size": row.total_size,
            "file_size": row.file_size,
            "wasted_bytes": (row.count - 1) * row.file_size,
        }
        for row in rows
    ]
    return offset_page(groups, offset, limit)


@router.get(
    "/{content_hash}/files",
    response_model=Page[DuplicateFile],
    summary="Files sharing a content hash",
    description=(
        "The individual copies in one duplicate group, ordered by entry id. Files hidden by per- "
        "user ACL filtering are omitted.\n"
        "Requires scope `files:read`. Source-limited API tokens only see their permitted sources. "
        "Errors: 422 for a malformed `cursor`."
    ),
)
async def get_duplicate_files(
    content_hash: Annotated[str, Path(description="Content hash shared by the copies, as returned by the group list.")],
    cursor: str | None = Query(default=None, description="Opaque cursor from a previous `next_cursor`."),
    limit: int = Query(default=500, ge=1, le=5000, description="Page size."),
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("files:read", source_scoped=True),
):
    user = principal.user
    offset = offset_from_cursor(cursor)
    stmt = select(Entry).where(
        Entry.kind == "file",
        Entry.content_hash == content_hash,
        Entry.is_deleted == False,  # noqa: E712
    ).order_by(Entry.id).offset(offset).limit(limit + 1)
    stmt = restrict_sources(stmt, principal)
    if await _should_enforce_perms(principal, False, db):
        stmt = stmt.where(viewable_clause(await user_principal_tokens(user, db), "read"))

    result = await db.execute(stmt)
    entries = result.scalars().all()
    files = [
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
    return offset_page(files, offset, limit)


# ── Bulk delete ────────────────────────────────────────────────────────────


class DeleteCopiesRequest(BaseModel):
    keep_entry_id: uuid.UUID = Field(description="Entry to keep. It must belong to the duplicate group.")
    delete_entry_ids: list[uuid.UUID] = Field(description="Entries to delete. Must be non-empty, must not include `keep_entry_id`, and every entry must be a file with the group's content hash.")

    model_config = {"json_schema_extra": {'examples': [{'keep_entry_id': '6f1c1a52-3c77-4a4e-9d0e-0f3f4a8f9c11', 'delete_entry_ids': ['a1b2c3d4-0000-4000-8000-000000000001', 'a1b2c3d4-0000-4000-8000-000000000002']}]}}


class DeleteCopyOutcome(BaseModel):
    entry_id: str = Field(description="Entry the outcome is for.")
    path: str = Field(description="Path of the entry.")
    ok: bool = Field(description="Whether the copy was deleted.")
    step: str = Field(default="", description="Step that failed, for a failed copy; empty on success.")
    message: str = Field(default="", description="Failure message, for a failed copy; empty on success.")


class DeleteCopiesResponse(BaseModel):
    deleted: list[DeleteCopyOutcome] = Field(description="Copies deleted from storage and the index.")
    failed: list[DeleteCopyOutcome] = Field(description="Copies that could not be deleted; they remain indexed.")


@router.post(
    "/{content_hash}/delete-copies",
    response_model=DeleteCopiesResponse,
    summary="Delete duplicate copies",
    description=(
        "Deletes the listed copies of a duplicate group from the storage system itself and from "
        "the index, keeping `keep_entry_id`. Copies are processed one at a time; the response "
        "lists which were deleted and which failed (for example a read-only share), so partial "
        "success is normal and still returns 200. A failed copy stays indexed. Each outcome is "
        "recorded as a `duplicate_copy_deleted` or `duplicate_copy_delete_failed` audit event.\n"
        "Requires scope `sources:write`. Source-limited API tokens only see their permitted "
        "sources.\n"
        "Errors: 400 if `delete_entry_ids` is empty, contains `keep_entry_id`, or any entry is "
        "not a file with this content hash; 404 if any entry does not exist or is outside the "
        "token's sources."
    ),
)
async def delete_duplicate_copies(
    content_hash: Annotated[str, Path(description="Content hash of the group the entries must belong to.")],
    body: DeleteCopiesRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("sources:write", source_scoped=True),
):
    """Bulk-delete copies of a duplicate group from disk + index.

    Validation:
      - keep_entry_id and every delete_entry_id must belong to the same
        content_hash group.
      - delete_entry_ids must be non-empty.
      - keep_entry_id must NOT also appear in delete_entry_ids.

    Per-entry execution: spawn `akashic-scanner delete`. On success we
    drop the Entry row from postgres + the doc from Meilisearch and
    write an audit_event. On failure we leave the row in place and
    surface the connector error to the caller. Partial-success is the
    common case (one path is read-only; the others delete fine).
    """
    admin = principal.user
    if not body.delete_entry_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="delete_entry_ids must not be empty",
        )
    if body.keep_entry_id in body.delete_entry_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="keep_entry_id cannot also be in delete_entry_ids",
        )

    # Pull every entry in this group + the keep entry, in one query.
    all_ids = list(body.delete_entry_ids) + [body.keep_entry_id]
    stmt = select(Entry).where(
        Entry.id.in_(all_ids),
        Entry.is_deleted == False,  # noqa: E712
    )
    stmt = restrict_sources(stmt, principal)  # out-of-limit entries read as not found
    result = await db.execute(stmt)
    by_id: dict[uuid.UUID, Entry] = {e.id: e for e in result.scalars().all()}

    # Existence + group-membership checks. Every supplied id must (a)
    # exist, (b) be a file, and (c) carry the same content_hash. This
    # rejects requests that try to use this endpoint to delete random
    # entries by smuggling them in with a real keep id.
    missing = [str(i) for i in all_ids if i not in by_id]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"entries not found: {', '.join(missing)}",
        )
    wrong_hash = [
        str(i) for i, e in by_id.items()
        if e.content_hash != content_hash or e.kind != "file"
    ]
    if wrong_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"entries do not belong to group {content_hash}: {', '.join(wrong_hash)}",
        )

    # Resolve sources upfront so we don't query per-entry. There may be
    # only a handful of distinct source_ids even with 50 entries.
    source_ids = {e.source_id for e in by_id.values()}
    src_result = await db.execute(select(Source).where(Source.id.in_(source_ids)))
    sources_by_id: dict[uuid.UUID, Source] = {
        s.id: s for s in src_result.scalars().all()
    }

    deleted: list[DeleteCopyOutcome] = []
    failed: list[DeleteCopyOutcome] = []

    # Sequential rather than concurrent. Most groups have <10 copies and
    # a single SMB connector setup takes ~50ms — the marginal value of
    # parallelism doesn't justify the failure-mode complexity (a
    # half-failed batch with concurrent connection establishment can
    # leak fds on any error). If a customer regularly deletes hundreds
    # of copies, revisit.
    for eid in body.delete_entry_ids:
        entry = by_id[eid]
        source = sources_by_id.get(entry.source_id)
        if source is None:
            failed.append(DeleteCopyOutcome(
                entry_id=str(eid),
                path=entry.path,
                ok=False,
                step="config",
                message="source not found",
            ))
            continue

        outcome = await delete_copy(source, entry.path)

        if outcome.ok:
            # Drop the row + the search index doc. Audit BEFORE the
            # row delete so the audit_event references something that
            # still exists in case the commit fails for any reason
            # (FK constraints aren't a concern; the audit row itself
            # is a sibling, not a parent).
            try:
                await record_event(
                    db=db,
                    user=admin,
                    event_type="duplicate_copy_deleted",
                    payload={
                        "content_hash": content_hash,
                        "entry_id": str(eid),
                        "source_id": str(entry.source_id),
                        "path": entry.path,
                        "size_bytes": entry.size_bytes,
                    },
                    request=request,
                    source_id=entry.source_id,
                )
            except Exception:  # noqa: BLE001
                logger.exception("audit failed for entry %s", eid)

            # Best-effort search-index delete. If Meili is down we still
            # want to clean up the postgres row — the index will be
            # repaired on the next scan.
            try:
                await delete_file_from_index(str(eid))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Meilisearch delete failed for %s: %s (postgres row will still be removed)",
                    eid, exc,
                )

            await db.delete(entry)
            # Commit per iteration (review D-C2): the physical file is
            # already gone from disk; if a later iteration crashes and
            # rolls back the surrounding transaction, this entry's row
            # would be revived in the DB pointing at a non-existent
            # file, which can never be cleaned up except by a full
            # rescan. Per-entry commit pairs the disk delete with the
            # row delete so they stay consistent.
            await db.commit()
            deleted.append(DeleteCopyOutcome(
                entry_id=str(eid),
                path=entry.path,
                ok=True,
            ))
        else:
            # Audit even the failure — gives admins a forensic trail
            # of "the user tried to delete X but the share said no."
            try:
                await record_event(
                    db=db,
                    user=admin,
                    event_type="duplicate_copy_delete_failed",
                    payload={
                        "content_hash": content_hash,
                        "entry_id": str(eid),
                        "source_id": str(entry.source_id),
                        "path": entry.path,
                        "step": outcome.step,
                        "message": outcome.message,
                    },
                    request=request,
                    source_id=entry.source_id,
                )
            except Exception:  # noqa: BLE001
                logger.exception("audit failed for entry %s (failure case)", eid)

            # Commit the failure audit_event per-iteration too, for
            # the same reason as the success branch — a crash on a
            # later iteration shouldn't lose the forensic record of
            # what we already attempted.
            await db.commit()
            failed.append(DeleteCopyOutcome(
                entry_id=str(eid),
                path=entry.path,
                ok=False,
                step=outcome.step,
                message=outcome.message,
            ))

    return DeleteCopiesResponse(deleted=deleted, failed=failed)
