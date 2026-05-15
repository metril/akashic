"""Work-unit endpoints for parallel scanning (v0.5.1).

A scan can be split into many ``ScanWorkUnit`` rows — one per
directory subtree the walker chooses to claim independently. Multiple
scanners cooperate on the same scan by leasing different units in
parallel via ``SELECT … FOR UPDATE SKIP LOCKED``.

The walker decides at runtime whether to walk a subdirectory inline
(small / leaf) or split it off as a new pending unit (large /
branchy), so the design adapts to uneven trees without requiring a
static partition up front.

Concurrency cap: ``Source.max_parallel_scanners`` (default 1) bounds
the number of distinct scanners that can hold leases on units of the
same scan simultaneously. Default 1 preserves legacy behaviour
(one scanner walks the entire tree, just via the unit primitive).

Backwards compatibility: scans that don't use the unit machinery work
exactly as today — the unit table is empty for them and the legacy
``/api/scans/{id}/complete`` endpoint still drives the terminal state.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.database import get_db
from akashic.models.scan import Scan
from akashic.models.scan_work_unit import ScanWorkUnit
from akashic.models.scanner import Scanner
from akashic.models.source import Source
from akashic.services.scanner_auth import verify_scanner_jwt

router = APIRouter(tags=["scan_work"])


# Lease window. Same 60 s as the scan-level lease so the watchdog and
# the unit reaper can share one threshold mental model.
_LEASE_SECONDS = 60


# ── Schemas ──────────────────────────────────────────────────────────────


class WorkUnitOut(BaseModel):
    id: uuid.UUID
    scan_id: uuid.UUID
    path: str
    status: str
    lease_expires_at: datetime | None = None


class SplitRequest(BaseModel):
    """Bulk-add child units. Idempotent on (scan_id, path) — safe to
    retry."""

    parent_unit_id: uuid.UUID | None = None
    child_paths: list[str] = Field(default_factory=list)


class SplitResponse(BaseModel):
    created: int
    skipped: int  # rows that already existed (idempotent retry)


class CompleteUnitRequest(BaseModel):
    error_message: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────────


async def _load_scan_and_source(
    db: AsyncSession, scan_id: uuid.UUID,
) -> tuple[Scan, Source | None]:
    scan = (await db.execute(
        select(Scan).where(Scan.id == scan_id)
    )).scalar_one_or_none()
    if scan is None:
        raise HTTPException(status_code=404, detail="scan not found")
    source = None
    if scan.source_id is not None:
        source = (await db.execute(
            select(Source).where(Source.id == scan.source_id)
        )).scalar_one_or_none()
    return scan, source


async def _distinct_active_scanners(
    db: AsyncSession, scan_id: uuid.UUID,
) -> set[uuid.UUID]:
    """Set of scanner ids currently holding a lease on a unit of this
    scan. A unit counts as 'held' when it's running with an unexpired
    lease; expired leases don't count toward the cap."""
    now = datetime.now(timezone.utc)
    rows = (await db.execute(
        select(ScanWorkUnit.assigned_scanner_id).where(
            ScanWorkUnit.scan_id == scan_id,
            ScanWorkUnit.status == "running",
            ScanWorkUnit.assigned_scanner_id.is_not(None),
            ScanWorkUnit.lease_expires_at.is_not(None),
            ScanWorkUnit.lease_expires_at > now,
        )
    )).scalars().all()
    return {r for r in rows if r is not None}


