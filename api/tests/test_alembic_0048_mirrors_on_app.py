"""Verify 0048_mirrors_on_app moves mirror_of to mirrors (and back on downgrade)."""
from __future__ import annotations

import json
import os
import uuid

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


def _admin_url() -> str:
    base = os.environ.get(
        "TEST_DB_URL",
        "postgresql+asyncpg://akashic:changeme@localhost:5432/akashic_test",
    )
    head, _, _ = base.rpartition("/")
    return f"{head}/postgres"


def _scratch_url(db_name: str) -> str:
    base = os.environ.get(
        "TEST_DB_URL",
        "postgresql+asyncpg://akashic:changeme@localhost:5432/akashic_test",
    )
    head, _, _ = base.rpartition("/")
    return f"{head}/{db_name}"


@pytest_asyncio.fixture
async def scratch_db():
    name = f"akashic_alembic_0048_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(_admin_url(), isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
        yield _scratch_url(name)
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        await admin.dispose()


def _alembic_cfg() -> Config:
    cfg = Config()
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg.set_main_option("script_location", os.path.join(here, "alembic"))
    return cfg


async def _alembic_upgrade(url: str, target: str) -> None:
    import asyncio as _asyncio
    from akashic.config import settings as _settings

    def _run() -> None:
        old = _settings.database_url
        _settings.database_url = url
        try:
            command.upgrade(_alembic_cfg(), target)
        finally:
            _settings.database_url = old

    await _asyncio.to_thread(_run)


async def _alembic_downgrade(url: str, target: str) -> None:
    import asyncio as _asyncio
    from akashic.config import settings as _settings

    def _run() -> None:
        old = _settings.database_url
        _settings.database_url = url
        try:
            command.downgrade(_alembic_cfg(), target)
        finally:
            _settings.database_url = old

    await _asyncio.to_thread(_run)





async def _put(conn, name, type_, cfg):
    sid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO sources (id, name, type, status, connection_config) "
            "VALUES (:i, :n, :t, 'offline', CAST(:c AS jsonb))"
        ),
        {"i": sid, "n": name, "t": type_, "c": json.dumps(cfg)},
    )
    return str(sid)


async def _cfg(url, sid):
    eng = create_async_engine(url, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            return (await conn.execute(
                text("SELECT connection_config FROM sources WHERE id = :i"), {"i": uuid.UUID(sid)}
            )).scalar()
    finally:
        await eng.dispose()


async def _seed(url, mirror_of_for):
    """Insert an immich app and a local backing carrying mirror_of; return (app, back)."""
    eng = create_async_engine(url, poolclass=NullPool)
    try:
        async with eng.begin() as conn:
            app = await _put(conn, "app", "immich", {"url": "http://x"})
            cfg = {"path": "/m", "mirror_of": mirror_of_for(app)}
            back = await _put(conn, "back", "local", cfg)
    finally:
        await eng.dispose()
    return app, back


@pytest.mark.asyncio
async def test_upgrade_moves_mirror_of(scratch_db):
    await _alembic_upgrade(scratch_db, "0047_mirror_links")
    app, back = await _seed(scratch_db, lambda a: [
        {"source_id": a, "remote_prefix": "/data", "local_prefix": "/m"}])
    await _alembic_upgrade(scratch_db, "0048_mirrors_on_app")
    assert (await _cfg(scratch_db, app))["mirrors"] == [
        {"source_id": back, "remote_prefix": "/data", "local_prefix": "/m"}]
    assert await _cfg(scratch_db, back) == {"path": "/m"}


@pytest.mark.asyncio
async def test_upgrade_drops_dangling_and_duplicates(scratch_db):
    await _alembic_upgrade(scratch_db, "0047_mirror_links")
    item = {"remote_prefix": "/data", "local_prefix": "/m"}
    app, back = await _seed(scratch_db, lambda a: [
        {"source_id": a, **item}, {"source_id": a, **item},
        {"source_id": str(uuid.uuid4()), **item}])
    await _alembic_upgrade(scratch_db, "0048_mirrors_on_app")
    assert (await _cfg(scratch_db, app))["mirrors"] == [{"source_id": back, **item}]
    assert "mirror_of" not in await _cfg(scratch_db, back)


@pytest.mark.asyncio
async def test_downgrade_restores_mirror_of(scratch_db):
    await _alembic_upgrade(scratch_db, "0047_mirror_links")
    orig = lambda a: [
        {"source_id": a, "remote_prefix": "/data", "local_prefix": "/m"},
        {"source_id": a, "remote_prefix": "/other", "local_prefix": "/n"}]
    app, back = await _seed(scratch_db, orig)
    await _alembic_upgrade(scratch_db, "0048_mirrors_on_app")
    await _alembic_downgrade(scratch_db, "0047_mirror_links")
    assert (await _cfg(scratch_db, back))["mirror_of"] == orig(app)
    assert "mirrors" not in await _cfg(scratch_db, app)
