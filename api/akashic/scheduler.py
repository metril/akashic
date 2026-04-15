"""Background scan scheduler.

Reads `scan_schedule` (cron expression) from each source and triggers scans
at the configured intervals. Runs as part of the FastAPI app lifespan.
Uses the application's existing DB session factory (no duplicate engine).
"""
import asyncio
import logging
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.database import async_session
from akashic.models.scan import Scan
from akashic.models.source import Source

logger = logging.getLogger(__name__)

_scheduler_task: asyncio.Task | None = None


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


async def _scheduler_loop():
    """Main scheduler loop — checks every 60 seconds."""
    while True:
        try:
            await _check_and_trigger_scans()
        except Exception as exc:
            logger.error("Scheduler loop error: %s", exc)
        await asyncio.sleep(60)


def start_scheduler():
    """Start the background scheduler task."""
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_scheduler_loop())
        logger.info("Scan scheduler started")


def stop_scheduler():
    """Stop the background scheduler task."""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        logger.info("Scan scheduler stopped")
