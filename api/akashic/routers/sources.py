import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import delete as sql_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import check_source_access, get_current_user, require_admin
from akashic.database import get_db
from akashic.models.audit_event import AuditEvent
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import SourcePermission, User
from akashic.schemas.audit import AuditEventList, AuditEventOut
from akashic.schemas.source import SourceCreate, SourceUpdate, SourceResponse
from akashic.services.audit import record_event
from akashic.services.source_merge import (
    field_diff,
    merge_connection_config,
    reject_sentinel_in_create,
)

router = APIRouter(prefix="/api/sources", tags=["sources"])


def _config_safe_summary(cfg: dict | None) -> dict:
    """Audit-safe snapshot of a connection_config: state tokens for
    secret keys, real values for the rest."""
    return {
        k: ("<set>" if v else "<empty>") if any(s in k.lower() for s in {"password", "secret", "key", "token", "credentials", "private_key"}) else v
        for k, v in (cfg or {}).items()
    }


@router.post("", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def create_source(
    data: SourceCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    err = reject_sentinel_in_create(data.connection_config)
    if err:
        raise HTTPException(status_code=400, detail=err)
    source = Source(**data.model_dump())
    db.add(source)
    await db.commit()
    await db.refresh(source)
    # Push to /ws/scans subscribers so the Sources page sees the
    # new card without polling.
    from akashic.services import scan_pubsub
    await scan_pubsub.publish_source_event({
        "kind": "source.created",
        "source_id": str(source.id),
        "source_status": source.status,
        "name": source.name,
        "type": source.type,
    })
    await record_event(
        db=db,
        user=user,
        event_type="source_created",
        source_id=source.id,
        request=request,
        payload={
            "name": source.name,
            "type": source.type,
            "config": _config_safe_summary(source.connection_config),
            "scan_schedule": source.scan_schedule,
        },
    )
    return source


@router.get("", response_model=list[SourceResponse])
async def list_sources(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role == "admin":
        result = await db.execute(select(Source).order_by(Source.name))
    else:
        # Non-admins only see sources they have permission for
        result = await db.execute(
            select(Source)
            .join(SourcePermission, Source.id == SourcePermission.source_id)
            .where(SourcePermission.user_id == user.id)
            .order_by(Source.name)
        )
    return result.scalars().all()


@router.get("/{source_id}", response_model=SourceResponse)
async def get_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await check_source_access(source_id, user, db)
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.get("/{source_id}/audit", response_model=AuditEventList)
async def get_source_audit(
    source_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Per-source audit timeline. Visible to any user with read access
    to the source — the same gate that lets them browse its files.

    Includes orphaned `source_deleted` events whose payload encodes the
    original source ID (the source row is gone by the time we record
    the event, so we can't FK it). Without this, the timeline would
    abruptly end at the second-to-last event for any deleted source.

    Pagination is offset-based but `total` is intentionally not
    computed — for high-traffic sources the COUNT(*) doubles every
    page request's cost. The UI uses page-by-page navigation; if it
    needs an exact total later we can add an opt-in flag.
    """
    await check_source_access(source_id, user, db, required_level="read")

    from sqlalchemy import or_
    stmt = (
        select(AuditEvent)
        .where(or_(
            AuditEvent.source_id == source_id,
            # Orphaned deletion events live without a source_id but
            # encode the original UUID in their payload.
            AuditEvent.payload["deleted_source_id"].astext == str(source_id),
        ))
        .order_by(AuditEvent.occurred_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size + 1)  # +1 to detect "more pages exist"
    )
    rows = (await db.execute(stmt)).scalars().all()
    has_more = len(rows) > page_size
    if has_more:
        rows = rows[:page_size]
    return AuditEventList(
        items=[AuditEventOut.model_validate(r) for r in rows],
        # `total = -1` signals "unknown — use page+has_more instead".
        # Frontend reads `len(items) < page_size or has_more` to render
        # next/prev controls.
        total=-1,
        page=page,
        page_size=page_size,
    )


@router.patch("/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: uuid.UUID,
    data: SourceUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    # Snapshot the before-state for the audit diff. Capture this BEFORE
    # any mutation so we have stable old values to compare against.
    before = {
        "name": source.name,
        "type": source.type,
        "connection_config": dict(source.connection_config or {}),
        "scan_schedule": source.scan_schedule,
        "exclude_patterns": list(source.exclude_patterns or []),
    }

    incoming = data.model_dump(exclude_unset=True)
    if "connection_config" in incoming and incoming["connection_config"]:
        # Reject "***" on non-secret keys at the validation layer —
        # never a meaningful value. Secret-named keys with "***" are
        # legitimate (the merge will preserve the existing secret),
        # so let those pass through to merge_connection_config.
        for k, v in incoming["connection_config"].items():
            if v == "***" and not any(
                token in k.lower()
                for token in {"password", "secret", "key", "token", "credentials", "private_key"}
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"connection_config.{k} = \"***\" — that's the "
                        "masked-secret sentinel; not a valid value for a "
                        "non-secret field."
                    ),
                )
    for field, value in incoming.items():
        if field == "connection_config":
            # Secret-merge: preserve real secrets when the UI sends back
            # the masked sentinel `"***"`. See source_merge.py for why.
            value = merge_connection_config(source.connection_config, value)
        setattr(source, field, value)
    await db.commit()
    await db.refresh(source)

    after = {
        "name": source.name,
        "type": source.type,
        "connection_config": dict(source.connection_config or {}),
        "scan_schedule": source.scan_schedule,
        "exclude_patterns": list(source.exclude_patterns or []),
    }
    diff_payload: dict = {}
    for field, after_val in after.items():
        if field == "connection_config":
            cfg_diff = field_diff(before["connection_config"], after_val)
            if cfg_diff:
                diff_payload["connection_config"] = cfg_diff
        elif before[field] != after_val:
            diff_payload[field] = {"before": before[field], "after": after_val}

    if diff_payload:
        await record_event(
            db=db,
            user=user,
            event_type="source_updated",
            source_id=source.id,
            request=request,
            payload={"diff": diff_payload},
        )
    return source


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: uuid.UUID,
    request: Request,
    purge_entries: bool = Query(
        False,
        description=(
            "When true, also delete every indexed entry from this source. "
            "Default false: source row is removed but entries survive with "
            "source_id=NULL — they stay searchable, content fetch returns "
            "404, and they can be re-attached to a new source via "
            "POST /sources/{id}/reattach-orphans."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    snapshot = {
        "deleted_source_id": str(source.id),
        "name": source.name,
        "type": source.type,
        "config": _config_safe_summary(source.connection_config),
    }
    deleted_id = source.id

    # Snapshot the affected entry ids BEFORE the source delete so we
    # can sync Meilisearch in either flavour. On the preserve path
    # the FK rule will SET source_id=NULL after `db.delete(source)`;
    # on the purge path we explicitly delete the entries first
    # (otherwise the SET-NULL FK rule would fire and orphan them
    # before the purge runs).
    affected_entry_ids = list((await db.execute(
        select(Entry.id).where(Entry.source_id == source_id)
    )).scalars().all())

    if purge_entries:
        await db.execute(sql_delete(Entry).where(Entry.source_id == source_id))

    await db.delete(source)
    await db.commit()

    # Sync Meilisearch. Failures here are logged but don't break the
    # delete — search index drift is recoverable, a half-deleted
    # source row is not.
    from akashic.services import search
    try:
        if purge_entries:
            await search.delete_files_batch([str(i) for i in affected_entry_ids])
        elif affected_entry_ids:
            await search.update_files_partial(
                [{"id": str(i), "source_id": None} for i in affected_entry_ids]
            )
    except Exception:  # noqa: BLE001
        # Caller already saw the source delete succeed; surface the
        # search-sync issue via logs rather than 500'ing.
        import logging
        logging.getLogger(__name__).warning(
            "delete_source: search-index sync failed for %s entries",
            len(affected_entry_ids),
        )

    from akashic.services import scan_pubsub
    await scan_pubsub.publish_source_event({
        "kind": "source.deleted",
        "source_id": str(deleted_id),
    })
    # Pass source_id=None — the row is gone and the FK on audit_events
    # would reject an INSERT referencing it. The original ID lives in
    # the payload so the timeline still surfaces the deletion.
    await record_event(
        db=db,
        user=user,
        event_type="source_deleted",
        source_id=None,
        request=request,
        payload={
            **snapshot,
            "purge_entries": purge_entries,
            "affected_entry_count": len(affected_entry_ids),
        },
    )


class SourceEntryCount(BaseModel):
    count: int


@router.get("/{source_id}/entry-count", response_model=SourceEntryCount)
async def get_source_entry_count(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Cheap COUNT used by the delete-source modal to show blast
    radius. Excludes soft-deleted entries (`is_deleted = true`)
    since those wouldn't be visibly affected by the delete."""
    await check_source_access(source_id, user, db, required_level="read")
    cnt = (await db.execute(
        select(func.count())
        .select_from(Entry)
        .where(Entry.source_id == source_id, Entry.is_deleted.is_(False))
    )).scalar_one()
    return SourceEntryCount(count=int(cnt))


# ── Orphan recovery (v0.4.0) ─────────────────────────────────────────────


class OrphanMatchCount(BaseModel):
    count: int


@router.get(
    "/{source_id}/orphan-match-count",
    response_model=OrphanMatchCount,
)
async def get_orphan_match_count(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """How many orphaned entries (source_id IS NULL) share a path
    with an entry of THIS source. Used by the source-detail
    banner to decide whether to surface the 'Recover orphans'
    affordance — returns instantly because it's a single COUNT on
    indexed columns."""
    from akashic.services.orphan_matcher import count_potential_matches
    cnt = await count_potential_matches(db, source_id)
    return OrphanMatchCount(count=cnt)


class ReattachRequest(BaseModel):
    strategy: str = "path"  # "path" | "path_and_hash"
    dry_run: bool = True


class ReattachResponse(BaseModel):
    matched: int
    conflicts: int
    ambiguous: int
    committed: bool


@router.post(
    "/{source_id}/reattach-orphans",
    response_model=ReattachResponse,
)
async def reattach_orphans(
    source_id: uuid.UUID,
    body: ReattachRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Re-attach orphaned entries (source_id IS NULL) into this
    source where (path, name, kind) — and optionally content_hash
    — match a freshly-scanned entry. The orphan keeps its history
    (tags, version history, audit trail); the duplicate fresh
    entry is deleted."""
    from akashic.services.orphan_matcher import (
        Strategy, commit_matches, find_matches,
    )
    from akashic.services import search

    if body.strategy not in ("path", "path_and_hash"):
        raise HTTPException(
            status_code=400,
            detail=f"unknown strategy '{body.strategy}'; "
                   "expected 'path' or 'path_and_hash'",
        )
    # Source must exist (otherwise there are no fresh entries to
    # match against, but we want a clear 404 either way).
    src = (await db.execute(
        select(Source).where(Source.id == source_id)
    )).scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")

    summary = await find_matches(db, source_id, body.strategy)  # type: ignore[arg-type]

    if body.dry_run:
        return ReattachResponse(
            matched=summary.matched,
            conflicts=summary.conflicts,
            ambiguous=summary.ambiguous,
            committed=False,
        )

    reattached_ids = await commit_matches(db, source_id, summary.pairs)
    await db.commit()

    # Sync Meilisearch — re-attached docs get the new source_id.
    if reattached_ids:
        try:
            await search.update_files_partial(
                [{"id": str(i), "source_id": str(source_id)} for i in reattached_ids]
            )
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "reattach_orphans: search-index sync failed for %s entries",
                len(reattached_ids),
            )

    await record_event(
        db=db, user=user, event_type="source_orphans_reattached",
        source_id=source_id, request=request,
        payload={
            "strategy": body.strategy,
            "matched": summary.matched,
            "conflicts": summary.conflicts,
            "ambiguous": summary.ambiguous,
        },
    )
    return ReattachResponse(
        matched=summary.matched,
        conflicts=summary.conflicts,
        ambiguous=summary.ambiguous,
        committed=True,
    )
