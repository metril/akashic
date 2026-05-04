"""Verify the 0023_hosts alembic migration:

- Creates the hosts table.
- Adds the host_id FK + index on sources.
- Backfills 1:1 hosts for non-local sources, moving host-shaped keys
  out of source.connection_config.
- Leaves local sources alone (host_id stays NULL).
- The downgrade reconstructs the original combined connection_config.

Uses a scratch database (same pattern as test_alembic_baseline.py)
because the conftest auto-creates schema via Base.metadata.create_all
and bypasses alembic entirely.
"""
from __future__ import annotations

import json
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


def _sync_url(asyncpg_url: str) -> str:
    return asyncpg_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


@pytest_asyncio.fixture
async def scratch_db():
    name = f"akashic_alembic_0023_{uuid.uuid4().hex[:10]}"
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
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # api/
    cfg.set_main_option("script_location", os.path.join(here, "alembic"))
    return cfg


async def _alembic_upgrade(url: str, target: str, monkeypatch) -> None:
    """Drive alembic to ``target`` against ``url``.

    The project's env.py reads ``settings.database_url`` and invokes
    ``asyncio.run(...)`` to drive the migration — which crashes when
    called from inside an already-running event loop. So we shell out
    to a thread and monkey-patch the global setting for the duration.
    """
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


async def _alembic_downgrade(url: str, target: str, monkeypatch) -> None:
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
async def test_0023_creates_hosts_and_backfills(scratch_db, monkeypatch):
    # Bring schema up to the migration *before* 0023 so we can plant
    # legacy-shape rows (host fields still in source.connection_config).
    await _alembic_upgrade(scratch_db, "0022_source_reachability", monkeypatch)

    eng = create_async_engine(scratch_db, poolclass=NullPool)
    try:
        # Insert two non-local sources (smb + ssh) with full configs and
        # one local source whose host_id should remain NULL post-upgrade.
        async with eng.begin() as conn:
            await conn.execute(text(
                "INSERT INTO sources (id, name, type, status, connection_config) "
                "VALUES (:id, :name, :type, 'offline', CAST(:cfg AS jsonb))"
            ), [
                {
                    "id": uuid.uuid4(),
                    "name": "smb-share",
                    "type": "smb",
                    "cfg": json.dumps({
                        "host": "fs.example.com",
                        "port": 445,
                        "username": "scan",
                        "password": "s3cret",
                        "share": "Docs",
                    }),
                },
                {
                    "id": uuid.uuid4(),
                    "name": "ssh-box",
                    "type": "ssh",
                    "cfg": json.dumps({
                        "host": "ssh.example.com",
                        "port": 22,
                        "username": "scan",
                        "password": "p",
                        "known_hosts_path": "/etc/ssh/known_hosts",
                    }),
                },
                {
                    "id": uuid.uuid4(),
                    "name": "local-fs",
                    "type": "local",
                    "cfg": json.dumps({"path": "/srv/data"}),
                },
            ])

        # Apply 0023 — schema + backfill.
        await _alembic_upgrade(scratch_db, "0023_hosts", monkeypatch)

        async with eng.connect() as conn:
            # hosts table exists
            tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
            assert "hosts" in tables

            # host_id column on sources
            cols = await conn.run_sync(
                lambda c: {col["name"] for col in inspect(c).get_columns("sources")}
            )
            assert "host_id" in cols

            # Two host rows created (one per non-local source)
            host_count = (await conn.execute(
                text("SELECT COUNT(*) FROM hosts")
            )).scalar_one()
            assert host_count == 2

            # smb source: host fields moved off, share stays
            smb = (await conn.execute(text(
                "SELECT s.host_id, s.connection_config, h.connection_config "
                "FROM sources s JOIN hosts h ON s.host_id = h.id "
                "WHERE s.name = 'smb-share'"
            ))).fetchone()
            assert smb is not None
            assert smb[1] == {"share": "Docs"}
            assert smb[2]["host"] == "fs.example.com"
            assert smb[2]["password"] == "s3cret"
            assert "share" not in smb[2]  # share stays on source row

            # ssh source: host fields moved off
            ssh = (await conn.execute(text(
                "SELECT s.host_id, s.connection_config, h.connection_config "
                "FROM sources s JOIN hosts h ON s.host_id = h.id "
                "WHERE s.name = 'ssh-box'"
            ))).fetchone()
            assert ssh is not None
            # SSH has no share-shaped keys, so source config is now empty
            assert ssh[1] == {}
            assert ssh[2]["host"] == "ssh.example.com"
            assert ssh[2]["known_hosts_path"] == "/etc/ssh/known_hosts"

            # local source: untouched, no host
            local = (await conn.execute(text(
                "SELECT host_id, connection_config FROM sources WHERE name = 'local-fs'"
            ))).fetchone()
            assert local[0] is None
            assert local[1] == {"path": "/srv/data"}
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_0023_downgrade_restores_combined_config(scratch_db, monkeypatch):
    await _alembic_upgrade(scratch_db, "0022_source_reachability", monkeypatch)

    eng = create_async_engine(scratch_db, poolclass=NullPool)
    try:
        async with eng.begin() as conn:
            await conn.execute(text(
                "INSERT INTO sources (id, name, type, status, connection_config) "
                "VALUES (:id, :name, :type, 'offline', CAST(:cfg AS jsonb))"
            ), {
                "id": uuid.uuid4(),
                "name": "smb-share",
                "type": "smb",
                "cfg": json.dumps({
                    "host": "fs", "username": "u", "password": "p",
                    "share": "Docs",
                }),
            })

        await _alembic_upgrade(scratch_db, "0023_hosts", monkeypatch)
        await _alembic_downgrade(scratch_db, "0022_source_reachability", monkeypatch)

        async with eng.connect() as conn:
            tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
            assert "hosts" not in tables
            cols = await conn.run_sync(
                lambda c: {col["name"] for col in inspect(c).get_columns("sources")}
            )
            assert "host_id" not in cols
            row = (await conn.execute(text(
                "SELECT connection_config FROM sources WHERE name = 'smb-share'"
            ))).fetchone()
            cfg_back = row[0]
            # All fields back on the source after downgrade.
            assert cfg_back["host"] == "fs"
            assert cfg_back["password"] == "p"
            assert cfg_back["share"] == "Docs"
    finally:
        await eng.dispose()
