"""Compute and persist a ScanSnapshot row for one source.

Called from scan_runner at scan completion, and from the nightly
scheduler job for sources that haven't scanned recently. Uses SQL
aggregates throughout — no entry-by-entry iteration — so a million-row
source completes in well under a second.

Top-N + `_other` rollup keeps the JSONB columns bounded at a few KB
even for corpora with tens of thousands of unique extensions or
owners. Top-N=50 is a deliberate compromise: enough to render a useful
chart of the head; the long tail rolls into a single bucket so charts
that key off names don't have to fan out.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TypedDict

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.entry import Entry
from akashic.models.scan_snapshot import ScanSnapshot

logger = logging.getLogger(__name__)


TOP_N = 50  # cap for by_extension / by_owner; matches scan_snapshot.py docstring.
HOT_DAYS = 30
WARM_DAYS = 365


class _Bucket(TypedDict):
    n: int
    bytes: int


def _empty() -> _Bucket:
    return {"n": 0, "bytes": 0}


async def _totals(db: AsyncSession, source_id: uuid.UUID) -> tuple[int, int, int]:
    """Single SQL aggregate for file_count, directory_count, total_size."""
    row = (
        await db.execute(
            select(
                func.count().filter(Entry.kind == "file").label("file_count"),
                func.count().filter(Entry.kind == "directory").label("dir_count"),
                func.coalesce(
                    func.sum(case((Entry.kind == "file", Entry.size_bytes), else_=0)),
                    0,
                ).label("total_size"),
            ).where(
                Entry.source_id == source_id,
                Entry.is_deleted.is_(False),
            )
        )
    ).one()
    return int(row.file_count), int(row.dir_count), int(row.total_size or 0)


async def _by_extension(db: AsyncSession, source_id: uuid.UUID) -> dict[str, _Bucket]:
    """Group files by lowercased extension. NULL/empty rolls into `_unknown`."""
    # Define the bucket expression once and reuse — Postgres requires the
    # SELECT and GROUP BY expressions to be textually identical, including
    # bind params. Two `func.coalesce(...)` calls produce different param
    # placeholders even with the same literal, which Postgres rejects.
    ext_expr = func.coalesce(func.lower(Entry.extension), "_unknown")
    rows = (
        await db.execute(
            select(
                ext_expr.label("ext"),
                func.count().label("n"),
                func.coalesce(func.sum(Entry.size_bytes), 0).label("bytes"),
            )
            .where(
                Entry.source_id == source_id,
                Entry.kind == "file",
                Entry.is_deleted.is_(False),
            )
            .group_by(ext_expr)
        )
    ).all()

    sorted_rows = sorted(rows, key=lambda r: int(r.bytes), reverse=True)
    out: dict[str, _Bucket] = {}
    for r in sorted_rows[:TOP_N]:
        out[r.ext] = {"n": int(r.n), "bytes": int(r.bytes)}
    if len(sorted_rows) > TOP_N:
        rest = sorted_rows[TOP_N:]
        out["_other"] = {
            "n": sum(int(r.n) for r in rest),
            "bytes": sum(int(r.bytes) for r in rest),
        }
    return out


async def _by_owner(db: AsyncSession, source_id: uuid.UUID) -> dict[str, _Bucket]:
    """Group files by owner_name (string). NULL rolls into `_unknown`."""
    owner_expr = func.coalesce(Entry.owner_name, "_unknown")
    rows = (
        await db.execute(
            select(
                owner_expr.label("owner"),
                func.count().label("n"),
                func.coalesce(func.sum(Entry.size_bytes), 0).label("bytes"),
            )
            .where(
                Entry.source_id == source_id,
                Entry.kind == "file",
                Entry.is_deleted.is_(False),
            )
            .group_by(owner_expr)
        )
    ).all()

    sorted_rows = sorted(rows, key=lambda r: int(r.bytes), reverse=True)
    out: dict[str, _Bucket] = {}
    for r in sorted_rows[:TOP_N]:
        out[r.owner] = {"n": int(r.n), "bytes": int(r.bytes)}
    if len(sorted_rows) > TOP_N:
        rest = sorted_rows[TOP_N:]
        out["_other"] = {
            "n": sum(int(r.n) for r in rest),
            "bytes": sum(int(r.bytes) for r in rest),
        }
    return out


async def _by_age(
    db: AsyncSession,
    source_id: uuid.UUID,
    now: datetime,
) -> dict[str, _Bucket]:
    """Hot/warm/cold by fs_modified_at. Files with NULL mtime go to `_unknown`."""
    hot_cutoff = now - timedelta(days=HOT_DAYS)
    warm_cutoff = now - timedelta(days=WARM_DAYS)

    bucket = case(
        (Entry.fs_modified_at.is_(None), "_unknown"),
        (Entry.fs_modified_at >= hot_cutoff, "hot"),
        (Entry.fs_modified_at >= warm_cutoff, "warm"),
        else_="cold",
    )

    rows = (
        await db.execute(
            select(
                bucket.label("bucket"),
                func.count().label("n"),
                func.coalesce(func.sum(Entry.size_bytes), 0).label("bytes"),
            )
            .where(
                Entry.source_id == source_id,
                Entry.kind == "file",
                Entry.is_deleted.is_(False),
            )
            .group_by(bucket)
        )
    ).all()

    out: dict[str, _Bucket] = {
        "hot": _empty(), "warm": _empty(), "cold": _empty(), "_unknown": _empty(),
    }
    for r in rows:
        out[r.bucket] = {"n": int(r.n), "bytes": int(r.bytes)}
    return out


async def write_snapshot(
    db: AsyncSession,
    source_id: uuid.UUID,
    scan_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> ScanSnapshot:
    """Compute aggregates over the source's current entries and persist
    one ScanSnapshot row.

    `now` is exposed so tests can pin the time-of-day for hot/warm/cold
    bucket assertions; production callers leave it None (defaults to UTC).
    The caller commits — this function flushes but does not commit so it
    composes with the surrounding transaction (e.g., scan_runner's final
    completion update).
    """
    when = now or datetime.now(timezone.utc)

    file_count, dir_count, total_size = await _totals(db, source_id)
    by_extension = await _by_extension(db, source_id)
    by_owner = await _by_owner(db, source_id)
    by_age = await _by_age(db, source_id, when)

    snap = ScanSnapshot(
        source_id=source_id,
        scan_id=scan_id,
        taken_at=when,
        file_count=file_count,
        directory_count=dir_count,
        total_size_bytes=total_size,
        by_extension=by_extension,
        by_owner=by_owner,
        by_kind_and_age=by_age,
    )
    db.add(snap)
    await db.flush()
    await db.refresh(snap)
    return snap
