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
_log_cleanup_task: asyncio.Task | None = None
_snapshot_task: asyncio.Task | None = None

# Phase 1 — log entries are kept for 7 days after the parent scan completes.
# Long-running scans never expire while in flight; only completed/failed
# scans contribute to the cleanup pass.
_LOG_RETENTION_DAYS = 7


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

    from akashic.services.scan_factory import previous_files_for_source
    prev = await previous_files_for_source(source.id, db)
    scan = Scan(
        source_id=source.id,
        scan_type="incremental",
        status="pending",
        previous_scan_files=prev,
        # Phase 2 multi-scanner: snapshot pool tag so the lease query
        # routes this scan to a matching scanner.
        pool=source.preferred_pool,
    )
    db.add(scan)
    await db.commit()
    logger.info(
        "Scheduled scan enqueued for source '%s' (scan_id=%s, pool=%s)",
        source.name, scan.id, source.preferred_pool or "<any>",
    )


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


async def _requeue_orphan_leases():
    """Phase-2 multi-scanner watchdog. Resets in-flight leases whose
    `lease_expires_at` has passed back to `pending` so another scanner
    can claim them — but only when there's at least one online scanner
    in the right pool to pick the work up. If no scanner is online,
    leave the row untouched and let `_check_stale_scans` fail it after
    the longer threshold instead.

    Online-ness uses the same window as routers/scanners.py
    (last_seen_at within ONLINE_WINDOW_SECONDS = 90s).
    """
    from akashic.routers.scanners import ONLINE_WINDOW_SECONDS

    now = datetime.now(timezone.utc)
    online_cutoff = now - timedelta(seconds=ONLINE_WINDOW_SECONDS)

    async with async_session() as db:
        # Pull all expired leases. Each row's `pool` decides whether
        # we re-queue or wait for the failure path.
        result = await db.execute(
            select(Scan).where(
                Scan.status == "running",
                Scan.lease_expires_at.isnot(None),
                Scan.lease_expires_at < now,
            )
        )
        expired = list(result.scalars().all())
        if not expired:
            return

        # One query for all distinct pools at risk → set of pools that
        # currently have an online scanner. Permissive null-pool scans
        # are eligible whenever ANY pool has an online scanner.
        from sqlalchemy import distinct
        from akashic.models.scanner import Scanner

        any_online = (await db.execute(
            select(Scanner.id).where(
                Scanner.enabled.is_(True),
                Scanner.last_seen_at.isnot(None),
                Scanner.last_seen_at >= online_cutoff,
            ).limit(1)
        )).scalar_one_or_none() is not None

        online_pools_rows = (await db.execute(
            select(distinct(Scanner.pool)).where(
                Scanner.enabled.is_(True),
                Scanner.last_seen_at.isnot(None),
                Scanner.last_seen_at >= online_cutoff,
            )
        )).all()
        online_pools = {row[0] for row in online_pools_rows}

        for scan in expired:
            can_requeue = (
                # Permissive null pool needs *any* online scanner.
                (scan.pool is None and any_online)
                # Pool-tagged scan needs an online scanner in that pool.
                or (scan.pool is not None and scan.pool in online_pools)
            )
            if can_requeue:
                scan.status = "pending"
                scan.assigned_scanner_id = None
                scan.lease_expires_at = None
                logger.info(
                    "Watchdog: re-queued orphan lease scan_id=%s (pool=%s)",
                    scan.id, scan.pool,
                )
            # else: leave it; _check_stale_scans will fail it after
            # the kill threshold.

        await db.commit()


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

    # Quick pass: re-queue any expired-but-recoverable lease before
    # the slow kill-cutoff path runs. Recoverable means there's an
    # online scanner that could pick it up.
    try:
        await _requeue_orphan_leases()
    except Exception as exc:
        logger.warning("orphan lease re-queue failed: %s", exc)

    async with async_session() as db:
        # 1) Active scans past the threshold. Prefer the heartbeat timestamp
        # when present (Phase 1 onwards) — a scan that's actively
        # heartbeating shouldn't be killed even if started_at is old (e.g.,
        # a multi-hour scan that's still progressing). Fall back to
        # started_at for legacy / pre-heartbeat scans.
        from sqlalchemy import or_, and_
        result = await db.execute(
            select(Scan).where(
                Scan.status.in_(["pending", "running"]),
                Scan.started_at.isnot(None),
                or_(
                    and_(Scan.last_heartbeat_at.is_(None), Scan.started_at < cutoff),
                    and_(Scan.last_heartbeat_at.isnot(None), Scan.last_heartbeat_at < cutoff),
                ),
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


async def _scan_log_cleanup_loop():
    """Hourly: drop scan_log_entries whose parent scan completed >7 days ago.

    Scoping by parent-scan completion time (rather than the row's own ts)
    means a long-running scan keeps its full log history; we only sweep
    scans the user is no longer actively investigating."""
    from sqlalchemy import delete
    from akashic.models.scan_log_entry import ScanLogEntry

    while True:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=_LOG_RETENTION_DAYS)
            async with async_session() as db:
                # Subquery: scan IDs whose terminal completion is past the
                # cutoff. Failed scans count too — they won't be re-opened
                # for inspection after a week.
                stale_scan_ids = (
                    select(Scan.id)
                    .where(
                        Scan.status.in_(["completed", "failed"]),
                        Scan.completed_at.isnot(None),
                        Scan.completed_at < cutoff,
                    )
                ).subquery()
                result = await db.execute(
                    delete(ScanLogEntry).where(ScanLogEntry.scan_id.in_(select(stale_scan_ids)))
                )
                await db.commit()
                if result.rowcount:
                    logger.info("Pruned %d scan_log_entries older than %s", result.rowcount, cutoff)
        except Exception as exc:  # noqa: BLE001
            logger.warning("scan log cleanup pass failed: %s", exc)
        await asyncio.sleep(3600)  # hourly


async def _snapshot_fallback_loop():
    """Daily: write a scan_snapshot for any source whose latest snapshot is
    older than 24 hours.

    Per Phase 1 plan: snapshots are normally written at scan-completion
    (in ingest.py). For sources scanned weekly or less, this fallback
    ensures the growth chart still has daily resolution. Sources that
    have never been scanned successfully (no rows in the entries table
    for them) are skipped — there's nothing to aggregate.
    """
    from sqlalchemy import func as _func

    from akashic.models.scan_snapshot import ScanSnapshot
    from akashic.services.snapshot_writer import write_snapshot

    while True:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            async with async_session() as db:
                # For each source, find the latest snapshot's taken_at.
                # If older than cutoff (or NULL = never), enqueue a fresh
                # snapshot. Done as one query so we don't fan out N reads.
                latest = (
                    select(
                        ScanSnapshot.source_id,
                        _func.max(ScanSnapshot.taken_at).label("latest_at"),
                    )
                    .group_by(ScanSnapshot.source_id)
                    .subquery()
                )
                stale_rows = (
                    await db.execute(
                        select(Source.id)
                        .outerjoin(latest, latest.c.source_id == Source.id)
                        .where(
                            (latest.c.latest_at.is_(None)) | (latest.c.latest_at < cutoff),
                        )
                    )
                ).all()
                stale_ids = [r.id for r in stale_rows]

            for source_id in stale_ids:
                try:
                    async with async_session() as db:
                        await write_snapshot(db, source_id)
                        await db.commit()
                    logger.info("Nightly snapshot written for source %s", source_id)
                except Exception as exc:
                    logger.warning(
                        "Nightly snapshot for source %s failed: %s", source_id, exc,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("snapshot fallback pass failed: %s", exc)
        await asyncio.sleep(24 * 3600)  # daily


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
    global _scheduler_task, _retention_task, _log_cleanup_task, _snapshot_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_scheduler_loop())
        logger.info("Scan scheduler started")
    if _retention_task is None or _retention_task.done():
        _retention_task = asyncio.create_task(_audit_retention_loop())
        logger.info("Audit retention scheduler started")
    if _log_cleanup_task is None or _log_cleanup_task.done():
        _log_cleanup_task = asyncio.create_task(_scan_log_cleanup_loop())
        logger.info("Scan-log cleanup scheduler started")
    if _snapshot_task is None or _snapshot_task.done():
        _snapshot_task = asyncio.create_task(_snapshot_fallback_loop())
        logger.info("Snapshot fallback scheduler started")


def stop_scheduler():
    """Stop the background scheduler tasks."""
    global _scheduler_task, _retention_task, _log_cleanup_task, _snapshot_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
    if _retention_task and not _retention_task.done():
        _retention_task.cancel()
    if _log_cleanup_task and not _log_cleanup_task.done():
        _log_cleanup_task.cancel()
    if _snapshot_task and not _snapshot_task.done():
        _snapshot_task.cancel()
