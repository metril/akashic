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
``/api/v1/scans/{id}/complete`` endpoint still drives the terminal state.
"""
from __future__ import annotations

from typing import Annotated
import uuid
from datetime import datetime, timedelta, timezone

from akashic.schemas.ws_events import WsScanState
from akashic.routing import ApiRouter
from fastapi import BackgroundTasks, Depends, HTTPException, Path, Response, status
from akashic.schemas.base import ApiModel
from pydantic import Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.database import get_db
from akashic.openapi import SCANNER_TAG
from akashic.models.scan import Scan
from akashic.models.scan_work_unit import ScanWorkUnit
from akashic.models.scanner import Scanner
from akashic.models.source import Source
from akashic.services.scanner_auth import verify_scanner_jwt
from akashic.services.source_config import effective_max_parallel_scanners

router = ApiRouter(tags=[SCANNER_TAG])


# Lease window. Same 60 s as the scan-level lease so the watchdog and
# the unit reaper can share one threshold mental model.
_LEASE_SECONDS = 60


# ── Schemas ──────────────────────────────────────────────────────────────


class WorkUnitOut(ApiModel):
    id: uuid.UUID = Field(description="Work unit id.")
    scan_id: uuid.UUID = Field(description="Scan the unit belongs to.")
    path: str = Field(description="Directory subtree to walk, relative to the source root; empty for the root.")
    status: str = Field(description="Unit status; `running` while leased.")
    lease_expires_at: datetime | None = Field(default=None, description="When the lease lapses unless renewed with a heartbeat.")


class SplitRequest(ApiModel):
    """Bulk-add child units. Idempotent on (scan_id, path) — safe to
    retry."""

    parent_unit_id: uuid.UUID | None = Field(default=None, description="Unit the paths were split from; null when enqueueing the root.")
    child_paths: list[str] = Field(default_factory=list, description="Directory paths to add as pending units.")

    model_config = {"json_schema_extra": {'examples': [{'parent_unit_id': None, 'child_paths': ['', 'projects', 'archive/2023']}]}}


class SplitResponse(ApiModel):
    created: int = Field(description="Units created.")
    skipped: int = Field(description="Paths that already existed (idempotent retry).")


class CompleteUnitRequest(ApiModel):
    error_message: str | None = Field(default=None, description="Why the unit failed.")
    # v0.34.0 — when true, /work/{id}/fail requeues the unit (status back
    # to `pending`) for retry instead of marking it permanently `failed`.
    # The scanner sets it on a transient SMB stall so a flaky share
    # doesn't permanently drop the unit's subtree from the index.
    requeue: bool = Field(default=False, description="Return the unit to the queue for another attempt (up to 3 attempts) instead of failing it.")

    model_config = {"json_schema_extra": {'examples': [{'error_message': 'connection reset', 'requeue': True}]}}


# A unit failed-with-requeue this many times falls back to a permanent
# `failed` — so a genuinely-unreachable subtree can't requeue forever and
# the scan can still finalize.
_MAX_UNIT_ATTEMPTS = 3


# ── Helpers ──────────────────────────────────────────────────────────────


async def _load_scan_and_source(
    db: AsyncSession, scan_id: uuid.UUID, *, lock_scan: bool = False,
) -> tuple[Scan, Source | None]:
    """Resolve the Scan + its Source.

    `lock_scan=True` takes a `SELECT … FOR UPDATE` row lock on the scan —
    used by /work/{id}/complete|fail so concurrent unit completions for one
    scan serialize. Without it, two sibling scanners finishing their last
    units near-simultaneously each run `_maybe_finalize_scan`'s status count
    under READ COMMITTED, each misses the other's not-yet-committed unit,
    both see pending>0, and the scan never finalizes via /complete (v0.33.0).
    """
    scan_stmt = select(Scan).where(Scan.id == scan_id)
    if lock_scan:
        scan_stmt = scan_stmt.with_for_update()
    scan = (await db.execute(scan_stmt)).scalar_one_or_none()
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
    swept_ids: list[str] | None = None,
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
    # v0.31.4 — record why the scan ended. The legacy single-scanner
    # /api/v1/scans/{id}/complete endpoint sets cancellation_reason; this
    # multi-scanner finalize path didn't, leaving it NULL. A scanner
    # still heartbeating after finalize then gets a 409 with reason=null,
    # which the Go decoder maps to "scan cancelled by user" — a false
    # cancellation warning in the Live Log. Set it to match the status
    # ("completed" / "failed") so the 409 carries an accurate reason.
    scan.cancellation_reason = new_status
    scan.completed_at = now
    scan.lease_expires_at = None

    # v0.31.5 — reap entries the scan didn't see, here at true
    # completion. The ingest `is_final` path defers to this for
    # unit-coordinated scans (a per-unit is_final batch must not sweep —
    # it would delete the subtrees other units haven't walked yet).
    # Only on a clean, fully-walked scan: `failed > 0` means a unit's
    # subtree was never covered, so its entries are not stale and must
    # not be swept. Runs before the counter flush so the files_deleted
    # delta lands on the row.
    if new_status == "completed" and failed == 0:
        from akashic.routers.ingest import _sweep_stale_entries
        swept = await _sweep_stale_entries(db, scan, now)
        if swept_ids is not None:
            swept_ids.extend(swept)

    # v0.29.2 — flush per-scan Redis counter hash back to scan.*
    # before terminal commit. Single round-trip; safe to call even
    # when the scan never had a hash (no-op).
    from akashic.services import scan_counters
    await scan_counters.flush_to_db(db, scan)
    # And drain the Meilisearch debouncer for this source so search
    # is current at terminal time.
    if source is not None:
        from akashic.services import meili_indexer
        try:
            await meili_indexer.flush(source.id)
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).debug(
                "meili flush on _maybe_finalize_scan failed: %s", exc,
            )
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
    await scan_pubsub.publish_source_event(WsScanState(
        source_id=str(source.id),
        scan_id=str(scan.id),
        scan_status=new_status,
        source_status=source.status,
        scanner_id=str(scanner.id),
        scanner_name=scanner.name,
        scan_type=scan.scan_type,
        files_found=scan.files_found or 0,
        current_path=None,
        started_at=scan.started_at.isoformat() if scan.started_at else None,
    ))
    await scan_broadcast.clear_broadcast(str(scan.id))


# ── Endpoints ────────────────────────────────────────────────────────────


@router.post(
    "/scans/{scan_id}/work/lease",
    responses={204: {"description": "No work unit available right now; keep polling."}},
    summary="Lease a work unit",
    description=(
        "Scanner protocol, parallel scans. Atomically claims the next pending work unit (a directory "
        "subtree) of the scan for the calling scanner, or an expired lease left by a dead scanner, "
        "for 60 seconds. Extend it with the unit heartbeat. The first lease moves a `pending` scan to "
        "`running`.\n"
        "Returns 204 when there is no unit to take (the scanner is done with this scan). The number "
        "of distinct scanners working one scan is capped by the source's `max_parallel_scanners`.\n"
        "Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an "
        "Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the "
        "scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock "
        "skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.\n"
        "Errors: 404 if the scan does not exist; 409 if the scan has ended (detail `reason: "
        "scan-terminal`) or the parallel-scanner cap is reached while work remains."
    ),
)
async def lease_unit(
    scan_id: Annotated[uuid.UUID, Path(description="Scan whose work queue to lease from.")],
    response: Response,
    db: AsyncSession = Depends(get_db),
    scanner: Scanner = Depends(verify_scanner_jwt),
) -> WorkUnitOut | None:
    """Lease the next pending unit for this scanner.

    Returns 204 (no content) when no work is available — the scanner
    treats that as "this scan is done for me, move on". Refuses with
    409 *only* when a unit IS available but leasing it would push the
    distinct-scanner count past the source's max_parallel_scanners cap.
    A scanner with simply no work left always gets 204, never 409 —
    "no work" is not "capped".
    """
    scan, source = await _load_scan_and_source(db, scan_id)
    if scan.status not in {"pending", "running"}:
        # v0.34.0 — the scan itself is terminal. Answer 409 (not 204) so
        # the scanner can tell "this scan is done, exit" apart from "no
        # unit available right now, keep polling" (a plain 204). The
        # detail mirrors the heartbeat 409's {status, reason, message}
        # shape so the scanner decodes it the same way.
        raise HTTPException(
            status_code=409,
            detail={
                "status": scan.status,
                "reason": "scan-terminal",
                "message": f"scan is in terminal state {scan.status!r}",
            },
        )

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=_LEASE_SECONDS)

    # Step 1 — lock a claimable unit (oldest pending, or a running unit
    # whose lease expired) WITHOUT mutating it yet. SKIP LOCKED so two
    # concurrent leases pick different rows (or the loser picks none);
    # the row lock is held until this transaction commits/rolls back.
    candidate = (await db.execute(text(
        """
        SELECT id, path FROM scan_work_units
        WHERE scan_id = :scan_id
          AND (
            status = 'pending'
            OR (status = 'running' AND lease_expires_at < :now)
          )
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
        """
    ), {"scan_id": scan_id, "now": now})).first()

    if candidate is None:
        # Nothing claimable — a clean 204. The cap is deliberately NOT
        # consulted here: with no work to hand out, "capped" would be a
        # misleading answer (and made a concurrent re-lease race between
        # 204 and 409, depending on commit ordering).
        await db.commit()
        response.status_code = status.HTTP_204_NO_CONTENT
        return None

    # Step 2 — a unit IS available; only now does the cap apply. A
    # scanner already holding a unit doesn't expand the active set, so
    # it's exempt; a new scanner may claim only if there's headroom.
    cap = effective_max_parallel_scanners(source) if source is not None else 1
    active = await _distinct_active_scanners(db, scan_id)
    if scanner.id not in active and len(active) >= cap:
        # Leave the candidate pending — the rollback triggered by this
        # raised error releases the SKIP LOCKED row lock so another
        # (already-active) scanner can claim it.
        raise HTTPException(
            status_code=409,
            detail=(
                f"max_parallel_scanners cap ({cap}) reached for scan "
                f"{scan_id}; try again after a holder finishes a unit."
            ),
        )

    # Step 3 — claim the unit we locked in step 1.
    unit_id, unit_path = candidate[0], candidate[1]
    await db.execute(text(
        """
        UPDATE scan_work_units
        SET status = 'running',
            assigned_scanner_id = :scanner_id,
            lease_expires_at = :expires_at,
            started_at = COALESCE(started_at, :now)
        WHERE id = :unit_id
        """
    ), {
        "scanner_id": scanner.id,
        "expires_at": expires_at,
        "now": now,
        "unit_id": unit_id,
    })

    # First unit-claim of the scan: bump the scan to 'running' so the
    # legacy /api/v1/scans/{id}/complete contract (only the scan's own
    # lease holder may terminate) doesn't apply to unit-driven scans.
    if scan.status == "pending":
        scan.status = "running"
        if scan.started_at is None:
            scan.started_at = now
    await db.commit()
    return WorkUnitOut(
        id=unit_id, scan_id=scan_id, path=unit_path,
        status="running", lease_expires_at=expires_at,
    )


@router.post(
    "/scans/{scan_id}/work/{unit_id}/heartbeat",
    response_model=WorkUnitOut,
    summary="Extend a work unit lease",
    description=(
        "Scanner protocol, parallel scans. Renews the 60 second lease on a unit the scanner holds. "
        "Send it more often than every 60 seconds while walking the unit.\n"
        "Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an "
        "Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the "
        "scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock "
        "skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.\n"
        "Errors: 403 if the caller does not hold the lease; 404 if the unit does not exist in the "
        "scan; 409 if the unit has already finished."
    ),
)
async def heartbeat_unit(
    scan_id: Annotated[uuid.UUID, Path(description="Scan the unit belongs to.")],
    unit_id: Annotated[uuid.UUID, Path(description="Leased work unit.")],
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
    "/scans/{scan_id}/work/{unit_id}/complete",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Complete a work unit",
    description=(
        "Scanner protocol, parallel scans. Marks a leased unit completed. When it was the last "
        "unfinished unit the scan itself is finalized in the same transaction: stale entries are "
        "swept, the source returns to `online` and records `last_scan_at`, subscribers are notified, "
        "and the subtree roll-up, snapshot and `scan.completed` webhooks run in the background. A "
        "scan with failed units finishes `completed` if any unit succeeded and `failed` otherwise.\n"
        "Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an "
        "Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the "
        "scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock "
        "skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.\n"
        "Errors: 403 if the caller does not hold the lease; 404 if the scan or unit does not exist; "
        "409 if the scan has ended or the unit is not running."
    ),
)
async def complete_unit(
    scan_id: Annotated[uuid.UUID, Path(description="Scan the unit belongs to.")],
    unit_id: Annotated[uuid.UUID, Path(description="Leased work unit.")],
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    scanner: Scanner = Depends(verify_scanner_jwt),
):
    """Mark a unit completed. If it's the last terminal-pending unit,
    transition the scan itself to 'completed' atomically and fire the
    same side-effects as the legacy /api/v1/scans/{id}/complete handler
    (source.status = online, last_scan_at, is_reachable, broadcast).

    On scan finalization (review notable) also enqueue the same
    post-scan background tasks the ingest-batch IsFinal=true path
    enqueues: subtree rollup, snapshot write, scan webhooks. Pre-fix
    these only ran on the legacy ingest path; unit-coordinated scans
    finalized correctly but skipped the post-scan rollup.
    """
    # lock_scan: serialize concurrent unit completions for this scan so
    # the last one's _maybe_finalize_scan sees every sibling unit (v0.33.0).
    scan, source = await _load_scan_and_source(db, scan_id, lock_scan=True)
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
    if scan.status not in ("pending", "running") or unit.status != "running":
        raise HTTPException(
            status_code=409,
            detail=f"scan is {scan.status}, unit is {unit.status}; cannot finalize unit",
        )
    now = datetime.now(timezone.utc)
    unit.status = "completed"
    unit.completed_at = now
    unit.lease_expires_at = None

    swept_ids: list[str] = []
    new_status = await _maybe_finalize_scan(db, scan, source, swept_ids)
    await db.commit()
    from akashic.routers.ingest import _delete_swept_from_index
    await _delete_swept_from_index(swept_ids)
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
    "/scans/{scan_id}/work/{unit_id}/fail",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Fail or requeue a work unit",
    description=(
        "Scanner protocol, parallel scans. Reports that a leased unit could not be finished. With "
        "`requeue` true the unit returns to the queue for another scanner, up to 3 attempts in total; "
        "otherwise (or after the third attempt) it becomes `failed`, which may finalize the scan as "
        "described for unit completion.\n"
        "Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an "
        "Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the "
        "scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock "
        "skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.\n"
        "Errors: 403 if the caller does not hold the lease; 404 if the scan or unit does not exist; "
        "409 if the scan has ended or the unit is not running."
    ),
)
async def fail_unit(
    scan_id: Annotated[uuid.UUID, Path(description="Scan the unit belongs to.")],
    unit_id: Annotated[uuid.UUID, Path(description="Leased work unit.")],
    body: CompleteUnitRequest,
    db: AsyncSession = Depends(get_db),
    scanner: Scanner = Depends(verify_scanner_jwt),
):
    # lock_scan — see complete_unit: serialize the finalize check.
    scan, source = await _load_scan_and_source(db, scan_id, lock_scan=True)
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
    if scan.status not in ("pending", "running") or unit.status != "running":
        raise HTTPException(
            status_code=409,
            detail=f"scan is {scan.status}, unit is {unit.status}; cannot finalize unit",
        )
    now = datetime.now(timezone.utc)
    unit.attempt_count = (unit.attempt_count or 0) + 1
    if body.error_message is not None:
        unit.error_message = body.error_message

    # v0.34.0 — a transient-stall failure (body.requeue) puts the unit
    # back on the queue for retry rather than abandoning its subtree, up
    # to _MAX_UNIT_ATTEMPTS. Beyond that a genuinely-unreachable subtree
    # is failed permanently so the scan can still finalize.
    if body.requeue and unit.attempt_count < _MAX_UNIT_ATTEMPTS:
        unit.status = "pending"
        unit.assigned_scanner_id = None
        unit.lease_expires_at = None
        await db.commit()
        # Wake an idle scanner to take the re-queued unit.
        if source is not None and effective_max_parallel_scanners(source) > 1:
            from akashic.services import scan_join
            try:
                await scan_join.notify_eligible_joiners(
                    db=db, scan=scan, source=source, exclude_scanner_id=None,
                )
            except Exception as exc:  # noqa: BLE001
                import logging
                logging.getLogger(__name__).warning(
                    "fail_unit re-queue notify failed scan=%s: %s", scan.id, exc,
                )
        return

    # Permanent failure — requeue not requested, or the attempt budget
    # is spent.
    unit.status = "failed"
    unit.completed_at = now
    unit.lease_expires_at = None

    new_status = await _maybe_finalize_scan(db, scan, source)
    await db.commit()
    if new_status is not None and source is not None:
        await _broadcast_terminal(scan, source, scanner, new_status)


@router.post(
    "/scans/{scan_id}/work/split",
    response_model=SplitResponse,
    summary="Add work units",
    description=(
        "Scanner protocol, parallel scans. Adds pending units for subdirectories the scanner chooses "
        "not to walk itself, so other scanners can lease them; the first scanner on a fresh scan also "
        "uses it to enqueue the root unit (`parent_unit_id` null, `child_paths` `[\"\"]`). Idempotent "
        "per path: paths that already exist are counted in `skipped`, so retrying is safe. Scanners "
        "that may join this scan are notified.\n"
        "Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an "
        "Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the "
        "scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock "
        "skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.\n"
        "Errors: 404 if the scan does not exist; 409 if the scan has ended."
    ),
)
async def split_units(
    scan_id: Annotated[uuid.UUID, Path(description="Scan to add units to.")],
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
    # join via /api/v1/scans/{id}/work/lease without having to first
    # discover the scan via /api/v1/scans/lease (which filters out scans
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
    if source is not None and effective_max_parallel_scanners(source) > 1:
        from akashic.services import scan_join
        import logging as _logging
        _log = _logging.getLogger(__name__)
        try:
            notified = await scan_join.notify_eligible_joiners(
                db=db, scan=scan, source=source,
                exclude_scanner_id=scanner.id,
            )
            # v0.29.5 — emit one diagnostic line per split call so the
            # user can verify the notify side-effect from
            # `docker compose logs api | grep split_units` on a
            # re-scan. notify_eligible_joiners itself logs the per-
            # scanner detail; this is the call-site summary.
            _log.info(
                "split_units: scan=%s source=%s created=%d skipped=%d "
                "notified=%d eligible scanner(s)",
                scan_id, source.id, created, skipped, notified,
            )
        except Exception as exc:  # noqa: BLE001
            # Notification is a best-effort wake-up; failure must not
            # block the split. The holder keeps going alone; joiners
            # will eventually pick up via the next split's notify.
            _log.warning(
                "split_units: notify_eligible_joiners failed scan=%s: %s",
                scan_id, exc,
            )

    return SplitResponse(created=created, skipped=skipped)
