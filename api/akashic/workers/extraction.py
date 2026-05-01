"""Redis Queue worker for text extraction.

Run with: rq worker extraction --url redis://localhost:6379/0

Loads the entry, fetches the file content if reachable, extracts text via
the appropriate extractor (Tika for documents, direct decode for text),
then re-indexes the entry into Meilisearch with the extracted content.
"""
import asyncio
import logging
import uuid
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


def process_file_extraction(entry_id: str):
    """Synchronous entry point for RQ. Runs the async extraction."""
    asyncio.run(_extract(entry_id))


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
