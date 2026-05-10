"""Read/write helpers for the on-demand `reachability_results` history.

The table is append-only — every probe (inline by the API, by a scanner
agent over long-poll, or implicit from a successful scan) inserts one
row. Consumers read the most recent row per (source, scanner) pair for
the eligibility panels and per-source summary; the history disclosure
in the panel can read the most recent N rows.

`prune_old(per_pair_limit=20)` runs once a day from the scheduler to
bound table size.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.reachability_result import ReachabilityResult


async def record_result(
    *,
    db: AsyncSession,
    source_id: uuid.UUID,
    scanner_id: Optional[uuid.UUID],
    ok: bool,
    step: Optional[str],
    error: Optional[str],
    started_at: datetime,
    triggered_by: Optional[uuid.UUID] = None,
) -> ReachabilityResult:
    """INSERT one probe outcome. Caller commits.

    `scanner_id=None` means an inline probe by the API itself (non-local
    sources can be dialled directly without an agent round-trip).
    `triggered_by=None` means an implicit bump (e.g. successful scan
    completion); set the caller's user id for explicit panel actions
    so the audit trail attributes who asked.
    """
    row = ReachabilityResult(
        source_id=source_id,
        scanner_id=scanner_id,
        ok=ok,
        step=step,
        error=error,
        started_at=started_at,
        completed_at=datetime.now(timezone.utc),
        triggered_by=triggered_by,
    )
    db.add(row)
    return row


async def prune_old(db: AsyncSession, per_pair_limit: int = 20) -> int:
    """Keep the last `per_pair_limit` rows per (source_id, scanner_id)
    pair; delete the rest. Returns the number of deleted rows.

    Single statement for atomicity — a row_number()-windowed CTE picks
    the rows to keep, and we DELETE everything else. NULL scanner_id is
    its own bucket via `IS NOT DISTINCT FROM` semantics in the window
    (postgres treats NULLs as distinct in PARTITION BY by default, so
    each NULL is its own partition — change to `coalesce` if the
    inline-probe history grows unboundedly, but it shouldn't because
    the daily prune bounds it per partition).
    """
    res = await db.execute(
        text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                         PARTITION BY source_id, scanner_id
                         ORDER BY completed_at DESC, id DESC
                       ) AS rn
                  FROM reachability_results
            )
            DELETE FROM reachability_results
             WHERE id IN (SELECT id FROM ranked WHERE rn > :keep)
            """
        ),
        {"keep": per_pair_limit},
    )
    # rowcount on the asyncpg cursor reflects affected rows from the
    # DELETE — used by the prune-tick logger so ops can see the trim
    # actually does work over time.
    return res.rowcount or 0
