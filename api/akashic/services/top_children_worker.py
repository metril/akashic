"""Streaming worker for incremental top_children maintenance.

v0.4.11 Phase 8e — behind the AKASHIC_STREAMING_TOPCHILDREN
(`streaming_topchildren=True`) feature flag.

Off by default: the post-scan rollup (`rollup_top_children` called
from `_rollup_subtree_aggregates` background task) is enough for
most installs. When on:

  - Ingest batches push touched parent paths into a Redis set per
    source: `topchld:dirty:{source_id}`.
  - A single asyncio task wakes every WORKER_TICK seconds, drains
    the union of dirty sets across all sources, and recomputes
    `top_children` for those parents via
    `rollup_top_children_for_paths`.
  - When all dirty sets are empty for IDLE_EXIT seconds, the worker
    keeps running but does nothing — the polling cost is one Redis
    SCAN per tick.

The worker uses Redis SADD/SMEMBERS/DEL to coordinate across
multiple api processes (one process drains, the others see an
empty set). No locking needed: SMEMBERS+DEL is racy but the worst
case is double-recomputation of a parent path, which is idempotent.

Lifecycle: started by main.py at app startup IF the feature flag
is on. Stopped via the cancellation token on app shutdown.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from akashic.config import settings
from akashic.services.subtree_rollup import rollup_top_children_for_paths

logger = logging.getLogger(__name__)


_KEY_PREFIX = "topchld:dirty:"
WORKER_TICK_S = 1.0


def _key(source_id: str) -> str:
    return f"{_KEY_PREFIX}{source_id}"


_redis: Redis | None = None


def _client() -> Redis:
    """Lazy Redis client; reuses settings.redis_url."""
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def mark_dirty(source_id: str | uuid.UUID, parent_paths: list[str]) -> None:
    """Mark these parent paths as dirty for the given source. Called
    from ingest after a batch commits, with the set of distinct
    parent paths the batch touched.

    Best-effort against Redis: failures are logged but don't propagate
    so a Redis outage doesn't break ingest. Worst case: top_children
    becomes stale until the post-scan rollup catches up.
    """
    if not parent_paths:
        return
    if not settings.streaming_topchildren:
        return
    try:
        await _client().sadd(_key(str(source_id)), *parent_paths)
    except Exception as exc:  # noqa: BLE001
        logger.warning("top_children_worker mark_dirty failed: %s", exc)


async def _drain_one_source(
    source_id_str: str, db: AsyncSession,
) -> int:
    """Pop all dirty paths for one source and recompute top_children
    for them. Returns the number of directories updated."""
    r = _client()
    # SMEMBERS + DEL is racy across processes (another worker could
    # be reading the same set), but recompute is idempotent so the
    # worst case is a tiny bit of extra work. Avoids the lock that
    # SPOP-loop would force.
    paths_raw = await r.smembers(_key(source_id_str))
    if not paths_raw:
        return 0
    await r.delete(_key(source_id_str))
    paths = list(paths_raw)
    try:
        source_uuid = uuid.UUID(source_id_str)
    except ValueError:
        logger.warning(
            "top_children_worker bad source_id key: %s", source_id_str,
        )
        return 0
    updated = await rollup_top_children_for_paths(db, source_uuid, paths)
    await db.commit()
    return updated


async def _drain_all() -> None:
    """One sweep: find every source with a dirty set, drain each.

    Uses SCAN_ITER (non-blocking) to discover keys; one batch per
    source so a single very-active source can't starve others.
    """
    r = _client()
    # Collect source IDs first so the iteration doesn't hold open a
    # SCAN cursor while we're doing DB work.
    source_ids: list[str] = []
    async for key in r.scan_iter(match=f"{_KEY_PREFIX}*", count=100):
        source_ids.append(key[len(_KEY_PREFIX):])
    if not source_ids:
        return

    engine = create_async_engine(settings.database_url)
    session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session() as db:
            for sid in source_ids:
                try:
                    n = await _drain_one_source(sid, db)
                    if n:
                        logger.debug(
                            "top_children_worker: source=%s updated %d dirs",
                            sid, n,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "top_children_worker drain failed source=%s: %s",
                        sid, exc,
                    )
    finally:
        await engine.dispose()


_worker_task: asyncio.Task[None] | None = None
_shutdown = asyncio.Event()


async def _worker_loop() -> None:
    """Periodic drain. Wake every WORKER_TICK_S; do nothing if no
    sources have dirty sets. Exits cleanly on _shutdown event."""
    logger.info(
        "top_children_worker started (tick=%.1fs, flag=%s)",
        WORKER_TICK_S, settings.streaming_topchildren,
    )
    while not _shutdown.is_set():
        try:
            await _drain_all()
        except Exception as exc:  # noqa: BLE001
            logger.exception("top_children_worker tick failed: %s", exc)
        try:
            await asyncio.wait_for(
                _shutdown.wait(), timeout=WORKER_TICK_S,
            )
        except asyncio.TimeoutError:
            pass
    logger.info("top_children_worker stopped")


def start_worker() -> None:
    """Start the periodic worker if the feature flag is on. Idempotent
    — safe to call multiple times. Called from app startup."""
    global _worker_task
    if not settings.streaming_topchildren:
        logger.debug(
            "top_children_worker not started (flag streaming_topchildren=False)",
        )
        return
    if _worker_task is not None and not _worker_task.done():
        return
    _shutdown.clear()
    _worker_task = asyncio.create_task(_worker_loop())


async def stop_worker() -> None:
    """Signal shutdown and await the worker. Called from app shutdown."""
    global _worker_task
    _shutdown.set()
    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("top_children_worker stop timed out")
            _worker_task.cancel()
        _worker_task = None
