"""Phase 1 — live scan progress and log endpoints.

The scanner POSTs heartbeats and log/stderr lines on these channels while a
scan is running. Each writer authenticates as the same user that triggered
the scan (same model as `/api/ingest/batch` — there's no separate
scanner-only token). Every persistence path also publishes to Redis so the
WS endpoint can fan out to connected browsers.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import (
    check_source_access,
    get_current_user,
    get_ingest_scanner_id,
    get_ingest_user,
)
from akashic.database import get_db
from akashic.models.scan import Scan
from akashic.models.scan_log_entry import ScanLogEntry
from akashic.models.user import User
from akashic.schemas.scan import (
    HeartbeatIn,
    LogBatchIn,
    LogEntryOut,
    StderrBatchIn,
)
from akashic.services import scan_broadcast, scan_pubsub

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scans", tags=["scan-progress"])


async def _load_scan_with_write(
    scan_id: uuid.UUID,
    user: User,
    db: AsyncSession,
) -> Scan:
    """Resolve the Scan and confirm the caller can write to its source.

    Heartbeat / log / stderr POSTs all need write because they're updating
    scan state — the same level the ingest endpoint requires."""
    scan = (await db.execute(select(Scan).where(Scan.id == scan_id))).scalar_one_or_none()
    if scan is None:
        raise HTTPException(status_code=404, detail="scan not found")
    await check_source_access(scan.source_id, user, db, required_level="write")
    return scan


@router.post("/{scan_id}/heartbeat", status_code=204)
async def post_heartbeat(
    scan_id: uuid.UUID,
    body: HeartbeatIn,
    db: AsyncSession = Depends(get_db),
    # Scanner agent presents the ingest-audience JWT minted at lease
    # time (audience=akashic-ingest). v0.24.0 scoped /api/ingest/batch
    # to that audience but the sibling scan-progress endpoints
    # (heartbeat, log, stderr) were missed and silently 401'd every
    # call from the scanner. v0.27.1 fix: accept ingest-audience JWTs.
    user: User = Depends(get_ingest_user),
) -> None:
    scan = await _load_scan_with_write(scan_id, user, db)

    # Cancellation signal: if the scan is in a terminal state, tell the
    # scanner to stop with HTTP 409. The scanner's heartbeat poster
    # treats 409 as "exit cleanly" — the scan record stays terminal,
    # the source.status was already flipped to online by /cancel, and
    # any in-flight batches arriving after this point also get refused.
    #
    # v0.29.8 — include the cancellation reason in the response body
    # so the scanner can log accurately rather than always saying
    # "scan cancelled by user". Reason values: "user", "watchdog",
    # "completed", "failed:<cause>", or NULL (legacy rows) which the
    # scanner treats as "user" for backwards compatibility.
    if scan.status in {"cancelled", "completed", "failed"}:
        raise HTTPException(
            status_code=409,
            detail={
                "status": scan.status,
                "reason": scan.cancellation_reason,
                "message": f"scan is {scan.status}",
            },
        )

    now = datetime.now(timezone.utc)

    if body.current_path is not None:
        scan.current_path = body.current_path
    if body.phase is not None:
        scan.phase = body.phase
    if body.total_estimated is not None:
        scan.total_estimated = body.total_estimated
    if body.current_batch_size is not None:
        scan.current_batch_size = body.current_batch_size
    scan.bytes_scanned_so_far = body.bytes_scanned
    scan.files_skipped = body.files_skipped
    scan.dirs_walked = body.dirs_walked
    scan.dirs_queued = body.dirs_queued
    # `files_found` accumulates from batch ingest as the source of truth for
    # totals on completion. Heartbeats expose `files_scanned` separately so
    # we don't double-count vs. the batch path. The UI reads
    # `bytes_scanned_so_far` + `dirs_walked`/`dirs_queued` for in-flight
    # state and `files_found`/`files_new`/`files_changed` for completed
    # scans.
    scan.last_heartbeat_at = now
    # v0.29.10 — renew the scan lease on every heartbeat. The lease has a
    # 60 s expiry set at claim time and nothing ever extended it, so
    # *every* scan running longer than ~60 s had an expired lease while
    # still heartbeating once a second. `_requeue_orphan_leases` then
    # reset the healthy scan to `pending` with no assignee, a second
    # scanner re-leased it, and the terminal-status race surfaced to the
    # user as a phantom "cancelled by api". A heartbeat IS the liveness
    # signal, so it must extend the lease.
    from akashic.routers.scanners import _LEASE_DURATION_SECONDS
    scan.lease_expires_at = now + timedelta(seconds=_LEASE_DURATION_SECONDS)
    if scan.status == "pending":
        # First heartbeat marks the scan as running even if no batch has
        # arrived yet (e.g., during the prewalk phase, no batches at all).
        scan.status = "running"
        if scan.started_at is None:
            scan.started_at = now

    await db.commit()

    await scan_pubsub.publish(
        scan_id,
        {
            "kind": "progress",
            "scan_id": str(scan_id),
            "current_path": scan.current_path,
            "files_scanned": body.files_scanned,
            "bytes_scanned": body.bytes_scanned,
            "files_skipped": body.files_skipped,
            "dirs_walked": body.dirs_walked,
            "dirs_queued": body.dirs_queued,
            "total_estimated": scan.total_estimated,
            "phase": scan.phase,
            "ts": now.isoformat(),
            # v0.29.2 — surface the AIMD batch size through to the
            # Live Log header so operators can see whether the
            # adaptive batcher has converged on a high or low value.
            "current_batch_size": scan.current_batch_size,
        },
    )

    # v0.4.11: change-detected broadcast. v0.4.7 fired this on every
    # heartbeat (functionally a 1 Hz polling channel into the SourceCard
    # WS); now we only publish when the user-visible state has actually
    # crossed an adaptive threshold. The 1 Hz scanner heartbeat is still
    # required for cancellation detection + watchdog, but heartbeats
    # without meaningful change persist silently.
    # v0.29.2 — overlay Redis counters on the scan row so the WS
    # broadcast reflects live ingest progress during the scan. Pre-fix
    # this read scan.files_found directly, which now stays at 0 until
    # terminal flush — so the dashboard's "files found" count froze at
    # zero throughout a running scan.
    from akashic.services import scan_counters
    live_counters = await scan_counters.overlay(scan)
    files_found = live_counters["files_found"]
    if await scan_broadcast.should_broadcast(
        str(scan_id),
        phase=scan.phase,
        status=scan.status,
        files_found=files_found,
        total_estimated=scan.total_estimated,
    ):
        scanner_name = await scan_broadcast.resolve_scanner_name(
            db, scan.assigned_scanner_id,
        )
        await scan_pubsub.publish_source_event({
            "kind": "scan.state",
            "source_id": str(scan.source_id),
            "scan_id": str(scan_id),
            "scan_status": scan.status,
            "source_status": "scanning",
            "scanner_id": (
                str(scan.assigned_scanner_id) if scan.assigned_scanner_id else None
            ),
            "scanner_name": scanner_name,
            "scan_type": scan.scan_type,
            "files_found": files_found,
            "current_path": scan.current_path,
            # v0.4.10 — carry started_at so the frontend's bySource map
            # can order scans correctly without losing the tiebreak in
            # recomputeBySource against older terminal scans whose
            # started_at was populated.
            "started_at": scan.started_at.isoformat() if scan.started_at else None,
            # v0.29.2 — latest AIMD batch size reported by the scanner.
            # Surfaced in the Live Log row tooltip so operators can see
            # whether the adaptive batcher has converged on a high or
            # low value for the current source.
            "current_batch_size": scan.current_batch_size,
        })
        await scan_broadcast.record_broadcast(
            str(scan_id),
            phase=scan.phase,
            status=scan.status,
            files_found=files_found,
            total_estimated=scan.total_estimated,
        )


def _now_or(ts: datetime) -> datetime:
    """Coerce naive datetimes into UTC. The scanner always sends UTC, but
    older clients (and the test harness) sometimes drop the tz."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


