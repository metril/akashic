"""Verify the Alembic baseline migration handles all three deployment paths.

The lifespan in akashic.database.ensure_schema must:
  1. Create everything cleanly on a fresh DB.
  2. Adopt an existing DB (created via the legacy Base.metadata.create_all
     path) by stamping at head before running upgrade.
  3. Be safe to re-run (no errors on a DB already at head).

These cases aren't covered by the API integration tests (which use
create_all directly via conftest.py), so they need explicit coverage.
"""
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


def _admin_url() -> str:
    """URL pointing at the `postgres` admin DB so tests can create/drop their
    own scratch databases without colliding with the test fixture's DB."""
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
    """Provision a fresh empty database, hand back its URL, drop on teardown."""
    name = f"akashic_alembic_scratch_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(_admin_url(), isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
        yield _scratch_url(name)
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        await admin.dispose()


async def _table_set(url: str) -> set[str]:
    eng = create_async_engine(url, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            return await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    finally:
        await eng.dispose()


async def _alembic_version(url: str) -> str | None:
    eng = create_async_engine(url, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            try:
                row = (await conn.execute(text("SELECT version_num FROM alembic_version"))).fetchone()
                return row[0] if row else None
            except Exception:
                return None
    finally:
        await eng.dispose()


# Tables expected to be present after the baseline migration. We're not
# enumerating columns — `\d scans` would catch column drift but the
# integration tests already exercise scan ingestion and would surface
# missing columns through real failures.
_REQUIRED_TABLES = {
    "users", "sources", "entries", "scans", "scan_log_entries",
    "fs_persons", "fs_bindings", "principals_cache", "principal_groups_cache",
    "audit_events", "tags", "webhooks", "entry_versions", "entry_events",
    "entry_tags", "api_keys", "purge_log", "source_permissions",
}


@pytest.mark.asyncio
async def test_fresh_db_upgrade_creates_full_schema(scratch_db, monkeypatch):
    """Case 1: empty DB → upgrade head → all tables present, stamped."""
    from akashic.config import settings
    from akashic.database import ensure_schema

    monkeypatch.setattr(settings, "database_url", scratch_db)
    await ensure_schema()

    tables = await _table_set(scratch_db)
    missing = _REQUIRED_TABLES - tables
    assert not missing, f"missing tables after fresh upgrade: {missing}"
    assert "alembic_version" in tables
    assert await _alembic_version(scratch_db) == "0001_baseline"


@pytest.mark.asyncio
async def test_existing_db_is_stamped_then_upgraded(scratch_db, monkeypatch):
    """Case 2: pre-Alembic DB (created via Base.metadata.create_all) →
    ensure_schema detects it, stamps at baseline, then upgrades."""
    from akashic.config import settings
    from akashic.database import Base, ensure_schema
    from akashic import models  # noqa: F401  (registers tables on Base.metadata)

    monkeypatch.setattr(settings, "database_url", scratch_db)

    # Simulate the legacy create_all path.
    pre_engine = create_async_engine(scratch_db, poolclass=NullPool)
    try:
        async with pre_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await pre_engine.dispose()

    # No alembic_version row yet — this is the discriminator.
    assert await _alembic_version(scratch_db) is None

    await ensure_schema()

    assert await _alembic_version(scratch_db) == "0001_baseline"
    tables = await _table_set(scratch_db)
    assert _REQUIRED_TABLES.issubset(tables)


@pytest.mark.asyncio
async def test_ensure_schema_is_idempotent(scratch_db, monkeypatch):
    """Case 3: running ensure_schema twice in a row is a no-op the second time."""
    from akashic.config import settings
    from akashic.database import ensure_schema

    monkeypatch.setattr(settings, "database_url", scratch_db)
    await ensure_schema()
    await ensure_schema()  # second call must not raise

    assert await _alembic_version(scratch_db) == "0001_baseline"