async def _maybe_finalize_scan(
    db: AsyncSession, scan: Scan, source: Source | None,
) -> str | None:
    """Transition the scan to a terminal state when all its units are
    terminal. Returns the new status when transitioned, else None.

    Called from /work/{id}/complete and /work/{id}/fail in the same
    transaction so the side-effects fire atomically with the unit's
    own transition.
    """
    counts = (await db.execute(
        select(ScanWorkUnit.status, func.count())
        .where(ScanWorkUnit.scan_id == scan.id)
        .group_by(ScanWorkUnit.status)
    )).all()
    if not counts:
        return None
    by_status = {s: int(c) for s, c in counts}
    pending = by_status.get("pending", 0) + by_status.get("running", 0)
    if pending > 0:
        return None
    failed = by_status.get("failed", 0)
    completed = by_status.get("completed", 0)
    if failed > 0 and completed == 0:
        new_status = "failed"
    elif failed > 0:
        new_status = "completed"  # mixed: at least some succeeded
    else:
        new_status = "completed"

    now = datetime.now(timezone.utc)
    scan.status = new_status
    scan.completed_at = now
    scan.lease_expires_at = None
    if source is not None:
        if new_status == "completed":
            source.status = "online"
            source.last_scan_at = now
            # v0.28.0 — record one implicit reachability_results row
            # per scanner that successfully completed at least one
            # work unit on this scan. Same intent as the single-
            # scanner /complete path: a scan is the strongest probe
            # we can ever do, so the per-pair panel shows it.
            from akashic.services import reachability_results
            unit_scanners = (await db.execute(
                select(ScanWorkUnit.assigned_scanner_id)
                .where(ScanWorkUnit.scan_id == scan.id)
                .where(ScanWorkUnit.status == "completed")
                .distinct()
            )).scalars().all()
            for sid in unit_scanners:
                if sid is None:
                    continue
                await reachability_results.record_result(
                    db=db, source_id=source.id, scanner_id=sid,
                    ok=True, step=None, error=None,
                    started_at=now, triggered_by=None,
                )
        elif new_status == "failed":
            source.status = "failed"
    return new_status


async def _broadcast_terminal(
    scan: Scan, source: Source, scanner: Scanner, new_status: str,
) -> None:
    from akashic.services import scan_broadcast, scan_pubsub
    await scan_pubsub.publish_source_event({
        "kind": "scan.state",
        "source_id": str(source.id),
        "scan_id": str(scan.id),
        "scan_status": new_status,
        "source_status": source.status,
        "scanner_id": str(scanner.id),
        "scanner_name": scanner.name,
        "scan_type": scan.scan_type,
        "files_found": scan.files_found or 0,
        "current_path": None,
        "started_at": scan.started_at.isoformat() if scan.started_at else None,
    })
    await scan_broadcast.clear_broadcast(str(scan.id))


# ── Endpoints ────────────────────────────────────────────────────────────


@router.post("/api/scans/{scan_id}/work/lease")
async def lease_unit(
    scan_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
    scanner: Scanner = Depends(verify_scanner_jwt),
) -> WorkUnitOut | None:
    """Lease the next pending unit for this scanner.

    Returns 204 (no content) when no work is available — the scanner
    treats that as "this scan is done for me, move on". Refuses with
    409 when leasing would push the distinct-scanner count past the
    source's max_parallel_scanners cap.
    """
    scan, source = await _load_scan_and_source(db, scan_id)
    if scan.status not in {"pending", "running"}:
        # Scan is already terminal — no more work to lease.
        response.status_code = status.HTTP_204_NO_CONTENT
        return None

    cap = (
        source.max_parallel_scanners if source is not None
        else 1
    )
    active = await _distinct_active_scanners(db, scan_id)
    # If the caller already holds a unit, leasing another one of theirs
    # doesn't expand the active set — no cap check needed for them. If
    # they're new, allow only if there's headroom.
    if scanner.id not in active and len(active) >= cap:
        raise HTTPException(
            status_code=409,
            detail=(
                f"max_parallel_scanners cap ({cap}) reached for scan "
                f"{scan_id}; try again after a holder finishes a unit."
            ),
        )

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=_LEASE_SECONDS)

    # Atomic claim: grab the oldest pending (or expired-running) unit
    # and flip it to running with our scanner_id. SKIP LOCKED so two
    # concurrent leases serialise without blocking.
    sql = text(
        """
        WITH cte AS (
            SELECT id FROM scan_work_units
            WHERE scan_id = :scan_id
              AND (
                status = 'pending'
                OR (status = 'running' AND lease_expires_at < :now)
              )
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE scan_work_units u
        SET status = 'running',
            assigned_scanner_id = :scanner_id,
            lease_expires_at = :expires_at,
            started_at = COALESCE(u.started_at, :now)
        FROM cte
        WHERE u.id = cte.id
        RETURNING u.id, u.path
        """
    )
    row = (await db.execute(sql, {
        "scan_id": scan_id,
        "now": now,
        "expires_at": expires_at,
        "scanner_id": scanner.id,
    })).first()

    if row is None:
        # No pending or expired-running rows. If the scan itself is
        # already in a terminal state via _maybe_finalize_scan elsewhere,
        # the caller will see it on next lease. For now: 204.
        await db.commit()
        response.status_code = status.HTTP_204_NO_CONTENT
        return None

    # First unit-claim of the scan: bump the scan to 'running' so the
    # legacy /api/scans/{id}/complete contract (only the scan's own
    # lease holder may terminate) doesn't apply to unit-driven scans.
    if scan.status == "pending":
        scan.status = "running"
        if scan.started_at is None:
            scan.started_at = now
    await db.commit()
    return WorkUnitOut(
        id=row[0], scan_id=scan_id, path=row[1],
        status="running", lease_expires_at=expires_at,
    )


