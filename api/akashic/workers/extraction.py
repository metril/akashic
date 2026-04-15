"""
Redis Queue worker for text extraction.

Run with: rq worker extraction --url redis://localhost:6379/0
"""
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from akashic.config import settings
from akashic.models.file import File
from akashic.services.search import index_file

engine = create_async_engine(settings.database_url)
session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def process_file_extraction(file_id: str):
    asyncio.run(_extract(file_id))


async def _extract(file_id: str):
    async with session_factory() as db:
        result = await db.execute(select(File).where(File.id == uuid.UUID(file_id)))
        file = result.scalar_one_or_none()
        if not file or not file.mime_type:
            return

        await index_file({
            "id": str(file.id),
            "source_id": str(file.source_id),
            "path": file.path,
            "filename": file.filename,
            "extension": file.extension,
            "mime_type": file.mime_type,
            "size_bytes": file.size_bytes,
            "fs_modified_at": int(file.fs_modified_at.timestamp()) if file.fs_modified_at else None,
            "tags": [],
        })
