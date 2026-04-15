import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.models.scan import Scan
from akashic.models.user import User
from akashic.schemas.scan import ScanResponse

router = APIRouter(prefix="/api/scans", tags=["scans"])


@router.get("", response_model=list[ScanResponse])
async def list_scans(
    source_id: uuid.UUID | None = None,
    status: str | None = None,
    offset: int = 0,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(Scan)
    if source_id:
        stmt = stmt.where(Scan.source_id == source_id)
    if status:
        stmt = stmt.where(Scan.status == status)
    stmt = stmt.order_by(Scan.started_at.desc()).offset(offset).limit(limit)
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
    return scan
