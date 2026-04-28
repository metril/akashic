"""Admin-only audit log read endpoints."""
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import require_admin
from akashic.database import get_db
from akashic.models.audit_event import AuditEvent
from akashic.models.user import User
from akashic.schemas.audit import AuditEventList, AuditEventOut

router = APIRouter(prefix="/api/admin/audit", tags=["admin-audit"])


def _parse_dt(raw: str | None) -> datetime | None:
    """Parse a datetime string, restoring '+' that may have been decoded as ' '
    by query string parsers (RFC 3986 treats '+' as a space in query params)."""
    if raw is None:
        return None
    # Restore the '+' sign in timezone offset if it was decoded as a space.
    # e.g. "2026-04-27T22:00:00.000000 00:00" → "2026-04-27T22:00:00.000000+00:00"
    fixed = raw.replace(" ", "+")
    try:
        dt = datetime.fromisoformat(fixed)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid datetime: {exc}")


@router.get("", response_model=AuditEventList)
async def list_audit_events(
    user_id: uuid.UUID | None = None,
    event_type: str | None = None,
    source_id: uuid.UUID | None = None,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> AuditEventList:
    page = max(1, page)
    page_size = max(1, min(page_size, 200))

    from_dt = _parse_dt(from_)
    to_dt = _parse_dt(to)

    conditions = []
    if user_id is not None:
        conditions.append(AuditEvent.user_id == user_id)
    if event_type is not None:
        conditions.append(AuditEvent.event_type == event_type)
    if source_id is not None:
        conditions.append(AuditEvent.source_id == source_id)
    if from_dt is not None:
        conditions.append(AuditEvent.occurred_at >= from_dt)
    if to_dt is not None:
        conditions.append(AuditEvent.occurred_at <= to_dt)

    count_stmt = select(func.count(AuditEvent.id))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total = (await db.execute(count_stmt)).scalar() or 0

    base = select(AuditEvent)
    if conditions:
        base = base.where(*conditions)
    rows = (await db.execute(
        base.order_by(desc(AuditEvent.occurred_at))
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()

    return AuditEventList(
        items=[AuditEventOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{event_id}", response_model=AuditEventOut)
async def get_audit_event(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> AuditEventOut:
    row = (await db.execute(
        select(AuditEvent).where(AuditEvent.id == event_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Audit event not found")
    return AuditEventOut.model_validate(row)
