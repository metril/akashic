"""Roll-up of attached-source reachability into the parent ``Host``.

A direct host probe (``POST /api/hosts/{id}/test-connection``)
writes ``is_reachable`` plus the two timestamps. A roll-up driven
by source-side probes writes only ``is_reachable``, leaving the
timestamps tied to the most recent direct host probe — that way the
UI tooltip can disambiguate "host probe at 12:34" from "share
roll-up of probes at 12:00, 12:10, 12:30".

Roll-up rule:
  - True   if any attached source has ``is_reachable=True``
  - False  if ALL attached sources are explicitly False AND at
           least one source exists
  - None   otherwise (no sources attached, or no checks yet)
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.host import Host
from akashic.models.source import Source


async def recompute_host_reachability(
    db: AsyncSession, host_id: uuid.UUID
) -> Optional[bool]:
    """Recompute and persist ``Host.is_reachable`` from the attached
    sources' state. Returns the new value (True/False/None).

    Caller is responsible for committing the surrounding transaction.
    """
    host = (await db.execute(
        select(Host).where(Host.id == host_id)
    )).scalar_one_or_none()
    if host is None:
        return None

    rows = (await db.execute(
        select(Source.is_reachable).where(Source.host_id == host_id)
    )).all()

    if not rows:
        # No sources attached: leave whatever the direct probe wrote.
        return host.is_reachable

    statuses = [r[0] for r in rows]
    if any(s is True for s in statuses):
        rolled = True
    elif all(s is False for s in statuses):
        rolled = False
    else:
        # Mix of False + None means "we don't know yet" — leave None.
        rolled = None

    if host.is_reachable != rolled:
        host.is_reachable = rolled
    return rolled
