"""Verify 0027_scan_inaccessible: adds inaccessible_dirs +
inaccessible_files to scans with default 0, downgrade drops them
cleanly."""
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
    name = f"akashic_alembic_0027_{uuid.uuid4().hex[:10]}"
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
async def test_0027_adds_inaccessible_columns_with_default_zero(scratch_db):
    await _alembic_upgrade(scratch_db, "0026_credential_profiles")

    eng = create_async_engine(scratch_db, poolclass=NullPool)
    try:
        # Plant a scan row before the migration to verify the default
        # backfills cleanly to 0.
        async with eng.begin() as conn:
            await conn.execute(text(
                "INSERT INTO sources (id, name, type, status, connection_config) "
                "VALUES (:id, 'src', 'local', 'offline', '{}'::jsonb)"
            ), [{"id": str(uuid.uuid4())}])
            src_row = await conn.execute(text("SELECT id FROM sources LIMIT 1"))
            src_id = src_row.scalar_one()
            await conn.execute(text(
                "INSERT INTO scans (id, source_id, scan_type, status, "
                "files_found, files_new, files_changed, files_deleted, bytes_scanned) "
                "VALUES (:id, :src, 'full', 'completed', 0, 0, 0, 0, 0)"
            ), [{"id": str(uuid.uuid4()), "src": str(src_id)}])

        await _alembic_upgrade(scratch_db, "0027_scan_inaccessible")

        async with eng.connect() as conn:
            cols = await conn.run_sync(
                lambda c: {col["name"]: col for col in inspect(c).get_columns("scans")}
            )
            assert "inaccessible_dirs" in cols
            assert "inaccessible_files" in cols
            assert cols["inaccessible_dirs"]["nullable"] is False
            assert cols["inaccessible_files"]["nullable"] is False

            # Pre-existing row backfilled to 0 (server_default).
            row = await conn.execute(text(
                "SELECT inaccessible_dirs, inaccessible_files FROM scans LIMIT 1"
            ))
            ad, af = row.one()
            assert ad == 0
            assert af == 0

        # Downgrade drops the columns.
        await _alembic_downgrade(scratch_db, "0026_credential_profiles")

        async with eng.connect() as conn:
            cols = await conn.run_sync(
                lambda c: {col["name"] for col in inspect(c).get_columns("scans")}
            )
            assert "inaccessible_dirs" not in cols
            assert "inaccessible_files" not in cols
    finally:
        await eng.dispose()
