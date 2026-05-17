"""Admin-only maintenance tooling.

Backs the web Maintenance page: a system-state overview, scan & log
hygiene (cancel stuck scans, run the watchdog, purge old logs), and the
search reindex / backfill jobs. Every endpoint requires an admin user;
destructive actions are written to the audit log.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import require_admin
from akashic.database import get_db
from akashic.models.entry import Entry
from akashic.models.maintenance_job import MaintenanceJob
from akashic.models.scan import Scan
from akashic.models.scan_log_entry import ScanLogEntry
from akashic.models.scanner import Scanner
from akashic.models.source import Source
from akashic.models.user import User
from akashic.routers.scanners import ONLINE_WINDOW_SECONDS
from akashic.routers.scans import cancel_scan_core
from akashic.scheduler import (
    _LOG_RETENTION_DAYS,
    _check_stale_scans,
    _requeue_orphan_leases,
    purge_scan_logs,
    purge_scan_logs_for_scan,
)
from akashic.services import maintenance_jobs as mj
from akashic.services.audit import record_event

router = APIRouter(prefix="/api/admin/maintenance", tags=["maintenance"])

_ACTIVE_STATUSES = ("pending", "running")
_TERMINAL_STATUSES = ("completed", "failed", "cancelled")


# — schemas —

class OverviewOut(BaseModel):
    scans_by_status: dict[str, int]
    scans_active: int
    entries_total: int
    sources_total: int
    scanners_total: int
    scanners_online: int
    scan_log_rows: int
    scan_log_purgeable: int
    log_retention_days: int
    meili_documents: int | None


class StuckScanOut(BaseModel):
    scan_id: uuid.UUID
    source_id: uuid.UUID | None
    source_name: str | None
    status: str
    scan_type: str
    started_at: datetime | None
    last_heartbeat_at: datetime | None
    age_seconds: float | None
    assigned_scanner_id: uuid.UUID | None
    assigned_scanner_name: str | None


class CancelOut(BaseModel):
    scan_id: uuid.UUID
    status: str


class WatchdogOut(BaseModel):
    active_before: int
    active_after: int


class PurgeLogsRequest(BaseModel):
    older_than_days: int | None = Field(default=None, ge=0)
    scan_id: uuid.UUID | None = None


class PurgeLogsOut(BaseModel):
    deleted: int


class JobOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    params: dict
    result: dict | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class StartJobRequest(BaseModel):
    kind: str
    params: dict = Field(default_factory=dict)


# — endpoints —

@router.get("/overview", response_model=OverviewOut)
async def overview(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> OverviewOut:
    """Data-state counts for the Maintenance page. Liveness of external
    services lives on the separate /admin/system-status page."""
    status_rows = (await db.execute(
        select(Scan.status, func.count(Scan.id)).group_by(Scan.status)
    )).all()
    scans_by_status = {status: count for status, count in status_rows}
    scans_active = sum(scans_by_status.get(s, 0) for s in _ACTIVE_STATUSES)

    entries_total = (await db.execute(select(func.count(Entry.id)))).scalar() or 0
    sources_total = (await db.execute(select(func.count(Source.id)))).scalar() or 0
    scanners_total = (await db.execute(select(func.count(Scanner.id)))).scalar() or 0

    online_cutoff = datetime.now(timezone.utc) - timedelta(seconds=ONLINE_WINDOW_SECONDS)
    scanners_online = (await db.execute(
        select(func.count(Scanner.id)).where(Scanner.last_seen_at >= online_cutoff)
    )).scalar() or 0

    scan_log_rows = (await db.execute(
        select(func.count(ScanLogEntry.id))
    )).scalar() or 0

    # Purgeable = log rows whose terminal parent scan completed past the
    # retention window — what the hourly cleanup loop would sweep.
    cutoff = datetime.now(timezone.utc) - timedelta(days=_LOG_RETENTION_DAYS)
    stale_scan_ids = select(Scan.id).where(
        Scan.status.in_(_TERMINAL_STATUSES),
        Scan.completed_at.isnot(None),
        Scan.completed_at < cutoff,
    ).subquery()
    scan_log_purgeable = (await db.execute(
        select(func.count(ScanLogEntry.id)).where(
            ScanLogEntry.scan_id.in_(select(stale_scan_ids))
        )
    )).scalar() or 0

    # Meili doc count is best-effort — a Meili outage must not 500 the page.
    meili_documents: int | None = None
    try:
        from akashic.routers.health_services import _meili_activity
        meili_documents = (await _meili_activity()).get("documents_in_index")
    except Exception:  # noqa: BLE001
        meili_documents = None

    return OverviewOut(
        scans_by_status=scans_by_status,
        scans_active=scans_active,
        entries_total=entries_total,
        sources_total=sources_total,
        scanners_total=scanners_total,
        scanners_online=scanners_online,
        scan_log_rows=scan_log_rows,
        scan_log_purgeable=scan_log_purgeable,
        log_retention_days=_LOG_RETENTION_DAYS,
        meili_documents=meili_documents,
    )


@router.get("/scans/stuck", response_model=list[StuckScanOut])
async def stuck_scans(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> list[StuckScanOut]:
    """Every non-terminal scan, admin-wide (no source ACL). Oldest first."""
    rows = (await db.execute(
        select(Scan, Source.name, Scanner.name)
        .outerjoin(Source, Scan.source_id == Source.id)
        .outerjoin(Scanner, Scan.assigned_scanner_id == Scanner.id)
        .where(Scan.status.in_(_ACTIVE_STATUSES))
        .order_by(Scan.started_at.asc().nulls_first())
    )).all()
    now = datetime.now(timezone.utc)
    out: list[StuckScanOut] = []
    for scan, source_name, scanner_name in rows:
        ref = scan.started_at
        age = (now - ref).total_seconds() if ref is not None else None
        out.append(StuckScanOut(
            scan_id=scan.id,
            source_id=scan.source_id,
            source_name=source_name,
            status=scan.status,
            scan_type=scan.scan_type,
            started_at=scan.started_at,
            last_heartbeat_at=scan.last_heartbeat_at,
            age_seconds=age,
            assigned_scanner_id=scan.assigned_scanner_id,
            assigned_scanner_name=scanner_name,
        ))
    return out


@router.post("/scans/{scan_id}/cancel", response_model=CancelOut)
async def cancel_stuck_scan(
    scan_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> CancelOut:
    """Admin force-cancel — no source ACL check. Idempotent: cancelling
    an already-terminal scan returns its current status."""
    scan = (await db.execute(
        select(Scan).where(Scan.id == scan_id)
    )).scalar_one_or_none()
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    status = await cancel_scan_core(db, scan, reason="admin")
    await record_event(
        db=db, user=admin, event_type="maintenance.scan_cancel",
        payload={"scan_id": str(scan_id), "result_status": status},
        request=request, source_id=scan.source_id,
    )
    return CancelOut(scan_id=scan_id, status=status)


@router.post("/watchdog/run", response_model=WatchdogOut)
async def run_watchdog(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> WatchdogOut:
    """Run the stale-scan watchdog now: re-queue expired leases, then
    fail scans past the stale threshold — instead of waiting up to 60 s
    for the scheduler's next pass."""
    async def _count_active() -> int:
        return (await db.execute(
            select(func.count(Scan.id)).where(Scan.status.in_(_ACTIVE_STATUSES))
        )).scalar() or 0

    before = await _count_active()
    await _requeue_orphan_leases()
    await _check_stale_scans()
    await db.rollback()  # drop the pre-watchdog snapshot before re-counting
    after = await _count_active()

    await record_event(
        db=db, user=admin, event_type="maintenance.watchdog_run",
        payload={"active_before": before, "active_after": after},
        request=request,
    )
    return WatchdogOut(active_before=before, active_after=after)


