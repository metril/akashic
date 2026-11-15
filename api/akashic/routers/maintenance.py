"""Admin-only maintenance tooling.

Backs the web Maintenance page: a system-state overview, scan & log
hygiene (cancel stuck scans, run the watchdog, purge old logs), and the
search reindex / backfill jobs. Every endpoint requires an admin user;
destructive actions are written to the audit log.
"""
from __future__ import annotations

from typing import Annotated
import uuid
from datetime import datetime, timedelta, timezone

from akashic.routing import ApiRouter
from fastapi import Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import Principal, require_scope
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

router = ApiRouter(prefix="/admin/maintenance", tags=["maintenance"])

_ACTIVE_STATUSES = ("pending", "running")
_TERMINAL_STATUSES = ("completed", "failed", "cancelled")


# — schemas —

class OverviewOut(BaseModel):
    scans_by_status: dict[str, int] = Field(description="Number of scans in each status (`pending`, `running`, `completed`, `failed`, `cancelled`).")
    scans_active: int = Field(description="Scans that are `pending` or `running`.")
    entries_total: int = Field(description="Indexed entries (files and directories) across all sources.")
    sources_total: int = Field(description="Number of sources.")
    scanners_total: int = Field(description="Number of registered scanners.")
    scanners_online: int = Field(description="Scanners seen within the last 90 seconds.")
    scan_log_rows: int = Field(description="Scan log rows stored.")
    scan_log_purgeable: int = Field(description="Log rows of scans that ended more than the retention period ago; the hourly sweep deletes these.")
    log_retention_days: int = Field(description="Days a finished scan's logs are kept.")
    meili_documents: int | None = Field(description="Documents in the search index; null if the search service could not be reached.")


class StuckScanOut(BaseModel):
    scan_id: uuid.UUID = Field(description="Scan id.")
    source_id: uuid.UUID | None = Field(description="Source being scanned; null if it was deleted.")
    source_name: str | None = Field(description="Source name.")
    status: str = Field(description="`pending` or `running`.")
    scan_type: str = Field(description="`full` or `incremental`.")
    started_at: datetime | None = Field(description="When the scan started; null while still waiting for a scanner.")
    last_heartbeat_at: datetime | None = Field(description="Last progress heartbeat from the scanner.")
    age_seconds: float | None = Field(description="Seconds since the scan started.")
    assigned_scanner_id: uuid.UUID | None = Field(description="Scanner holding the lease, if any.")
    assigned_scanner_name: str | None = Field(description="Name of that scanner.")


class CancelOut(BaseModel):
    scan_id: uuid.UUID = Field(description="Scan id.")
    status: str = Field(description="Scan status after the call (`cancelled`, or the terminal status it already had).")


class WatchdogOut(BaseModel):
    active_before: int = Field(description="Active scans before the pass.")
    active_after: int = Field(description="Active scans after the pass.")


class PurgeLogsRequest(BaseModel):
    older_than_days: int | None = Field(default=None, ge=0, description="Delete logs of finished scans that completed more than this many days ago (`0` for all finished scans). Mutually exclusive with `scan_id`.")
    scan_id: uuid.UUID | None = Field(default=None, description="Delete all logs of this one scan, whatever its status. Mutually exclusive with `older_than_days`.")

    model_config = {"json_schema_extra": {'examples': [{'older_than_days': 14}]}}


class PurgeLogsOut(BaseModel):
    deleted: int = Field(description="Log rows deleted.")


class JobOut(BaseModel):
    id: uuid.UUID = Field(description="Job id.")
    kind: str = Field(description="Job kind: `reindex_search`, `backfill_subtree_sizes`, `backfill_viewable` or `warm_groups`.")
    status: str = Field(description="`running`, `succeeded` or `failed`.")
    params: dict = Field(description="Parameters the job was started with.")
    result: dict | None = Field(description="Result when finished, e.g. `{\"rows_affected\": 1200}`.")
    error: str | None = Field(description="Error message when the job failed.")
    started_at: datetime = Field(description="When the job started.")
    finished_at: datetime | None = Field(description="When the job finished; null while running.")

    model_config = {"from_attributes": True}


class StartJobRequest(BaseModel):
    kind: str = Field(description="Job to run: `reindex_search`, `backfill_subtree_sizes`, `backfill_viewable` or `warm_groups`.")
    params: dict = Field(default_factory=dict, description="Optional job parameters: `batch_size` (`reindex_search`, `backfill_viewable`) and `source_id` (the three backfills and `warm_groups`).")

    model_config = {"json_schema_extra": {'examples': [{'kind': 'reindex_search', 'params': {'batch_size': 100}}]}}


# — endpoints —

