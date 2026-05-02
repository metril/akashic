import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import check_source_access, get_current_user, get_permitted_source_ids
from akashic.database import get_db
from akashic.models.scan import Scan
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.scan import ScanResponse

router = APIRouter(prefix="/api/scans", tags=["scans"])


class ScanTriggerRequest(BaseModel):
    source_name: str | None = None
    source_id: uuid.UUID | None = None
    scan_type: Literal["incremental", "full"] = "incremental"


class ScanTriggerResponse(BaseModel):
    scan_id: uuid.UUID
    source_id: uuid.UUID
    source_name: str
    scan_type: str
    last_scan_at: str | None


@router.post("/trigger", response_model=ScanTriggerResponse)
async def trigger_scan(
    data: ScanTriggerRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Enqueue a scan into the lease queue.

    Phase 2 multi-scanner: the api no longer spawns a subprocess on
    its own host. We insert a `pending` Scan row tagged with the
    source's preferred_pool (or NULL for any scanner); a registered
    scanner agent picks it up via /api/scans/lease within a few
    seconds. Returns 202-shaped success even though the scan hasn't
    started — the UI watches the status field for "running" /
    "completed" via the existing polling.

    The `background` arg is retained for forward compatibility (e.g.
    a future post-enqueue webhook) but currently unused — no work
    runs on the api host as a result of this call.
    """
    if not data.source_name and not data.source_id:
        raise HTTPException(status_code=400, detail="source_name or source_id required")

    if data.source_id:
        result = await db.execute(select(Source).where(Source.id == data.source_id))
    else:
        result = await db.execute(select(Source).where(Source.name == data.source_name))

    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    await check_source_access(source.id, user, db, required_level="write")

    from akashic.services.scan_factory import previous_files_for_source
    prev = await previous_files_for_source(source.id, db)

    scan = Scan(
        source_id=source.id,
        scan_type=data.scan_type,
        status="pending",
        previous_scan_files=prev,
        # Snapshot the source's pool preference at enqueue time so
        # later edits to Source.preferred_pool don't reroute an
        # already-queued scan.
        pool=source.preferred_pool,
    )
    db.add(scan)
    # Phase-2 multi-scanner: don't flip source.status here. The
    # status field reflects what an *agent* is doing right now —
    # /lease sets it to 'scanning' on claim, /complete clears it.
    # Setting it on enqueue (v0.1.0 behaviour, when the api itself
    # spawned the subprocess immediately) leaves sources stuck on
    # 'scanning' forever when no scanner is registered.
    await db.commit()
    await db.refresh(scan)
    await db.refresh(source)

    # Phase-2 multi-scanner: push to the list-level WS so the
    # Sources page sees the queued scan instantly (no 2s poll).
    from akashic.services import scan_pubsub
    await scan_pubsub.publish_source_event({
        "kind": "scan.state",
        "source_id": str(source.id),
        "scan_id": str(scan.id),
        "scan_status": "pending",
        "source_status": source.status,
        "scanner_id": None,
        "scanner_name": None,
        "scan_type": data.scan_type,
        "files_found": 0,
        "current_path": None,
    })

    # `background` retained — see docstring. Linter happiness:
    _ = background

    return ScanTriggerResponse(
        scan_id=scan.id,
        source_id=source.id,
        source_name=source.name,
        scan_type=data.scan_type,
        last_scan_at=source.last_scan_at.isoformat() if source.last_scan_at else None,
    )


@router.get("", response_model=list[ScanResponse])
async def list_scans(
    source_id: uuid.UUID | None = None,
    status: str | None = None,
    offset: int = 0,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if source_id:
        await check_source_access(source_id, user, db)

    stmt = select(Scan)
    if source_id:
        stmt = stmt.where(Scan.source_id == source_id)
    else:
        allowed = await get_permitted_source_ids(user, db)
        if allowed is not None:
            stmt = stmt.where(Scan.source_id.in_(allowed)) if allowed else stmt.where(False)
    if status:
        # Comma-separated list supported so the UI can poll
        # `?status=running,pending` in one request.
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if len(statuses) == 1:
            stmt = stmt.where(Scan.status == statuses[0])
        elif statuses:
            stmt = stmt.where(Scan.status.in_(statuses))
    stmt = stmt.order_by(Scan.started_at.desc().nulls_first()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{scan_id}", response_model=ScanResponse)
async def get_scan(
    scan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(Scan).where(Scan.id == scan_id))
    scan = result.scalar_one_or_none()
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    await check_source_access(scan.source_id, user, db)
    return scan


class CancelResponse(BaseModel):
    scan_id: uuid.UUID
    status: str


@router.post("/{scan_id}/cancel", response_model=CancelResponse)
async def cancel_scan(
    scan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Mark a running scan as cancelled. The next heartbeat from the
    scanner will receive HTTP 409 and exit cleanly. Idempotent: calling
    cancel on an already-terminal scan returns the current status
    without raising.

    The source's status flips back to 'online' so subsequent triggers
    work without waiting for the watchdog. The actual scanner process
    won't terminate instantly — it learns about the cancellation on its
    next heartbeat tick (≤1 s)."""
    from datetime import datetime, timezone

    result = await db.execute(select(Scan).where(Scan.id == scan_id))
    scan = result.scalar_one_or_none()
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    await check_source_access(scan.source_id, user, db, required_level="write")

    if scan.status in {"pending", "running"}:
        scan.status = "cancelled"
        if scan.completed_at is None:
            scan.completed_at = datetime.now(timezone.utc)
        scan.error_message = "Cancelled by user"

        # Flip the source back to online so the user can immediately
        # retrigger. If the scanner is mid-flight it'll keep posting
        # heartbeats for a few seconds — the heartbeat endpoint refuses
        # those (409) and the scanner exits.
        source_result = await db.execute(select(Source).where(Source.id == scan.source_id))
        source = source_result.scalar_one_or_none()
        if source is not None and source.status == "scanning":
            source.status = "online"

        await db.commit()
    return CancelResponse(scan_id=scan.id, status=scan.status)
