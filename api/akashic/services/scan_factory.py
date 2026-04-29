"""Helpers for creating Scan rows with the right baseline metadata.

Centralizes the `previous_scan_files` snapshot so both the scheduler and the
manual trigger endpoint produce comparable rows. Without this snapshot the
UI's ETA falls back to "unknown" until the prewalk phase runs.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.scan import Scan


async def previous_files_for_source(source_id: uuid.UUID, db: AsyncSession) -> int | None:
    """Return the most-recent successful scan's `files_found` for the source,
    or None if no successful scan exists yet.

    Used as the ETA fallback when no prewalk count is available."""
    stmt = (
        select(Scan.files_found)
        .where(Scan.source_id == source_id, Scan.status == "completed")
        .order_by(Scan.completed_at.desc())
        .limit(1)
    )
    val = (await db.execute(stmt)).scalar_one_or_none()
    return val if val and val > 0 else None
