"""Bulk re-index every file Entry into Meilisearch.

Usage:
    python -m akashic.tools.reindex_search [--batch-size 100]

Re-walks every Entry where kind='file' AND is_deleted=False and rebuilds
its Meili document via build_entry_doc(). Safe to run repeatedly.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from akashic.config import settings
from akashic.models.entry import Entry
from akashic.services.search import build_entry_doc, ensure_index, index_files_batch

logger = logging.getLogger(__name__)


async def _reindex(batch_size: int) -> int:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await ensure_index()
    total = 0
    try:
        async with session_factory() as db:
            offset = 0
            while True:
                rows = (await db.execute(
                    select(Entry)
                    .where(Entry.kind == "file", Entry.is_deleted == False)  # noqa: E712
                    .order_by(Entry.id)
                    .offset(offset)
                    .limit(batch_size)
                )).scalars().all()
                if not rows:
                    break
                docs = [build_entry_doc(e) for e in rows]
                await index_files_batch(docs)
                total += len(rows)
                offset += len(rows)
                logger.info("Re-indexed %d entries so far", total)
    finally:
        await engine.dispose()
    return total


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Bulk re-index every file Entry into Meilisearch.")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    total = asyncio.run(_reindex(args.batch_size))
    print(f"Re-indexed {total} entries.")


if __name__ == "__main__":
    main()
