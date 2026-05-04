"""Shared writer for reachability probe results.

Both the scanner-report endpoint and the api self-worker funnel
their results through ``apply_reachability_result`` so the
``reachability_checks`` row update, the source state update, and
the host roll-up always happen as one atomic step.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.reachability_check import ReachabilityCheck
from akashic.models.source import Source
from akashic.services.host_reachability import recompute_host_reachability


async def apply_reachability_result(
    db: AsyncSession,
    check_id: uuid.UUID,
    ok: bool,
    step: Optional[str],
    error: Optional[str],
) -> Optional[ReachabilityCheck]:
    """Persist a probe result against the work-item row, the source,
    and the parent host. Returns the updated check row, or None if
    the check id no longer exists (race with delete).

    Caller is responsible for committing the surrounding transaction.
    """
    check = (await db.execute(
        select(ReachabilityCheck).where(ReachabilityCheck.id == check_id)
    )).scalar_one_or_none()
    if check is None:
        return None

    now = datetime.now(timezone.utc)
    check.status = "completed" if ok else "failed"
    check.result_ok = ok
    check.result_step = step
    check.result_error = error
    check.completed_at = now
    check.lease_expires_at = None

    src = (await db.execute(
        select(Source).where(Source.id == check.source_id)
    )).scalar_one_or_none()
    if src is not None:
        src.last_reachability_check_at = now
        src.is_reachable = ok
        if ok:
            src.last_reachable_at = now
        if src.host_id is not None:
            await recompute_host_reachability(db, src.host_id)

    return check
