"""Redis Queue worker for text extraction.

Run with: rq worker extraction --url redis://localhost:6379/0

Loads the entry, fetches the file content if reachable, extracts text via
the appropriate extractor (Tika for documents, direct decode for text),
then re-indexes the entry into Meilisearch with the extracted content.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from akashic.config import settings
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.services.extraction import extract_text
from akashic.services.search import index_file

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.database_url)
session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

MAX_EXTRACTION_SIZE = 50 * 1024 * 1024  # 50 MB

# v0.29.0 — Tika activity counters in Redis. Surfaced by the
# /api/health/services/activity endpoint so the admin "System Status"
# page can show queue depth, throughput, and last-activity timestamps
# without needing to grep worker logs. Per-minute bucket TTL of 360 s
# gives us a 5-minute rolling window with one bucket of slack so the
# read side never sees a partial-rollover gap.
_TIKA_TOTAL_KEY = "akashic:tika:extracted_total"
_TIKA_FAILED_KEY = "akashic:tika:failed_total"
_TIKA_LAST_AT_KEY = "akashic:tika:last_extracted_at"
_TIKA_BUCKET_PREFIX = "akashic:tika:extracted:"
_TIKA_BUCKET_TTL_SECONDS = 360


def process_file_extraction(entry_id: str):
    """Synchronous entry point for RQ. Runs the async extraction."""
    asyncio.run(_extract(entry_id))


async def _bump_tika_counters(*, success: bool) -> None:
    """Bump Tika activity counters in Redis. Best-effort — extraction
    succeeds even when Redis is unreachable; the counters are purely
    observational. Tries the async pub/sub client first (already
    initialized in the API process); the RQ worker subprocess gets a
    fresh per-process client on first call."""
    from akashic.services.scan_pubsub import _client as _redis_client
    try:
        redis = _redis_client()
        if success:
            now = datetime.now(timezone.utc)
            # Per-minute bucket key: YYYYMMDDHHMM. The activity endpoint
            # sums the last 5 buckets to derive "extracted in last 5 min".
            bucket = now.strftime("%Y%m%d%H%M")
            pipe = redis.pipeline()
            pipe.incr(_TIKA_TOTAL_KEY)
            pipe.set(_TIKA_LAST_AT_KEY, now.isoformat())
            pipe.incr(f"{_TIKA_BUCKET_PREFIX}{bucket}")
            pipe.expire(f"{_TIKA_BUCKET_PREFIX}{bucket}", _TIKA_BUCKET_TTL_SECONDS)
            await pipe.execute()
        else:
            await redis.incr(_TIKA_FAILED_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.debug("tika counters bump failed: %s", exc)


async def _read_file_content(source: Source, file_path: str) -> bytes | None:
    if source.type in ("local", "nfs"):
        full_path = Path(file_path)
        if full_path.exists() and full_path.is_file():
            try:
                return full_path.read_bytes()
            except (OSError, PermissionError) as exc:
                logger.warning("Cannot read %s: %s", full_path, exc)
                return None
    return None


async def _extract(entry_id: str):
    try:
        async with session_factory() as db:
            result = await db.execute(
                select(Entry).where(Entry.id == uuid.UUID(entry_id))
            )
            entry = result.scalar_one_or_none()
            if not entry or entry.kind != "file" or not entry.mime_type:
                return

            if entry.size_bytes and entry.size_bytes > MAX_EXTRACTION_SIZE:
                logger.debug(
                    "Skipping extraction for %s: too large (%d bytes)",
                    entry.path,
                    entry.size_bytes,
                )
                content_text = None
            else:
                source_result = await db.execute(
                    select(Source).where(Source.id == entry.source_id)
                )
                source = source_result.scalar_one_or_none()

                content_text = None
                if source:
                    file_bytes = await _read_file_content(source, entry.path)
                    if file_bytes:
                        content_text = await extract_text(file_bytes, entry.mime_type)
                        if content_text:
                            logger.info(
                                "Extracted %d chars from %s",
                                len(content_text),
                                entry.path,
                            )

            from akashic.services.search import build_entry_doc
            from akashic.services.tag_inheritance import get_tags_for_entries

            tag_map = await get_tags_for_entries(db, entry_ids=[entry.id])
            await index_file(build_entry_doc(
                entry,
                content_text=content_text,
                tags=tag_map.get(entry.id, []),
            ))
        await _bump_tika_counters(success=True)
    except Exception:
        await _bump_tika_counters(success=False)
        raise