@router.get(
    "/overview",
    response_model=OverviewOut,
    summary="System state overview",
    description=(
        "Counts for the Maintenance page: scans by status and how many are active, total entries, "
        "sources and scanners, scanners seen within the last 90 seconds, scan log rows and how many "
        "the retention sweep (7 days after a scan ends) would delete, and the Meilisearch document "
        "count (null if the search service cannot be reached).\n"
        "Requires scope `maintenance:write`. Not available to source-limited tokens."
    ),
)
async def overview(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("maintenance:write"),
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


@router.get(
    "/scans/stuck",
    response_model=list[StuckScanOut],
    summary="List active scans",
    description=(
        "Every scan that is still `pending` or `running`, across all sources, oldest first, with the "
        "assigned scanner and the age since start. Use it to find scans that stopped making progress; "
        "combine with the heartbeat time to judge.\n"
        "Requires scope `maintenance:write`. Not available to source-limited tokens."
    ),
)
async def stuck_scans(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("maintenance:write"),
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


@router.post(
    "/scans/{scan_id}/cancel",
    response_model=CancelOut,
    summary="Force-cancel a scan",
    description=(
        "Cancels a `pending` or `running` scan regardless of who started it, marking it `cancelled` "
        "with reason `admin` and returning its source to `online`. Idempotent: a scan that already "
        "ended is returned unchanged with its current status. The scanner notices on its next "
        "heartbeat. Records a `maintenance.scan_cancel` audit event.\n"
        "Requires scope `maintenance:write`. Errors: 404 if the scan does not exist."
    ),
)
async def cancel_stuck_scan(
    scan_id: Annotated[uuid.UUID, Path(description="Scan to cancel.")],
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("maintenance:write"),
) -> CancelOut:
    """Admin force-cancel — no source ACL check. Idempotent: cancelling
    an already-terminal scan returns its current status."""
    admin = principal.user
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


@router.post(
    "/watchdog/run",
    response_model=WatchdogOut,
    summary="Run the stale-scan watchdog now",
    description=(
        "Runs the watchdog pass immediately instead of waiting for the scheduler (up to 60 seconds): "
        "leases that expired are re-queued, then scans idle past the stale threshold are failed. "
        "Returns the number of active scans before and after. Records a `maintenance.watchdog_run` "
        "audit event.\n"
        "Requires scope `maintenance:write`."
    ),
)
async def run_watchdog(
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("maintenance:write"),
) -> WatchdogOut:
    """Run the stale-scan watchdog now: re-queue expired leases, then
    fail scans past the stale threshold — instead of waiting up to 60 s
    for the scheduler's next pass."""
    admin = principal.user
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


@router.post(
    "/logs/purge",
    response_model=PurgeLogsOut,
    summary="Purge scan logs",
    description=(
        "Deletes scan log entries. Give exactly one of `older_than_days` (delete the logs of every "
        "finished scan that completed more than that many days ago; `0` deletes all finished scans' "
        "logs) or `scan_id` (delete one scan's logs whatever its status). Records a "
        "`maintenance.logs_purge` audit event.\n"
        "Requires scope `maintenance:write`. Errors: 400 unless exactly one of the two fields is "
        "given; 422 for a negative number of days."
    ),
)
async def purge_logs(
    body: PurgeLogsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("maintenance:write"),
) -> PurgeLogsOut:
    """Delete scan log entries. Provide exactly one of `older_than_days`
    (sweep terminal scans completed past N days) or `scan_id` (clear one
    scan's logs regardless of status)."""
    admin = principal.user
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


@router.get(
    "/jobs",
    response_model=list[JobOut],
    summary="List maintenance jobs",
    description=(
        "The 20 most recent maintenance jobs, newest first, with status, parameters and result.\n"
        "Requires scope `maintenance:write`."
    ),
)
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("maintenance:write"),
) -> list[JobOut]:
    """The 20 most recent maintenance jobs, newest first."""
    rows = (await db.execute(
        select(MaintenanceJob)
        .order_by(desc(MaintenanceJob.started_at))
        .limit(20)
    )).scalars().all()
    return [JobOut.model_validate(r) for r in rows]


@router.post(
    "/jobs",
    response_model=JobOut,
    status_code=202,
    summary="Start a maintenance job",
    description=(
        "Starts a long-running job in the background and returns immediately (202) with a `running` "
        "job; poll `GET /admin/maintenance/jobs` for the outcome. Kinds: `reindex_search` (rebuild "
        "the search index; optional `batch_size`), `backfill_subtree_sizes` (optional `source_id`), "
        "`backfill_viewable` (optional `batch_size`, `source_id`) and `warm_groups` (optional "
        "`source_id`). Only one job of a kind runs at a time. The job does not survive an API "
        "restart; it is then marked `failed`. Records a `maintenance.job_start` audit event.\n"
        "Requires scope `maintenance:write`. Errors: 400 for an unknown `kind`; 409 if a job of that "
        "kind is already running; 422 for a malformed body."
    ),
)
async def start_job(
    body: StartJobRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("maintenance:write"),
) -> JobOut:
    """Kick off a long-running maintenance job (reindex or a backfill).
    Returns immediately with a `running` job row; poll GET /jobs."""
    admin = principal.user
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