@router.post("/logs/purge", response_model=PurgeLogsOut)
async def purge_logs(
    body: PurgeLogsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> PurgeLogsOut:
    """Delete scan log entries. Provide exactly one of `older_than_days`
    (sweep terminal scans completed past N days) or `scan_id` (clear one
    scan's logs regardless of status)."""
    if (body.older_than_days is None) == (body.scan_id is None):
        raise HTTPException(
            status_code=400,
            detail="provide exactly one of older_than_days or scan_id",
        )
    if body.scan_id is not None:
        deleted = await purge_scan_logs_for_scan(db, body.scan_id)
        payload = {"scan_id": str(body.scan_id), "deleted": deleted}
    else:
        deleted = await purge_scan_logs(db, body.older_than_days)
        payload = {"older_than_days": body.older_than_days, "deleted": deleted}
    await record_event(
        db=db, user=admin, event_type="maintenance.logs_purge",
        payload=payload, request=request,
    )
    return PurgeLogsOut(deleted=deleted)


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> list[JobOut]:
    """The 20 most recent maintenance jobs, newest first."""
    rows = (await db.execute(
        select(MaintenanceJob)
        .order_by(desc(MaintenanceJob.started_at))
        .limit(20)
    )).scalars().all()
    return [JobOut.model_validate(r) for r in rows]


@router.post("/jobs", response_model=JobOut, status_code=202)
async def start_job(
    body: StartJobRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> JobOut:
    """Kick off a long-running maintenance job (reindex or a backfill).
    Returns immediately with a `running` job row; poll GET /jobs."""
    if body.kind not in mj.JOB_KINDS:
        raise HTTPException(status_code=400, detail=f"unknown job kind: {body.kind}")
    try:
        job = await mj.start_job(db, body.kind, body.params, admin)
    except mj.JobAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await record_event(
        db=db, user=admin, event_type="maintenance.job_start",
        payload={"kind": body.kind, "job_id": str(job.id), "params": body.params},
        request=request,
    )
    return JobOut.model_validate(job)
