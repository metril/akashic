"""Backfill `entries.subtree_*` aggregates for legacy data.

Usage:
    python -m akashic.tools.backfill_subtree_sizes [--source-id <uuid>]

Walks every source (or one source if --source-id given), runs the
bottom-up rollup once. Resumable in the trivial sense — re-running
just recomputes deterministically. Per-source progress is logged so
operators can monitor a long backfill.

For new sources scanned after Phase 9 ships, the rollup runs as a
post-scan background task; this tool is the one-shot for upgrades.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from akashic.config import settings
from akashic.models.source import Source
from akashic.services.subtree_rollup import rollup_source

logger = logging.getLogger(__name__)


async def _backfill(source_id: uuid.UUID | None) -> int:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    total_updated = 0
    try:
        async with session_factory() as db:
            if source_id is not None:
                source_ids = [source_id]
            else:
                source_ids = list(
                    (await db.execute(select(Source.id))).scalars().all()
                )
            for sid in source_ids:
                logger.info("Rolling up subtree aggregates for source %s …", sid)
                updated = await rollup_source(db, sid)
                await db.commit()
                total_updated += updated
                logger.info("  done — %d directory rows updated", updated)
    finally:
        await engine.dispose()
    return total_updated


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Backfill entries.subtree_size_bytes / file_count / dir_count.",
    )
    parser.add_argument(
        "--source-id",
        type=uuid.UUID,
        default=None,
        help="Constrain backfill to a single source.",
    )
    args = parser.parse_args()
    total = asyncio.run(_backfill(args.source_id))
    print(f"Updated {total} directory rows across all sources.")


if __name__ == "__main__":
    main()
