import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import check_source_access, get_current_user, require_admin
from akashic.database import get_db
from akashic.models.audit_event import AuditEvent
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
    await db.delete(source)
    await db.commit()
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
        payload=snapshot,
    )
