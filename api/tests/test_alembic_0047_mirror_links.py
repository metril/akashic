"""Verify 0047_mirror_links adds entry link columns, constraint, indexes, FK behaviour."""
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
    name = f"akashic_alembic_0047_{uuid.uuid4().hex[:10]}"
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



async def _ins(conn, eid, role=None, linked=None):
    await conn.execute(text(
        "INSERT INTO entries (id, kind, path, name, parent_path, is_deleted, link_role, linked_entry_id) "
        "VALUES (:id, 'file', :p, 'n', '/', false, :r, :l)"
    ), {"id": eid, "p": f"/{eid}", "r": role, "l": linked})


@pytest.mark.asyncio
async def test_upgrade_adds_link_columns(scratch_db):
    await _alembic_upgrade(scratch_db, "0047_mirror_links")
    eng = create_async_engine(scratch_db, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            def _insp(c):
                i = inspect(c)
                return (
                    {col["name"] for col in i.get_columns("entries")},
                    {ix["name"] for ix in i.get_indexes("entries")},
                    {cc["name"] for cc in i.get_check_constraints("entries")},
                    {ix["name"]: ix.get("dialect_options", {}).get("postgresql_where") for ix in i.get_indexes("entries")},
                )
            cols, idx, checks, where = await conn.run_sync(_insp)
        assert "link_role IS NOT NULL" in str(where["ix_entries_source_link_role"])
        assert "linked_entry_id IS NOT NULL" in str(where["ix_entries_linked_entry_id"])
        assert {"linked_entry_id", "link_role", "link_target_viewable", "link_target_source_id"} <= cols
        assert {"ix_entries_linked_entry_id", "ix_entries_source_link_role"} <= idx
        assert {"ck_entries_link_role", "ck_entries_link_role_when_linked"} <= checks
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_link_role_check_rejects_unknown(scratch_db):
    await _alembic_upgrade(scratch_db, "0047_mirror_links")
    eng = create_async_engine(scratch_db, poolclass=NullPool)
    try:
        with pytest.raises(IntegrityError):
            async with eng.begin() as conn:
                await _ins(conn, str(uuid.uuid4()), role="bogus")
        async with eng.begin() as conn:
            await _ins(conn, str(uuid.uuid4()), role="thumbnail")
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_linked_entry_requires_link_role(scratch_db):
    await _alembic_upgrade(scratch_db, "0047_mirror_links")
    eng = create_async_engine(scratch_db, poolclass=NullPool)
    target = str(uuid.uuid4())
    try:
        async with eng.begin() as conn:
            await _ins(conn, target)
        with pytest.raises(IntegrityError):
            async with eng.begin() as conn:
                await _ins(conn, str(uuid.uuid4()), role=None, linked=target)
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_hard_delete_target_nulls_link(scratch_db):
    await _alembic_upgrade(scratch_db, "0047_mirror_links")
    eng = create_async_engine(scratch_db, poolclass=NullPool)
    target, backing = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        async with eng.begin() as conn:
            await _ins(conn, target)
            await _ins(conn, backing, role="original", linked=target)
            await conn.execute(text("DELETE FROM entries WHERE id = :i"), {"i": target})
        async with eng.connect() as conn:
            v = (await conn.execute(text("SELECT linked_entry_id FROM entries WHERE id=:i"), {"i": backing})).scalar()
        assert v is None
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_downgrade_removes_link_columns(scratch_db):
    await _alembic_upgrade(scratch_db, "0047_mirror_links")
    await _alembic_downgrade(scratch_db, "0046_user_roles")
    eng = create_async_engine(scratch_db, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            def _insp(c):
                i = inspect(c)
                return (
                    {col["name"] for col in i.get_columns("entries")},
                    {ix["name"] for ix in i.get_indexes("entries")},
                    {cc["name"] for cc in i.get_check_constraints("entries")},
                )
            cols, idx, checks = await conn.run_sync(_insp)
        assert not cols & {"linked_entry_id", "link_role", "link_target_viewable", "link_target_source_id"}
        assert not idx & {"ix_entries_linked_entry_id", "ix_entries_source_link_role"}
        assert "ck_entries_link_role" not in checks
    finally:
        await eng.dispose()
