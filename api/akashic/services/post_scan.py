"""Post-scan background work and mirror-link background relinks."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from akashic.auth.scopes import roles_with_scope
from akashic.services.mirror_links import relink_after_scan, relink_and_flush

logger = logging.getLogger(__name__)

# Cached background-task engines, keyed by db_url. Each background
# task used to spin up a fresh AsyncEngine and dispose it on completion;
# under sustained ingest that meant churning through dozens of short-
# lived connections. Cache here so tasks share one pool per URL.
# Keyed by URL (not module-globally) so tests pointing at a different
# database get their own engine and don't bleed into the production
# pool — `akashic.database` already manages the latter for request-
# path code.
_bg_session_makers: dict[str, "async_sessionmaker[AsyncSession]"] = {}


def bg_session(db_url: str) -> "async_sessionmaker[AsyncSession]":
    """Return a process-cached async_sessionmaker for the given URL.

    Background tasks call this instead of building their own engine so
    a busy ingest doesn't create + dispose dozens of engines.
    """
    sm = _bg_session_makers.get(db_url)
    if sm is None:
        from sqlalchemy.ext.asyncio import (
            async_sessionmaker as _asm,
            create_async_engine,
        )
        engine = create_async_engine(db_url, pool_size=2, max_overflow=2)
        sm = _asm(engine, class_=AsyncSession, expire_on_commit=False)
        _bg_session_makers[db_url] = sm
    return sm


async def dispose_bg_engines() -> None:
    """Dispose cached background-task engines (app shutdown)."""
    sms = list(_bg_session_makers.values())
    _bg_session_makers.clear()
    for sm in sms:
        await sm.kw["bind"].dispose()


async def _rollup_subtree_aggregates(source_id: str, db_url: str):
    """Background task: refresh subtree_size / file_count / dir_count
    on every directory in the source after an ingest's final batch
    has settled. Cheap on incremental scans (most aggregates didn't
    change), bounded by directory count on full scans. Failures are
    logged but don't propagate — the StorageExplorer treemap will
    just look stale until the next scan."""
    from akashic.services.subtree_rollup import rollup_source, rollup_top_children

    try:
        async with bg_session(db_url)() as db:
            # Phase B safety net: only fill in NULL rows. Connectors
            # that emit subtree totals at scan time (Local from Phase B
            # onward) win; the rollup back-fills directories the
            # connector couldn't compute (S3 streaming, legacy data).
            await rollup_source(db, uuid.UUID(source_id), null_only=True)
            # v0.4.11 Phase 8 — pre-compute top-K children per directory
            # so the storage tree read path can iteratively expand from
            # JSONB instead of running a recursive CTE. Must run AFTER
            # rollup_source because top_children needs the freshly-
            # computed subtree_size_bytes to rank correctly.
            await rollup_top_children(db, uuid.UUID(source_id))
            await db.commit()
    except Exception as exc:
        logger.warning(
            "subtree rollup failed for source_id=%s: %s", source_id, exc,
        )


async def _write_scan_snapshot(scan_id: str, source_id: str, db_url: str):
    """Background task: write a scan_snapshot row at scan completion.

    Done in the background (not in the ingest transaction) because the
    aggregates run a few SQL passes over the entries table, which we
    don't want blocking the final batch's response. Failure is logged
    but does not propagate — a missing snapshot row degrades analytics
    charts, but must not fail the scan."""
    from akashic.services.snapshot_writer import write_snapshot

    try:
        async with bg_session(db_url)() as db:
            await write_snapshot(db, uuid.UUID(source_id), uuid.UUID(scan_id))
            await db.commit()
        logger.info("scan_snapshot written for scan_id=%s source_id=%s", scan_id, source_id)
    except Exception as exc:
        logger.warning(
            "scan_snapshot write failed for scan_id=%s source_id=%s: %s",
            scan_id, source_id, exc,
        )


async def _dispatch_scan_webhooks(scan_id: str, source_id: str, status: str, db_url: str):
    """Background task: dispatch webhooks for scan events.

    Scoped to webhook owners whose role may manage scans (`scans:write`).
    """
    from akashic.models.user import User
    from akashic.models.webhook import Webhook
    from akashic.services.webhooks import dispatch_webhook

    event_type = f"scan.{status}"
    try:
        async with bg_session(db_url)() as db:
            admin_ids_result = await db.execute(
                select(User.id).where(User.role.in_(roles_with_scope("scans:write")))
            )
            admin_ids = [row[0] for row in admin_ids_result.all()]

            allowed_user_ids = admin_ids
            if not allowed_user_ids:
                return

            result = await db.execute(
                select(Webhook).where(
                    Webhook.event_type == event_type,
                    Webhook.enabled == True,  # noqa: E712
                    Webhook.user_id.in_(allowed_user_ids),
                )
            )
            from akashic.services.webhooks import dispatch_webhooks
            await dispatch_webhooks(result.scalars().all(), {
                "event": event_type,
                "scan_id": scan_id,
                "source_id": source_id,
            })
    except Exception as exc:
        logger.warning("Webhook dispatch failed: %s", exc)


async def _relink_mirror_links(source_id: str, db_url: str) -> None:
    try:
        async with bg_session(db_url)() as db:
            await relink_after_scan(db, uuid.UUID(source_id))
    except Exception:  # noqa: BLE001
        logger.exception("mirror relink failed for source_id=%s", source_id)


async def run_post_scan_tasks(scan_id: str, source_id: str, status: str, db_url: str) -> None:
    """Rollup, then mirror relink (completed scans), snapshot, webhooks.

    Order matters: the snapshot must see fresh aggregates, and linked
    entries must be settled before webhooks fire.
    """
    await _rollup_subtree_aggregates(source_id, db_url)
    if status == "completed":
        await _relink_mirror_links(source_id, db_url)
    await _write_scan_snapshot(scan_id, source_id, db_url)
    await _dispatch_scan_webhooks(scan_id, source_id, status, db_url)


async def relink_source_bg(backing_id: str, db_url: str) -> None:
    """Relink one backing after its mirror_of mapping changed (including cleared)."""
    try:
        async with bg_session(db_url)() as db:
            await relink_and_flush(db, uuid.UUID(backing_id))
    except Exception:  # noqa: BLE001
        logger.exception("mirror relink failed for backing_id=%s", backing_id)
