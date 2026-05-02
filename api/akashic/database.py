import asyncio
import logging
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from akashic.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Slow-query observability (v0.4.3).  Any individual SQL statement
# whose execution exceeds this threshold gets logged at WARN with
# a truncated SQL preview. Helps surface backend regressions
# (e.g. an unindexed JOIN) before users complain. The threshold
# is conservative — in steady state we should see ~zero of these.
_QUERY_SLOW_MS = 100


@event.listens_for(engine.sync_engine, "before_cursor_execute")
def _q_start(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
    conn.info.setdefault("_q_t0", []).append(time.perf_counter())


@event.listens_for(engine.sync_engine, "after_cursor_execute")
def _q_end(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
    stack = conn.info.get("_q_t0") or []
    if not stack:
        return
    t0 = stack.pop()
    dur_ms = (time.perf_counter() - t0) * 1000
    if dur_ms >= _QUERY_SLOW_MS:
        # Truncate the statement so a giant query doesn't blow the
        # log line; full text is in the EXPLAIN the operator will
        # run anyway. Newlines collapsed to single spaces so the
        # entry stays one log record.
        snippet = " ".join(statement.split())[:200]
        logger.warning("slow query: %.0fms: %s", dur_ms, snippet)
    # Phase 10 hooks the prometheus histogram in here too.
    try:
        from akashic.services import metrics
        metrics.observe_pg_query(statement, dur_ms / 1000.0)
    except Exception:  # noqa: BLE001
        # Metrics module may not be importable yet during startup;
        # don't let a bad import break SQL execution.
        pass


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
