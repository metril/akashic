"""Backfill `entries.viewable_by_*` for legacy rows.

Usage:
    python -m akashic.tools.backfill_viewable [--batch-size 500]
                                              [--checkpoint /tmp/backfill.ckpt]
                                              [--source-id <uuid>]

Walks every Entry where any of the three viewable_by_* columns is NULL
and recomputes the denormalized ACL projection from acl/mode/uid/gid
via the same `compute_viewable_buckets` funnel that ingest uses.

Resumable: writes the last-processed entry id to a checkpoint file after
each batch. Re-running with the same checkpoint resumes where it left
off — a crash mid-backfill doesn't restart the run from zero. Pass
--source-id to constrain the run to a single source for staged rollouts.

Idempotent: a row that's already populated is skipped by the WHERE
predicate, but re-running on a populated row re-derives the same value
from the same inputs anyway.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from akashic.config import settings
from akashic.models.entry import Entry
from akashic.services.ingest import compute_viewable_buckets

logger = logging.getLogger(__name__)


def _read_checkpoint(path: Path) -> uuid.UUID | None:
    if not path.exists():
        return None
    raw = path.read_text().strip()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        logger.warning("Invalid checkpoint contents at %s; ignoring", path)
        return None


def _write_checkpoint(path: Path, last_id: uuid.UUID) -> None:
    # Atomic write: tmp + rename so a crash mid-write can't corrupt the
    # checkpoint file (otherwise a partial write could resume from a
    # garbled UUID and the run would either skip work or restart).
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(str(last_id))
    tmp.replace(path)


async def _backfill(
    batch_size: int,
    checkpoint_path: Path,
    source_id: uuid.UUID | None,
) -> int:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    total = 0
    last_id = _read_checkpoint(checkpoint_path)
    if last_id:
        logger.info("Resuming from checkpoint %s", last_id)
    try:
        async with session_factory() as db:
            while True:
                # Page on `id > last_id` rather than offset so we don't pay
                # the ever-growing OFFSET cost on a huge table, and so a
                # row inserted partway through doesn't shift our cursor.
                stmt = select(Entry).where(
                    or_(
                        Entry.viewable_by_read.is_(None),
                        Entry.viewable_by_write.is_(None),
                        Entry.viewable_by_delete.is_(None),
                    )
                )
                if source_id is not None:
                    stmt = stmt.where(Entry.source_id == source_id)
                if last_id is not None:
                    stmt = stmt.where(Entry.id > last_id)
                stmt = stmt.order_by(Entry.id).limit(batch_size)

                rows = (await db.execute(stmt)).scalars().all()
                if not rows:
                    break

                for e in rows:
                    buckets = compute_viewable_buckets(e.acl, e.mode, e.uid, e.gid)
                    e.viewable_by_read = buckets["read"]
                    e.viewable_by_write = buckets["write"]
                    e.viewable_by_delete = buckets["delete"]

                await db.commit()
                total += len(rows)
                last_id = rows[-1].id
                _write_checkpoint(checkpoint_path, last_id)
                logger.info(
                    "Backfilled %d entries (total %d), last id %s",
                    len(rows), total, last_id,
                )
    finally:
        await engine.dispose()
    return total


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Backfill entries.viewable_by_* columns from acl/mode/uid/gid.",
    )
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path("/tmp/akashic-backfill-viewable.ckpt"),
        help="Path to checkpoint file (resumes on re-run if present).",
    )
    parser.add_argument(
        "--source-id",
        type=uuid.UUID,
        default=None,
        help="Constrain backfill to a single source (for staged rollouts).",
    )
    args = parser.parse_args()
    total = asyncio.run(_backfill(args.batch_size, args.checkpoint, args.source_id))
    print(f"Backfilled {total} entries.")


if __name__ == "__main__":
    main()
