"""Per-source Meilisearch index debouncer (v0.29.2).

Pre-v0.29.2 every ingest batch enqueued a per-batch
``_index_files_to_meilisearch`` background task. On a fast-throughput
scan that was hundreds of small Meili tasks created back-to-back, each
incurring task-object overhead and serializing internally on Meili's
indexing queue.

This module replaces that with a per-source dirty set in Redis:

  * Each ingest batch calls ``mark_dirty(source_id, entry_ids)`` →
    ``SADD akashic:meili:pending:{source_id} ...``. Constant-time, no
    HTTP round-trip, no task creation overhead.
  * A single ``run_debouncer()`` asyncio task (started from the app
    lifespan) sweeps every 5 seconds. For each source whose dirty set
    is non-empty, the loop calls ``flush(source_id)`` when SCARD >=
    ``_FLUSH_SIZE_THRESHOLD`` (5000) OR when the set is older than
    ``_FLUSH_AGE_SECONDS`` (5 s) since first mark — whichever fires
    first. ``flush`` reads the set, clears it, bulk-fetches the
    entries, and pushes a single Meili batch.
  * **Parallel across sources**: two concurrent scans on different
    sources flush independently. Meili itself runs per-index ops
    sequentially, but task enqueue is parallel and our enqueue layer
    no longer serializes them.
  * **Scan-completion flush**: ``complete_scan`` and
    ``_maybe_finalize_scan`` call ``flush(source_id)`` directly so
    the index is current at terminal time without waiting for the
    5 s debounce window.

v0.30.0 — `flush` uses Meilisearch partial updates (update_documents),
not full add_documents. The scanner extracts file text and posts it
to /api/ingest/content, which partial-updates the doc's content_text.
A metadata flush here that did a full replace would wipe that text;
partial updates from both writers merge cleanly by id.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from akashic.config import settings
from akashic.models.entry import Entry
from akashic.services.scan_pubsub import _client as _redis_client

logger = logging.getLogger(__name__)

# Per-source dirty set + sibling "first-marked-at" timestamp key.
# Set TTL is 1 hour so an abandoned set after a scan crash doesn't
# linger forever (the next mark_dirty re-arms it; flush wipes both).
_SET_TTL_SECONDS = 3600

# Trigger thresholds for the debouncer.
_FLUSH_SIZE_THRESHOLD = 5000
_FLUSH_AGE_SECONDS = 5

# Debouncer tick cadence. Must be <= _FLUSH_AGE_SECONDS so the age
# check actually fires on time.
_DEBOUNCE_TICK_SECONDS = 1


def _set_key(source_id: uuid.UUID | str) -> str:
    return f"akashic:meili:pending:{source_id}"


def _ts_key(source_id: uuid.UUID | str) -> str:
    return f"akashic:meili:pending_since:{source_id}"


# Lazy background-session maker, mirrors the pattern in routers/ingest.py
# so we don't depend on the request-scope session inside the debouncer
# (which runs in the lifespan, not a request).
_bg_engine = None
_bg_sessionmaker: async_sessionmaker | None = None


def _bg_session() -> async_sessionmaker:
    global _bg_engine, _bg_sessionmaker
    if _bg_sessionmaker is None:
        _bg_engine = create_async_engine(settings.database_url)
        _bg_sessionmaker = async_sessionmaker(
            _bg_engine, class_=AsyncSession, expire_on_commit=False,
        )
    return _bg_sessionmaker


async def mark_dirty(source_id: uuid.UUID | str, entry_ids: list[uuid.UUID | str]) -> None:
    """Add entry ids to this source's pending set and stamp the
    first-added-at timestamp on first mark of a debounce window.

    Hot path — pipelined into a single Redis round-trip. Failures
    log at DEBUG and swallow; the worst case is a freshly-indexed
    entry never reaching Meili until the next scan, which is the
    same failure mode the v0.28 background task had.
    """
    if not entry_ids:
        return
    members = [str(e) for e in entry_ids]
    try:
        redis = _redis_client()
        set_key = _set_key(source_id)
        ts_key = _ts_key(source_id)
        pipe = redis.pipeline()
        pipe.sadd(set_key, *members)
        pipe.expire(set_key, _SET_TTL_SECONDS)
        # SETNX so we only stamp the timestamp on the FIRST mark of a
        # window. The debouncer reads this to decide whether to flush
        # on age (vs. size). A subsequent flush clears the ts key, so
        # the next batch's SETNX wins again.
        pipe.set(ts_key, str(time.time()), ex=_SET_TTL_SECONDS, nx=True)
        await pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "meili_indexer.mark_dirty failed for source=%s: %s",
            source_id, exc,
        )


async def flush(source_id: uuid.UUID | str) -> int:
    """Pop the pending set for ``source_id`` and bulk-index everything
    in it to Meilisearch. Returns the number of entries indexed.

    Idempotent on empty sets (returns 0). Safe to call from anywhere
    — the scan-terminal path calls this directly, and the debouncer
    calls it periodically.

    Implementation:
      1) SPOP up to _FLUSH_SIZE_THRESHOLD members (caps work per call).
      2) SELECT those entries from Postgres.
      3) build_entry_doc + tag_map + bulk Meili push.
      4) DEL the timestamp key so the next mark_dirty restarts the
         debounce window.
    """
    try:
        redis = _redis_client()
        set_key = _set_key(source_id)
        ts_key = _ts_key(source_id)
        # SPOP with count returns a list of popped members. Caps the
        # work this call does so a runaway set doesn't pin the loop.
        # If more remain, the next debouncer tick (or final scan-end
        # flush) picks them up.
        raw = await redis.spop(set_key, _FLUSH_SIZE_THRESHOLD)
    except Exception as exc:  # noqa: BLE001
        logger.debug("meili_indexer.flush SPOP failed: %s", exc)
        return 0
    if not raw:
        # Set was empty — best to also clear the orphan timestamp.
        try:
            await _redis_client().delete(ts_key)
        except Exception:  # noqa: BLE001
            pass
        return 0

    # SPOP returns set (single-arg) or list (count); decode_responses=True
    # gives strings either way. Normalise.
    if isinstance(raw, (set, frozenset)):
        members = list(raw)
    else:
        members = list(raw)
    try:
        uuids = [uuid.UUID(m) for m in members]
    except (TypeError, ValueError) as exc:
        logger.warning("meili_indexer.flush: bad members in set: %s", exc)
        uuids = []
    if not uuids:
        return 0

    from akashic.services.search import build_entry_doc, update_files_partial
    from akashic.services.tag_inheritance import get_tags_for_entries

    try:
        async with _bg_session()() as db:
            tag_map = await get_tags_for_entries(db, entry_ids=uuids)
            result = await db.execute(
                select(Entry).where(Entry.id.in_(uuids))
            )
            entries = [e for e in result.scalars() if e.kind == "file"]
            if entries:
                # v0.30.0 — partial update, NOT add_documents (full
                # replace). build_entry_doc emits only metadata fields
                # (no content_text), so a replace would wipe the
                # extracted text the scanner posted via
                # /api/ingest/content. update_documents merges by id:
                # metadata fields are overwritten, content_text is
                # left intact. The two writers (metadata flush here +
                # content endpoint) are independent and order-safe.
                await update_files_partial([
                    build_entry_doc(e, tags=tag_map.get(e.id, []))
                    for e in entries
                ])
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "meili_indexer.flush: Meili index failed for source=%s: %s",
            source_id, exc,
        )
        # Re-arm the set so we retry next tick. SPOP already removed
        # the members; put them back so they aren't lost.
        try:
            await _redis_client().sadd(_set_key(source_id), *members)
        except Exception:  # noqa: BLE001
            pass
        return 0

    # Success — clear the debounce timestamp so the next mark_dirty
    # opens a fresh window.
    try:
        await _redis_client().delete(ts_key)
    except Exception:  # noqa: BLE001
        pass
    return len(entries)


async def _pending_source_ids() -> list[uuid.UUID]:
    """SCAN for all sources with a non-empty pending set. Used by the
    debouncer to decide what to inspect each tick. Pattern-scan via
    Redis SCAN is non-blocking — the count is bounded by source count,
    which is small (tens, not millions)."""
    try:
        redis = _redis_client()
        keys: list[str] = []
        async for key in redis.scan_iter(match="akashic:meili:pending:*"):
            keys.append(key)
        # Filter out the timestamp-key flavor.
        keys = [k for k in keys if not k.startswith("akashic:meili:pending_since:")]
    except Exception as exc:  # noqa: BLE001
        logger.debug("meili_indexer._pending_source_ids SCAN failed: %s", exc)
        return []
    out: list[uuid.UUID] = []
    for k in keys:
        suffix = k.rsplit(":", 1)[-1]
        try:
            out.append(uuid.UUID(suffix))
        except ValueError:
            continue
    return out


async def _should_flush(source_id: uuid.UUID) -> bool:
    """Per-tick decision: flush iff SCARD >= threshold OR age >= max."""
    try:
        redis = _redis_client()
        scard = await redis.scard(_set_key(source_id))
        if scard >= _FLUSH_SIZE_THRESHOLD:
            return True
        ts_raw = await redis.get(_ts_key(source_id))
    except Exception:  # noqa: BLE001
        return False
    if scard == 0 or not ts_raw:
        return False
    try:
        ts = float(ts_raw)
    except (TypeError, ValueError):
        return False
    return (time.time() - ts) >= _FLUSH_AGE_SECONDS


async def run_debouncer(stop_event: asyncio.Event) -> None:
    """Long-running asyncio task: every tick, decide which source sets
    are due to flush and flush them. Exits cleanly when ``stop_event``
    is set.

    Started from the app lifespan in main.py; cancelled on shutdown.
    """
    logger.info("meili_indexer debouncer started")
    try:
        while not stop_event.is_set():
            try:
                # Short stop-aware sleep so shutdown is responsive even
                # during a long Meili push.
                await asyncio.wait_for(
                    stop_event.wait(), timeout=_DEBOUNCE_TICK_SECONDS,
                )
                return  # stop_event set
            except asyncio.TimeoutError:
                pass

            try:
                source_ids = await _pending_source_ids()
            except Exception as exc:  # noqa: BLE001
                logger.debug("meili_indexer debouncer scan failed: %s", exc)
                continue

            # Fan out flushes per source. Run concurrently so a slow
            # Meili call on source A doesn't gate the flush for source B.
            await asyncio.gather(
                *[
                    _flush_if_due(sid) for sid in source_ids
                ],
                return_exceptions=True,
            )
    finally:
        logger.info("meili_indexer debouncer stopped")


async def _flush_if_due(source_id: uuid.UUID) -> None:
    if await _should_flush(source_id):
        try:
            await flush(source_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "meili_indexer flush failed for source=%s: %s",
                source_id, exc,
            )
