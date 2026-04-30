import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user, get_permitted_source_ids, require_admin
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services.audit import record_event
from akashic.services.duplicate_delete import delete_copy
from akashic.services.search import delete_file_from_index

logger = logging.getLogger(__name__)

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


# ── Bulk delete ────────────────────────────────────────────────────────────


class DeleteCopiesRequest(BaseModel):
    keep_entry_id: uuid.UUID
    delete_entry_ids: list[uuid.UUID]


class DeleteCopyOutcome(BaseModel):
    entry_id: str
    path: str
    ok: bool
    step: str = ""
    message: str = ""


class DeleteCopiesResponse(BaseModel):
    deleted: list[DeleteCopyOutcome]
    failed: list[DeleteCopyOutcome]


@router.post(
    "/{content_hash}/delete-copies",
    response_model=DeleteCopiesResponse,
)
async def delete_duplicate_copies(
    content_hash: str,
    body: DeleteCopiesRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
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

            failed.append(DeleteCopyOutcome(
                entry_id=str(eid),
                path=entry.path,
                ok=False,
                step=outcome.step,
                message=outcome.message,
            ))

    await db.commit()

    return DeleteCopiesResponse(deleted=deleted, failed=failed)
