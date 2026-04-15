"""
Redis Queue worker for text extraction.

Run with: rq worker extraction --url redis://localhost:6379/0

This worker:
1. Loads the file record from the database
2. Determines how to read the file content based on the source type
3. Extracts text using the appropriate extractor (Tika for documents, direct for text)
4. Indexes the file with extracted content_text into Meilisearch
"""
import asyncio
import logging
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from akashic.config import settings
from akashic.models.file import File
from akashic.models.source import Source
from akashic.services.extraction import extract_text
from akashic.services.search import index_file

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.database_url)
session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Max file size to attempt extraction on (50 MB)
MAX_EXTRACTION_SIZE = 50 * 1024 * 1024


def process_file_extraction(file_id: str):
    """Synchronous entry point for RQ. Runs the async extraction."""
    asyncio.run(_extract(file_id))


async def _read_file_content(source: Source, file_path: str) -> bytes | None:
    """Read file content based on source type.

    Currently supports local and NFS sources (direct filesystem read).
    For SSH/SMB/S3 sources, returns None (content extraction requires
    the scanner to push content, or a future content-fetch API).
    """
    if source.type in ("local", "nfs"):
        config = source.connection_config or {}
        # The file path is absolute from the source root
        full_path = Path(file_path)
        if full_path.exists() and full_path.is_file():
            try:
                return full_path.read_bytes()
            except (OSError, PermissionError) as exc:
                logger.warning("Cannot read %s: %s", full_path, exc)
                return None
    # SSH, SMB, S3 sources: content extraction not yet supported from worker
    # These would need connector access or a content-fetch API endpoint
    return None


async def _extract(file_id: str):
    async with session_factory() as db:
        result = await db.execute(select(File).where(File.id == uuid.UUID(file_id)))
        file = result.scalar_one_or_none()
        if not file or not file.mime_type:
            return

        # Skip files that are too large for extraction
        if file.size_bytes and file.size_bytes > MAX_EXTRACTION_SIZE:
            logger.debug("Skipping extraction for %s: too large (%d bytes)", file.path, file.size_bytes)
            content_text = None
        else:
            # Load the source to determine how to read the file
            source_result = await db.execute(select(Source).where(Source.id == file.source_id))
            source = source_result.scalar_one_or_none()

            content_text = None
            if source:
                file_bytes = await _read_file_content(source, file.path)
                if file_bytes:
                    content_text = await extract_text(file_bytes, file.mime_type)
                    if content_text:
                        logger.info("Extracted %d chars from %s", len(content_text), file.path)

        # Index into Meilisearch (with or without extracted text)
        await index_file({
            "id": str(file.id),
            "source_id": str(file.source_id),
            "path": file.path,
            "filename": file.filename,
            "extension": file.extension,
            "mime_type": file.mime_type,
            "size_bytes": file.size_bytes,
            "content_text": content_text,
            "fs_modified_at": int(file.fs_modified_at.timestamp()) if file.fs_modified_at else None,
            "tags": [],
        })
