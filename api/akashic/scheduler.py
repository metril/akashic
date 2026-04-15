"""Background scan scheduler.

Reads `scan_schedule` (cron expression) from each source and triggers scans
at the configured intervals. Runs as part of the FastAPI app lifespan.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from akashic.config import settings
from akashic.models.scan import Scan
from akashic.models.source import Source

logger = logging.getLogger(__name__)

_scheduler_task: asyncio.Task | None = None


async def _check_and_trigger_scans(session_factory: async_sessionmaker):
    """Check all sources with a scan_schedule and trigger any that are due."""
    async with session_factory() as db:
        result = await db.execute(
            select(Source).where(
                Source.scan_schedule.isnot(None),
                Source.status != "scanning",
            )
        )
        sources = result.scalars().all()

        now = datetime.now(timezone.utc)
        for source in sources:
            if not source.scan_schedule:
                continue

            try:
                cron = croniter(source.scan_schedule, source.last_scan_at or datetime(2000, 1, 1, tzinfo=timezone.utc))
                next_run = cron.get_next(datetime)
                if next_run <= now:
                    # Time to scan — create a pending scan record
                    scan = Scan(
                        source_id=source.id,
                        scan_type="incremental",
                        status="pending",
                    )
                    db.add(scan)
                    source.status = "scanning"
                    await db.commit()
                    logger.info(
                        "Scheduled scan triggered for source '%s' (scan_id=%s)",
                        source.name, scan.id,
                    )
            except (ValueError, KeyError) as exc:
                logger.warning("Invalid cron expression for source '%s': %s", source.name, exc)


async def _scheduler_loop():
    """Main scheduler loop — checks every 60 seconds."""
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        while True:
            try:
                await _check_and_trigger_scans(session_factory)
            except Exception as exc:
                logger.error("Scheduler error: %s", exc)
            await asyncio.sleep(60)
    finally:
        await engine.dispose()


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
