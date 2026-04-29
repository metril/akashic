import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from akashic.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session


# Idempotent ALTERs run on startup (after Base.metadata.create_all). create_all
# only adds new tables/columns when the table is created — it does NOT alter
# existing tables. Each entry is `ALTER TABLE … ADD COLUMN IF NOT EXISTS …`
# style and safe to re-run. When Alembic gets introduced, this list collapses
# into a baseline migration and this helper is removed.
_INLINE_ALTERS: list[str] = [
    # Phase 1 (scan observability) — added 2026-04-29.
    "ALTER TABLE scans ADD COLUMN IF NOT EXISTS current_path TEXT",
    "ALTER TABLE scans ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ",
    "ALTER TABLE scans ADD COLUMN IF NOT EXISTS bytes_scanned_so_far BIGINT",
    "ALTER TABLE scans ADD COLUMN IF NOT EXISTS files_skipped INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE scans ADD COLUMN IF NOT EXISTS dirs_walked INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE scans ADD COLUMN IF NOT EXISTS dirs_queued INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE scans ADD COLUMN IF NOT EXISTS total_estimated INTEGER",
    "ALTER TABLE scans ADD COLUMN IF NOT EXISTS phase VARCHAR",
    "ALTER TABLE scans ADD COLUMN IF NOT EXISTS previous_scan_files INTEGER",
]


async def apply_inline_alters() -> None:
    """Run idempotent post-create_all schema patches.

    Each statement uses `IF NOT EXISTS` so re-runs are no-ops. Failures are
    logged but don't abort startup — a single failed ALTER must not take the
    API offline; the underlying feature simply degrades."""
    async with engine.begin() as conn:
        for stmt in _INLINE_ALTERS:
            try:
                await conn.execute(text(stmt))
            except Exception as exc:  # noqa: BLE001
                logger.warning("inline ALTER failed (continuing): %s — %s", stmt, exc)
