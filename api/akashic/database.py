import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from akashic.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session


def _alembic_config() -> Config:
    """Build an Alembic Config pointing at the api directory's alembic/ tree.

    Resolved relative to this file so it works whether the api is started
    from the repo root, from `api/`, or inside a Docker container with
    /app as the working dir."""
    cfg_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    cfg = Config(str(cfg_path))
    cfg.set_main_option("script_location", str(cfg_path.parent / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    return cfg


async def ensure_schema() -> None:
    """Bring the schema to the latest Alembic revision.

    Three cases the lifespan needs to handle correctly:
      1. Fresh DB (no tables, no alembic_version table): `alembic upgrade head`
         creates everything from migrations.
      2. Existing DB created via the old `Base.metadata.create_all` path,
         possibly augmented with the legacy `_INLINE_ALTERS` columns, and
         no `alembic_version` row: stamp it at `head` first so Alembic
         knows the baseline is already in place, then run upgrade (a no-op
         until later migrations are added).
      3. DB already managed by Alembic: just `alembic upgrade head`.

    Cases 1 and 2 are distinguished by the presence of the `users` table —
    part of the baseline but not of an empty DB.

    A short-lived engine is used (rather than the module-level `engine`)
    so tests can swap settings.database_url between calls.
    """
    probe = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with probe.connect() as conn:

            def _has_table(sync_conn, name: str) -> bool:
                return inspect(sync_conn).has_table(name)

            users_present = await conn.run_sync(_has_table, "users")
            alembic_version_present = await conn.run_sync(_has_table, "alembic_version")
    finally:
        await probe.dispose()

    cfg = _alembic_config()
    if users_present and not alembic_version_present:
        # Pre-Alembic deployment — adopt without re-creating the schema.
        logger.info("Existing schema detected; stamping at baseline before upgrade")
        await _run_alembic_in_executor(command.stamp, cfg, "head")

    await _run_alembic_in_executor(command.upgrade, cfg, "head")
    logger.info("Database schema at head")


async def _run_alembic_in_executor(fn, *args) -> None:
    """Alembic's command API is sync. Our env.py runs the migration inside
    its own `asyncio.run(...)`, which requires no running loop in the
    calling thread — so offload to a ThreadPoolExecutor thread (which has
    none) rather than calling sync from within the lifespan's loop."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, fn, *args)
