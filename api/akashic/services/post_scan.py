"""Post-scan background work and mirror-link background relinks."""
from __future__ import annotations

import logging
import uuid

from akashic.routers.ingest import (
    _bg_session,
    _dispatch_scan_webhooks,
    _rollup_subtree_aggregates,
    _write_scan_snapshot,
)
from akashic.services.mirror_links import relink_after_scan, relink_and_flush

logger = logging.getLogger(__name__)


async def _relink_mirror_links(source_id: str, db_url: str) -> None:
    try:
        async with _bg_session(db_url)() as db:
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
        async with _bg_session(db_url)() as db:
            await relink_and_flush(db, uuid.UUID(backing_id))
    except Exception:  # noqa: BLE001
        logger.exception("mirror relink failed for backing_id=%s", backing_id)
