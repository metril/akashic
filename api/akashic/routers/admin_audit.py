"""Admin-only audit log read endpoints."""
import uuid
from datetime import datetime, timezone
from typing import Annotated

from akashic.routing import ApiRouter
from fastapi import Depends, HTTPException, Path, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import Principal, require_scope
from akashic.database import get_db
from akashic.models.audit_event import AuditEvent
from akashic.models.user import User
from akashic.schemas.audit import AuditEventOut
from akashic.schemas.common import Page, offset_from_cursor, offset_page

router = ApiRouter(prefix="/admin/audit", tags=["admin-audit"])


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


@router.get(
    "",
    response_model=Page[AuditEventOut],
    summary="List audit events",
    description=(
        "Newest first, optionally filtered. `total` is the number of events matching the filters.\n"
        "Requires scope `audit:read`.\n"
        "Errors: 422 for a malformed `cursor` or an unparseable `from`/`to` datetime."
    ),
)
async def list_audit_events(
    user_id: Annotated[uuid.UUID | None, Query(description="Only events performed by this user.")] = None,
    event_type: Annotated[str | None, Query(description="Only events of this type, e.g. `access_lookup`, `api_token_created`, `host_created`.", examples=['access_lookup'])] = None,
    source_id: Annotated[uuid.UUID | None, Query(description="Only events tied to this source.")] = None,
    from_: Annotated[str | None, Query(alias="from", description="Only events at or after this time (ISO 8601; naive values are taken as UTC).", examples=['2026-04-27T22:00:00+00:00'])] = None,
    to: Annotated[str | None, Query(description="Only events at or before this time (ISO 8601; naive values are taken as UTC).", examples=['2026-04-28T22:00:00+00:00'])] = None,
    cursor: str | None = Query(default=None, description="Opaque cursor from a previous `next_cursor`."),
    limit: int = Query(default=50, ge=1, le=200, description="Page size."),
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("audit:read"),
) -> dict:
    offset = offset_from_cursor(cursor)

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
        .offset(offset).limit(limit + 1)
    )).scalars().all()

    return {**offset_page([AuditEventOut.model_validate(r) for r in rows], offset, limit), "total": total}


@router.get(
    "/{event_id}",
    response_model=AuditEventOut,
    summary="Get an audit event",
    description="Requires scope `audit:read`. Errors: 404 if no event has this id.",
)
async def get_audit_event(
    event_id: Annotated[uuid.UUID, Path(description="Audit event id.")],
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("audit:read"),
) -> AuditEventOut:
    row = (await db.execute(
        select(AuditEvent).where(AuditEvent.id == event_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Audit event not found")
    return AuditEventOut.model_validate(row)
