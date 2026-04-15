import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
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
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a scan record and return the info needed to run the scanner.

    The caller (CLI, HA integration, or scheduler) uses the returned scan_id
    and source config to invoke the Go scanner binary.
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

    scan = Scan(
        source_id=source.id,
        scan_type=data.scan_type,
        status="pending",
    )
    db.add(scan)
    source.status = "scanning"
    await db.commit()
    await db.refresh(scan)

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
        stmt = stmt.where(Scan.status == status)
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