async def _persist_lines(
    scan: Scan,
    rows: list[tuple[datetime, str, str]],
    db: AsyncSession,
    scanner_id: uuid.UUID | None = None,
) -> list[ScanLogEntry]:
    """Insert log rows and return the persisted models. Single COMMIT keeps
    the round-trip latency from dominating the 500 ms scanner debounce
    window — the scanner already coalesces; we shouldn't re-fragment.

    v0.28.2 — `scanner_id` is the claim baked into the ingest JWT at
    lease time. When set it's persisted on every row so the Live Log
    panel can attribute lines to the right scanner; None for tokens
    predating the claim.

    v0.30.2 — resolve the scanner's display name once and snapshot it
    onto every row. A log line is immutable history; storing the name
    here means the reopen/backfill path serves it directly instead of
    re-deriving it with a JOIN that silently dropped the name."""
    scanner_name = await _scanner_name(db, scanner_id)
    objs = [
        ScanLogEntry(
            scan_id=scan.id, scanner_id=scanner_id, scanner_name=scanner_name,
            ts=_now_or(ts), level=level, message=message,
        )
        for (ts, level, message) in rows
    ]
    db.add_all(objs)
    await db.commit()
    for obj in objs:
        await db.refresh(obj)
    return objs


async def _scanner_name(
    db: AsyncSession, scanner_id: uuid.UUID | None,
) -> str | None:
    """Single lookup per batch so each fanned-out line in the WS
    payload carries a human-readable scanner pill without the
    frontend needing a separate cache. None for legacy/no-attribution
    rows. Delegates to the shared resolver so the scanner-name lookup
    lives in exactly one place (v0.30.1)."""
    return await scan_broadcast.resolve_scanner_name(db, scanner_id)


