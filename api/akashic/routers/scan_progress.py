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
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import check_source_access, get_current_user
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
from akashic.services import scan_pubsub

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
    user: User = Depends(get_current_user),
) -> None:
    scan = await _load_scan_with_write(scan_id, user, db)

    # Cancellation signal: if a user marked this scan as cancelled, tell
    # the scanner to stop with HTTP 409. The scanner's heartbeat poster
    # treats 409 as "exit cleanly" — the scan record stays cancelled,
    # the source.status was already flipped to online by /cancel, and
    # any in-flight batches arriving after this point also get refused.
    if scan.status in {"cancelled", "completed", "failed"}:
        raise HTTPException(status_code=409, detail=f"scan is {scan.status}")

    now = datetime.now(timezone.utc)

    if body.current_path is not None:
        scan.current_path = body.current_path
    if body.phase is not None:
        scan.phase = body.phase
    if body.total_estimated is not None:
        scan.total_estimated = body.total_estimated
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
        },
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
) -> list[ScanLogEntry]:
    """Insert log rows and return the persisted models. Single COMMIT keeps
    the round-trip latency from dominating the 500 ms scanner debounce
    window — the scanner already coalesces; we shouldn't re-fragment."""
    objs = [
        ScanLogEntry(scan_id=scan.id, ts=_now_or(ts), level=level, message=message)
        for (ts, level, message) in rows
    ]
    db.add_all(objs)
    await db.commit()
    for obj in objs:
        await db.refresh(obj)
    return objs


@router.post("/{scan_id}/log", status_code=204)
async def post_log_batch(
    scan_id: uuid.UUID,
    body: LogBatchIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    if not body.lines:
        return
    scan = await _load_scan_with_write(scan_id, user, db)
    rows = [(line.ts, line.level, line.message) for line in body.lines]
    saved = await _persist_lines(scan, rows, db)
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
    user: User = Depends(get_current_user),
) -> None:
    if not body.chunks:
        return
    scan = await _load_scan_with_write(scan_id, user, db)
    rows = [(c.ts, "stderr", c.chunk) for c in body.chunks]
    saved = await _persist_lines(scan, rows, db)
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
) -> list[ScanLogEntry]:
    """Backfill / catch-up endpoint. The WS path streams new events live;
    GET handles the gap on reconnect (`since=<last_ts>`) and the initial
    drawer mount before WS is ready."""
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
    return list(rows)
