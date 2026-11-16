"""Verify 0049_directory_cache creates the directory tables and the per-backend run lock."""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


def _base() -> str:
    return os.environ.get(
        "TEST_DB_URL",
        "postgresql+asyncpg://akashic:changeme@localhost:5432/akashic_test",
    )


def _admin_url() -> str:
    return f"{_base().rpartition('/')[0]}/postgres"


def _scratch_url(db_name: str) -> str:
    return f"{_base().rpartition('/')[0]}/{db_name}"


@pytest_asyncio.fixture
async def scratch_db():
    name = f"akashic_alembic_0049_{uuid.uuid4().hex[:10]}"
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


async def _alembic(url: str, fn, target: str) -> None:
    import asyncio as _asyncio
    from akashic.config import settings as _settings

    def _run() -> None:
        old = _settings.database_url
        _settings.database_url = url
        try:
            fn(_alembic_cfg(), target)
        finally:
            _settings.database_url = old

    await _asyncio.to_thread(_run)


async def _run_row(conn, backend: str, status: str = "running") -> None:
    await conn.execute(text(
        "INSERT INTO directory_sync_runs (id, backend, status, trigger) "
        "VALUES (:i, :b, :s, 'manual')"
    ), {"i": str(uuid.uuid4()), "b": backend, "s": status})


@pytest.mark.asyncio
async def test_0049_upgrade_creates_tables_and_lock_index(scratch_db):
    await _alembic(scratch_db, command.upgrade, "head")
    eng = create_async_engine(scratch_db, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            def _insp(c):
                i = inspect(c)
                return (
                    set(i.get_table_names()),
                    {ix["name"] for ix in i.get_indexes("directory_entries")},
                    {u["name"] for u in i.get_unique_constraints("directory_entries")},
                )
            tables, idx, uniques = await conn.run_sync(_insp)
        assert {"directory_entries", "directory_sync_runs"} <= tables
        assert {
            "ix_directory_entries_sid", "ix_directory_entries_dn_lower",
            "ix_directory_entries_email_lower", "ix_directory_entries_username_lower",
        } <= idx
        assert "uq_directory_entries_backend_ref" in uniques

        async with eng.begin() as conn:
            await _run_row(conn, "ad")
            await _run_row(conn, "authentik")
            await _run_row(conn, "ad", status="succeeded")
        with pytest.raises(IntegrityError):
            async with eng.begin() as conn:
                await _run_row(conn, "ad")

        async with eng.begin() as conn:
            await conn.execute(text(
                "INSERT INTO directory_entries (id, backend, ref, kind, groups_status, synced_at) "
                "VALUES (:i, 'ad', 'r1', 'person', 'none', now())"
            ), {"i": str(uuid.uuid4())})
            row = (await conn.execute(text("SELECT group_sids FROM directory_entries"))).scalar()
        assert row == []
        with pytest.raises(IntegrityError):
            async with eng.begin() as conn:
                await conn.execute(text(
                    "INSERT INTO directory_entries (id, backend, ref, kind, groups_status, synced_at) "
                    "VALUES (:i, 'ad', 'r1', 'group', 'none', now())"
                ), {"i": str(uuid.uuid4())})
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_0049_downgrade_drops_tables(scratch_db):
    await _alembic(scratch_db, command.upgrade, "head")
    await _alembic(scratch_db, command.downgrade, "0048_mirrors_on_app")
    eng = create_async_engine(scratch_db, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        assert not tables & {"directory_entries", "directory_sync_runs"}
    finally:
        await eng.dispose()
