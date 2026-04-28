"""Background scan scheduler.

Reads `scan_schedule` (cron expression) from each source and triggers scans
at the configured intervals. Runs as part of the FastAPI app lifespan.
Uses the application's existing DB session factory (no duplicate engine).
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from croniter import croniter
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.config import settings
from akashic.database import async_session
from akashic.models.scan import Scan
from akashic.models.source import Source

logger = logging.getLogger(__name__)

# Source.status values: "offline" (default), "scanning", "online", "failed".
# Scan.status values:   "pending", "running", "completed", "failed".

_scheduler_task: asyncio.Task | None = None
_retention_task: asyncio.Task | None = None


async def _try_trigger_source(db: AsyncSession, source: Source, now: datetime):
    """Attempt to trigger a scan for a single source. Uses conditional UPDATE to avoid races."""
    try:
        cron = croniter(source.scan_schedule, source.last_scan_at or datetime(2000, 1, 1, tzinfo=timezone.utc))
        next_run = cron.get_next(datetime)
        if next_run > now:
            return  # Not due yet
    except (ValueError, KeyError) as exc:
        logger.warning("Invalid cron expression for source '%s': %s", source.name, exc)
        return

    # Atomic conditional update to prevent race conditions:
    # Only set status='scanning' if it's still not 'scanning'
    result = await db.execute(
        update(Source)
        .where(Source.id == source.id, Source.status != "scanning")
        .values(status="scanning")
        .returning(Source.id)
    )
    updated = result.first()
    if not updated:
        return  # Another tick already claimed this source

    scan = Scan(
        source_id=source.id,
        scan_type="incremental",
        status="pending",
    )
    db.add(scan)
    await db.commit()
    logger.info("Scheduled scan triggered for source '%s' (scan_id=%s)", source.name, scan.id)


async def _check_and_trigger_scans():
    """Check all sources with a scan_schedule and trigger any that are due."""
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        result = await db.execute(
            select(Source).where(
                Source.scan_schedule.isnot(None),
                Source.status != "scanning",
            )
        )
        sources = result.scalars().all()

    # Process each source in its own session to isolate failures
    for source in sources:
        if not source.scan_schedule:
            continue
        try:
            async with async_session() as db:
                await _try_trigger_source(db, source, now)
        except Exception as exc:
            logger.error("Scheduler error for source '%s': %s", source.name, exc)


async def _check_stale_scans():
    """Mark scans/sources stuck in pending|running|scanning past the threshold as failed.

    Two paths:
    1. Active scans (started_at set, status pending|running) older than the threshold.
    2. Sources stuck in `scanning` whose last_scan_at is older than the threshold —
       this catches orphaned pending scans (started_at NULL) and any other case where
       the source state never returned to online/offline.
    """
    threshold_minutes = settings.stale_scan_threshold_minutes
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)
    message = f"Watchdog: exceeded {threshold_minutes} min"

    async with async_session() as db:
        # 1) Active scans past the threshold
        result = await db.execute(
            select(Scan).where(
                Scan.status.in_(["pending", "running"]),
                Scan.started_at.isnot(None),
                Scan.started_at < cutoff,
            )
        )
        for scan in result.scalars().all():
            scan.status = "failed"
            scan.error_message = message
            await db.execute(
                update(Source)
                .where(Source.id == scan.source_id, Source.status == "scanning")
                .values(status="failed")
            )
            logger.warning(
                "Watchdog: marked scan %s and source %s as failed (active-scan path)",
                scan.id,
                scan.source_id,
            )

        # 2) Sources stuck in scanning (covers orphan pending scans / lost workers)
        result = await db.execute(
            select(Source).where(
                Source.status == "scanning",
                Source.last_scan_at.isnot(None),
                Source.last_scan_at < cutoff,
            )
        )
        for source in result.scalars().all():
            source.status = "failed"
            await db.execute(
                update(Scan)
                .where(
                    Scan.source_id == source.id,
                    Scan.status.in_(["pending", "running"]),
                )
                .values(status="failed", error_message=message)
            )
            logger.warning(
                "Watchdog: source %s stuck in scanning since %s — reset to failed",
                source.id,
                source.last_scan_at,
            )

        await db.commit()


async def _scheduler_loop():
    """Main scheduler loop — checks every 60 seconds."""
    while True:
        try:
            await _check_stale_scans()
        except Exception as exc:
            logger.error("Stale-scan watchdog error: %s", exc)
        try:
            await _check_and_trigger_scans()
        except Exception as exc:
            logger.error("Scheduler loop error: %s", exc)
        await asyncio.sleep(60)


async def _audit_retention_loop():
    """Daily: delete audit events older than `settings.audit_retention_days`.
    No-op when the setting is 0."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import delete
    from akashic.config import settings
    from akashic.database import async_session
    from akashic.models.audit_event import AuditEvent

    while True:
        try:
            if settings.audit_retention_days > 0:
                cutoff = datetime.now(timezone.utc) - timedelta(days=settings.audit_retention_days)
                async with async_session() as db:
                    result = await db.execute(
                        delete(AuditEvent).where(AuditEvent.occurred_at < cutoff)
                    )
                    await db.commit()
                    if result.rowcount:
                        logger.info("Pruned %d audit events older than %s", result.rowcount, cutoff)
        except Exception as exc:  # noqa: BLE001
            logger.warning("audit retention pass failed: %s", exc)
        await asyncio.sleep(24 * 3600)  # daily


def start_scheduler():
    """Start the background scheduler tasks."""
    global _scheduler_task, _retention_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_scheduler_loop())
        logger.info("Scan scheduler started")
    if _retention_task is None or _retention_task.done():
        _retention_task = asyncio.create_task(_audit_retention_loop())
        logger.info("Audit retention scheduler started")


def stop_scheduler():
    """Stop the background scheduler tasks."""
    global _scheduler_task, _retention_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
    if _retention_task and not _retention_task.done():
        _retention_task.cancel()
