import asyncio
import os
from collections.abc import AsyncGenerator

# The Settings validator rejects the shipped default secret_key in
# prod; tests run with whatever default is around, so opt in to the
# dev-bypass before any code path imports config.
os.environ.setdefault("AKASHIC_DEV_ALLOW_DEFAULT_KEY", "1")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from akashic.database import Base, get_db
from akashic.main import create_app
from akashic.models import *  # noqa: F401,F403

TEST_DB_URL = os.environ.get(
    "TEST_DB_URL",
    "postgresql+asyncpg://akashic:changeme@localhost:5432/akashic_test",
)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    engine = create_async_engine(TEST_DB_URL, echo=False, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    # Clear in-process rate-limit buckets between tests so the
    # claim / discover / oauth-callback limiters don't drift over the
    # test session and 429 unrelated tests. The shared rate_limit
    # service module tracks every Limiter instance so one call resets
    # all of them.
    try:
        from akashic.services.rate_limit import reset_all
        reset_all()
    except Exception:
        pass
    # Reset the shared Redis client too — scan_pubsub caches it on the
    # event loop pytest-asyncio just closed, so a stale reference will
    # raise "Event loop is closed" on the next publish in any test
    # that exercises probe_dispatch / pubsub paths. The aclose() call
    # is safe even if the cached client was bound to a dead loop.
    try:
        from akashic.services import scan_pubsub
        await scan_pubsub.aclose()
    except Exception:
        pass
    yield session_maker
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    try:
        from akashic.services import scan_pubsub
        await scan_pubsub.aclose()
    except Exception:
        pass


@pytest_asyncio.fixture
async def client(setup_db):
    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with setup_db() as session:
            yield session
    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def db_session(setup_db):
    async with setup_db() as session:
        yield session


async def seed_scan(db_session, source_id, *, status: str = "pending"):
    """Pre-create a Scan row and return its UUID. Ingest tests need
    this since /api/ingest/batch refuses unknown scan_ids
    (review A-I6) — every batch must reference a Scan row that the
    api created via /api/scans/trigger or the lease path."""
    import uuid as _uuid

    from akashic.models.scan import Scan

    sid = _uuid.uuid4()
    db_session.add(
        Scan(id=sid, source_id=source_id, scan_type="incremental", status=status)
    )
    await db_session.commit()
    return sid
