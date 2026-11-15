"""Verify 0045_api_tokens revokes every live refresh token (the v1 cookie
path change forces a re-login), and leaves already-revoked rows alone."""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
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
    name = f"akashic_alembic_0045_{uuid.uuid4().hex[:10]}"
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


@pytest.mark.asyncio
async def test_0045_revokes_live_refresh_tokens(scratch_db):
    await _alembic_upgrade(scratch_db, "0044_scanner_runtime_config")

    eng = create_async_engine(scratch_db, poolclass=NullPool)
    uid, live, old = (str(uuid.uuid4()) for _ in range(3))
    try:
        async with eng.begin() as conn:
            await conn.execute(text(
                "INSERT INTO users (id, username, password_hash, role, auth_provider) VALUES (:id, 'u', 'x', 'user', 'local')"
            ), [{"id": uid}])
            for rid, h, revoked in ((live, "a" * 64, None), (old, "b" * 64, "now() - interval '1 day'")):
                await conn.execute(text(
                    "INSERT INTO refresh_tokens (id, user_id, chain_id, token_hash, expires_at, revoked_at, revoke_reason) "
                    f"VALUES (:id, :u, :c, :h, now() + interval '7 days', {revoked or 'NULL'}, "
                    f"{'\'rotated\'' if revoked else 'NULL'})"
                ), [{"id": rid, "u": uid, "c": str(uuid.uuid4()), "h": h}])

        await _alembic_upgrade(scratch_db, "0045_api_tokens")

        async with eng.connect() as conn:
            rows = {
                str(r[0]): (r[1], r[2])
                for r in await conn.execute(text("SELECT id, revoked_at, revoke_reason FROM refresh_tokens"))
            }
        assert rows[live][0] is not None and rows[live][1] == "api_v1_cookie_path"
        assert rows[old][1] == "rotated"  # already revoked: untouched
    finally:
        await eng.dispose()
