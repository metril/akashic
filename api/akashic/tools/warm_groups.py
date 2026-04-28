"""Bulk warm the principal_groups_cache for every binding on a source.

Usage:
    python -m akashic.tools.warm_groups --source-id <UUID>
    python -m akashic.tools.warm_groups   # all sources

Calls resolve_groups for every distinct (source, identity_type, identifier)
that has at least one FsBinding. Writes results into the cache table.
Skips bindings whose source.type doesn't support resolution.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import uuid

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from akashic.config import settings
from akashic.models.fs_person import FsBinding
from akashic.models.principal_groups_cache import PrincipalGroupsCache
from akashic.models.source import Source
from akashic.services.group_resolver import (
    ResolutionFailed, UnsupportedResolution, resolve_groups,
)

logger = logging.getLogger(__name__)


async def _warm(source_id: uuid.UUID | None) -> int:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    n = 0
    try:
        async with session_factory() as db:
            stmt = select(FsBinding, Source).join(Source, FsBinding.source_id == Source.id)
            if source_id is not None:
                stmt = stmt.where(FsBinding.source_id == source_id)
            rows = (await db.execute(stmt)).all()

            for binding, source in rows:
                try:
                    result = await resolve_groups(source, binding)
                except (UnsupportedResolution, ResolutionFailed) as exc:
                    logger.info("skipped %s/%s: %s", source.name, binding.identifier, exc)
                    continue

                cache_stmt = pg_insert(PrincipalGroupsCache).values(
                    source_id=source.id,
                    identity_type=binding.identity_type,
                    identifier=binding.identifier,
                    groups=list(result.groups),
                    resolved_at=result.resolved_at,
                )
                cache_stmt = cache_stmt.on_conflict_do_update(
                    index_elements=[
                        PrincipalGroupsCache.source_id,
                        PrincipalGroupsCache.identity_type,
                        PrincipalGroupsCache.identifier,
                    ],
                    set_={
                        "groups": cache_stmt.excluded.groups,
                        "resolved_at": cache_stmt.excluded.resolved_at,
                    },
                )
                await db.execute(cache_stmt)
                n += 1
            await db.commit()
    finally:
        await engine.dispose()
    return n


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Warm the principal_groups_cache.")
    parser.add_argument("--source-id", type=uuid.UUID, default=None)
    args = parser.parse_args()
    n = asyncio.run(_warm(args.source_id))
    print(f"Warmed {n} bindings.")


if __name__ == "__main__":
    main()