@router.post(
    "/api/scans/{scan_id}/work/{unit_id}/heartbeat",
    response_model=WorkUnitOut,
)
async def heartbeat_unit(
    scan_id: uuid.UUID,
    unit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    scanner: Scanner = Depends(verify_scanner_jwt),
) -> WorkUnitOut:
    unit = (await db.execute(
        select(ScanWorkUnit).where(
            ScanWorkUnit.id == unit_id,
            ScanWorkUnit.scan_id == scan_id,
        )
    )).scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="work unit not found")
    if unit.assigned_scanner_id != scanner.id:
        raise HTTPException(
            status_code=403, detail="scanner is not the lease holder",
        )
    if unit.status != "running":
        raise HTTPException(
            status_code=409,
            detail=f"unit is in terminal state {unit.status!r}; cannot heartbeat",
        )
    now = datetime.now(timezone.utc)
    unit.lease_expires_at = now + timedelta(seconds=_LEASE_SECONDS)
    await db.commit()
    await db.refresh(unit)
    return WorkUnitOut(
        id=unit.id, scan_id=unit.scan_id, path=unit.path,
        status=unit.status, lease_expires_at=unit.lease_expires_at,
    )


@router.post(
    "/api/scans/{scan_id}/work/{unit_id}/complete",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def complete_unit(
    scan_id: uuid.UUID,
    unit_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    scanner: Scanner = Depends(verify_scanner_jwt),
):
    """Mark a unit completed. If it's the last terminal-pending unit,
    transition the scan itself to 'completed' atomically and fire the
    same side-effects as the legacy /api/scans/{id}/complete handler
    (source.status = online, last_scan_at, is_reachable, broadcast).

    On scan finalization (review notable) also enqueue the same
    post-scan background tasks the ingest-batch IsFinal=true path
    enqueues: subtree rollup, snapshot write, scan webhooks. Pre-fix
    these only ran on the legacy ingest path; unit-coordinated scans
    finalized correctly but skipped the post-scan rollup.
    """
    scan, source = await _load_scan_and_source(db, scan_id)
    unit = (await db.execute(
        select(ScanWorkUnit).where(
            ScanWorkUnit.id == unit_id,
            ScanWorkUnit.scan_id == scan_id,
        )
    )).scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="work unit not found")
    if unit.assigned_scanner_id != scanner.id:
        raise HTTPException(
            status_code=403, detail="scanner is not the lease holder",
        )
    now = datetime.now(timezone.utc)
    unit.status = "completed"
    unit.completed_at = now
    unit.lease_expires_at = None

    new_status = await _maybe_finalize_scan(db, scan, source)
    await db.commit()
    if new_status is not None and source is not None:
        await _broadcast_terminal(scan, source, scanner, new_status)
        # Mirror the ingest-batch IsFinal=true post-scan tasks.
        from akashic.config import settings as _settings
        from akashic.routers.ingest import (
            _dispatch_scan_webhooks,
            _rollup_subtree_aggregates,
            _write_scan_snapshot,
        )
        background_tasks.add_task(
            _rollup_subtree_aggregates, str(source.id), _settings.database_url,
        )
        background_tasks.add_task(
            _write_scan_snapshot, str(scan.id), str(source.id),
            _settings.database_url,
        )
        background_tasks.add_task(
            _dispatch_scan_webhooks, str(scan.id), str(source.id),
            new_status, _settings.database_url,
        )


