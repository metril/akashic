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

from sqlalchemy import select, text
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
    """Record one probe outcome — dedup'd against the most recent row
    for the same (source, scanner) pair. Caller commits.

    `scanner_id=None` means an inline probe by the API itself (non-local
    sources can be dialled directly without an agent round-trip).
    `triggered_by=None` means an implicit bump (e.g. successful scan
    completion); set the caller's user id for explicit panel actions
    so the audit trail attributes who asked.

    v0.29.0 — dedup write-side: when the most recent row for this
    (source_id, scanner_id) pair has identical (ok, step, error),
    bump its `completed_at` to now and return it instead of inserting
    a new row. Eliminates "click Test 5 times, see 5 identical green
    dots" noise in the AllowedScannersPanel history disclosure and
    keeps the table compact under bulk fan-out (the test-shares
    endpoint can fire many same-outcome probes back-to-back).
    """
    completed_at = datetime.now(timezone.utc)

    latest = (await db.execute(
        select(ReachabilityResult)
        .where(ReachabilityResult.source_id == source_id)
        .where(ReachabilityResult.scanner_id.is_(scanner_id) if scanner_id is None
               else ReachabilityResult.scanner_id == scanner_id)
        .order_by(ReachabilityResult.completed_at.desc(),
                  ReachabilityResult.id.desc())
        .limit(1)
    )).scalar_one_or_none()

    if (
        latest is not None
        and latest.ok == ok
        and (latest.step or None) == (step or None)
        and (latest.error or None) == (error or None)
    ):
        # Same state as last time — just advance the timestamp so the
        # panel still shows "fresh" without growing a duplicate dot.
        latest.completed_at = completed_at
        latest.started_at = started_at
        # Refresh triggered_by when the user explicitly re-triggered; an
        # implicit scan-completion shouldn't overwrite a recorded user
        # action since the action is what gives the row audit value.
        if triggered_by is not None:
            latest.triggered_by = triggered_by
        return latest

    row = ReachabilityResult(
        source_id=source_id,
        scanner_id=scanner_id,
        ok=ok,
        step=step,
        error=error,
        started_at=started_at,
        completed_at=completed_at,
        triggered_by=triggered_by,
    )
    db.add(row)
    return row


async def list_history(
    *,
    db: AsyncSession,
    source_id: uuid.UUID,
    limit_per_scanner: int = 20,
) -> list[tuple[Optional[uuid.UUID], Optional[str], list[ReachabilityResult]]]:
    """v0.41.0 — return the per-scanner reachability history for one
    source, newest outcomes first within each scanner group.

    Returns a list of `(scanner_id, scanner_name, outcomes)` tuples.
    `scanner_id` is None for inline-by-API probes (hostless sources
    probed by the api process itself) and the matching `scanner_name`
    is None.

    Caps at `limit_per_scanner` rows per (source, scanner) pair,
    which dovetails with the daily `prune_old(per_pair_limit=20)`.
    Hot path is the existing
    `(source_id, scanner_id, completed_at DESC)` index, so the read
    is cheap.
    """
    from akashic.models.scanner import Scanner

    rows = (await db.execute(
        select(ReachabilityResult)
        .where(ReachabilityResult.source_id == source_id)
        .order_by(
            ReachabilityResult.scanner_id.asc().nulls_last(),
            ReachabilityResult.completed_at.desc(),
            ReachabilityResult.id.desc(),
        )
    )).scalars().all()

    if not rows:
        return []

    scanner_ids = {r.scanner_id for r in rows if r.scanner_id is not None}
    names: dict[uuid.UUID, str] = {}
    if scanner_ids:
        for sid, name in (await db.execute(
            select(Scanner.id, Scanner.name).where(Scanner.id.in_(scanner_ids))
        )).all():
            names[sid] = name

    # Group + cap per scanner, preserving sort order.
    grouped: list[tuple[Optional[uuid.UUID], Optional[str], list[ReachabilityResult]]] = []
    buckets: dict[Optional[uuid.UUID], list[ReachabilityResult]] = {}
    for r in rows:
        bucket = buckets.setdefault(r.scanner_id, [])
        if len(bucket) < limit_per_scanner:
            bucket.append(r)
    for sid, outcomes in buckets.items():
        grouped.append((sid, names.get(sid) if sid is not None else None, outcomes))
    return grouped


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