@router.post("/{scan_id}/log", status_code=204)
async def post_log_batch(
    scan_id: uuid.UUID,
    body: LogBatchIn,
    db: AsyncSession = Depends(get_db),
    # Ingest-audience JWT — see post_heartbeat for the rationale.
    user: User = Depends(get_ingest_user),
    scanner_id: uuid.UUID | None = Depends(get_ingest_scanner_id),
) -> None:
    if not body.lines:
        return
    scan = await _load_scan_with_write(scan_id, user, db)
    rows = [(line.ts, line.level, line.message) for line in body.lines]
    saved = await _persist_lines(scan, rows, db, scanner_id=scanner_id)
    await scan_pubsub.publish(
        scan_id,
        {
            "kind": "log",
            "scan_id": str(scan_id),
            "lines": [
                {
                    "id": str(s.id),
                    "ts": s.ts.isoformat(),
                    "level": s.level,
                    "message": s.message,
                    "scanner_id": (
                        str(s.scanner_id) if s.scanner_id else None
                    ),
                    "scanner_name": s.scanner_name,
                }
                for s in saved
            ],
        },
    )


@router.post("/{scan_id}/stderr", status_code=204)
async def post_stderr_batch(
    scan_id: uuid.UUID,
    body: StderrBatchIn,
    db: AsyncSession = Depends(get_db),
    # Ingest-audience JWT — see post_heartbeat for the rationale.
    user: User = Depends(get_ingest_user),
    scanner_id: uuid.UUID | None = Depends(get_ingest_scanner_id),
) -> None:
    if not body.chunks:
        return
    scan = await _load_scan_with_write(scan_id, user, db)
    rows = [(c.ts, "stderr", c.chunk) for c in body.chunks]
    saved = await _persist_lines(scan, rows, db, scanner_id=scanner_id)
    await scan_pubsub.publish(
        scan_id,
        {
            "kind": "stderr",
            "scan_id": str(scan_id),
            "lines": [
                {
                    "id": str(s.id),
                    "ts": s.ts.isoformat(),
                    "level": s.level,
                    "message": s.message,
                    "scanner_id": (
                        str(s.scanner_id) if s.scanner_id else None
                    ),
                    "scanner_name": s.scanner_name,
                }
                for s in saved
            ],
        },
    )


@router.get("/{scan_id}/log", response_model=list[LogEntryOut])
async def get_log(
    scan_id: uuid.UUID,
    since: datetime | None = Query(None, description="Return entries strictly after this timestamp"),
    kind: str = Query("all", pattern="^(structured|stderr|all)$"),
    limit: int = Query(500, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[LogEntryOut]:
    """Backfill / catch-up endpoint. The WS path streams new events live;
    GET handles the gap on reconnect (`since=<last_ts>`) and the initial
    drawer mount before WS is ready.

    v0.30.2 — scanner_name is read straight off the row (snapshotted at
    write time by _persist_lines), so the backfill renders the per-row
    scanner pill without a read-time JOIN that could drop the name."""
    scan = (await db.execute(select(Scan).where(Scan.id == scan_id))).scalar_one_or_none()
    if scan is None:
        raise HTTPException(status_code=404, detail="scan not found")
    await check_source_access(scan.source_id, user, db, required_level="read")

    stmt = select(ScanLogEntry).where(ScanLogEntry.scan_id == scan_id)
    if since is not None:
        stmt = stmt.where(ScanLogEntry.ts > _now_or(since))
    if kind == "structured":
        stmt = stmt.where(ScanLogEntry.level != "stderr")
    elif kind == "stderr":
        stmt = stmt.where(ScanLogEntry.level == "stderr")
    stmt = stmt.order_by(ScanLogEntry.ts).limit(limit)

    rows = (await db.execute(stmt)).scalars().all()
    return [
        LogEntryOut(
            id=entry.id,
            ts=entry.ts,
            level=entry.level,
            message=entry.message,
            scanner_id=entry.scanner_id,
            scanner_name=entry.scanner_name,
        )
        for entry in rows
    ]