@router.post(
    "/api/scans/{scan_id}/work/{unit_id}/fail",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def fail_unit(
    scan_id: uuid.UUID,
    unit_id: uuid.UUID,
    body: CompleteUnitRequest,
    db: AsyncSession = Depends(get_db),
    scanner: Scanner = Depends(verify_scanner_jwt),
):
    scan, source = await _load_scan_and_source(db, scan_id)
    unit = (await db.execute(
        select(ScanWorkUnit).where(
            ScanWorkUnit.id == unit_id,
            ScanWorkUnit.scan_id == scan_id,
        )
    )).scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="work unit not found")
    if unit.assigned_scanner_id != scanner.id:
        raise HTTPException(
            status_code=403, detail="scanner is not the lease holder",
        )
    now = datetime.now(timezone.utc)
    unit.status = "failed"
    unit.completed_at = now
    unit.lease_expires_at = None
    if body.error_message is not None:
        unit.error_message = body.error_message

    new_status = await _maybe_finalize_scan(db, scan, source)
    await db.commit()
    if new_status is not None and source is not None:
        await _broadcast_terminal(scan, source, scanner, new_status)


@router.post(
    "/api/scans/{scan_id}/work/split",
    response_model=SplitResponse,
)
async def split_units(
    scan_id: uuid.UUID,
    body: SplitRequest,
    db: AsyncSession = Depends(get_db),
    scanner: Scanner = Depends(verify_scanner_jwt),
) -> SplitResponse:
    """Bulk-add child units. Idempotent on (scan_id, path) so retries
    after a partial failure don't double-enqueue.

    Used by the walker to push newly-discovered subdirectories back to
    the queue so siblings can pick them up. Also used by the very first
    scanner on a fresh scan to enqueue the root unit (parent_unit_id
    None, child_paths=[""]).
    """
    scan = (await db.execute(
        select(Scan).where(Scan.id == scan_id)
    )).scalar_one_or_none()
    if scan is None:
        raise HTTPException(status_code=404, detail="scan not found")
    if scan.status not in {"pending", "running"}:
        raise HTTPException(
            status_code=409,
            detail=f"scan is in terminal state {scan.status!r}; cannot split",
        )

    # Per-path SAVEPOINT (review notable). Pre-fix a unique-violation
    # rolled back the entire transaction (and everything flushed
    # earlier this loop), then required a re-fetch + repeat. Now: one
    # savepoint per path, conflict only rolls back THIS path, the
    # already-flushed siblings stay valid.
    created = 0
    skipped = 0
    for path in body.child_paths:
        unit = ScanWorkUnit(
            scan_id=scan_id,
            path=path,
            parent_unit_id=body.parent_unit_id,
            status="pending",
        )
        try:
            async with db.begin_nested():
                db.add(unit)
                await db.flush()
            created += 1
        except IntegrityError:
            skipped += 1
    await db.commit()

    # v0.29.0 — push-based multi-scanner cooperation. Once units exist
    # on the queue, notify every other eligible scanner so they can
    # join via /api/scans/{id}/work/lease without having to first
    # discover the scan via /api/scans/lease (which filters out scans
    # that already have an assigned_scanner_id).
    #
    # Only fired when the source allows >1 cooperating scanner; the
    # notify helper short-circuits on max_parallel_scanners <= 1.
    # Fire on every split, not just the root one — late-arriving
    # subdirectory splits should still wake idle joiners. The
    # per-scanner queue is durable and LTRIM-capped, so repeated
    # notifications for the same scan don't accumulate.
    source = None
    if scan.source_id is not None:
        source = (await db.execute(
            select(Source).where(Source.id == scan.source_id)
        )).scalar_one_or_none()
    if source is not None and source.max_parallel_scanners > 1:
        from akashic.services import scan_join
        try:
            await scan_join.notify_eligible_joiners(
                db=db, scan=scan, source=source,
                exclude_scanner_id=scanner.id,
            )
        except Exception as exc:  # noqa: BLE001
            # Notification is a best-effort wake-up; failure must not
            # block the split. The holder keeps going alone; joiners
            # will eventually pick up via the next split's notify.
            import logging
            logging.getLogger(__name__).warning(
                "split_units: notify_eligible_joiners failed scan=%s: %s",
                scan_id, exc,
            )

    return SplitResponse(created=created, skipped=skipped)
