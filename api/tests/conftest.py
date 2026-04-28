import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from akashic.database import Base, get_db
from akashic.main import create_app
from akashic.models import *  # noqa: F401,F403

import os

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
    yield session_maker
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


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
