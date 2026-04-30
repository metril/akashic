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
from akashic.services.scan_runner import spawn_scan

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
    """Create a scan record AND spawn the scanner subprocess.

    Pre-fix this endpoint just inserted a `pending` Scan row and
    expected an external orchestrator (HA integration, CLI, etc.) to
    actually run the binary. That meant the UI's "Scan now" button was
    a dead end on a vanilla install — no logs, no progress, no
    completion. The runner now spawns the bundled scanner via
    BackgroundTasks so the trigger response returns immediately and
    the scan begins within ~the request roundtrip.
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
    )
    db.add(scan)
    source.status = "scanning"
    await db.commit()
    await db.refresh(scan)

    # Snapshot the fields spawn_scan needs while we still have the
    # Source row attached to the session, then schedule the subprocess
    # to start after the response is sent. BackgroundTasks runs
    # *after* `await db.commit()`, so the scanner-side first heartbeat
    # is guaranteed to find the row.
    await db.refresh(source)
    background.add_task(spawn_scan, source, scan, user)

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
