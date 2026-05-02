"""POST /api/scans/trigger idempotency (v0.4.4).

The user pressed "Scan now" twice because the source.status doesn't
flip to 'scanning' until an agent leases the work (~5s after enqueue
on the default poll interval), so the button looked unresponsive
and they re-pressed. Without dedup that creates parallel pending
scan rows the agent then races on.

The fix returns the existing pending/running scan's id when one
already exists for the source, instead of inserting a duplicate.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.scan import Scan
from akashic.models.source import Source
from akashic.models.user import User


@pytest_asyncio.fixture
async def admin_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(), username="admin", email="a@b.c",
            password_hash="x", role="admin",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def _admin_client(setup_db, user: User) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_get_current_user():
        return user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_source(setup_db, name: str = "src") -> uuid.UUID:
    async with setup_db() as db:
        src = Source(
            id=uuid.uuid4(), name=name, type="local",
            connection_config={"path": "/tmp"},
        )
        db.add(src)
        await db.commit()
        return src.id


@pytest.mark.asyncio
async def test_trigger_returns_existing_pending_instead_of_stacking(
    setup_db, admin_user,
):
    src_id = await _seed_source(setup_db)
    async with _admin_client(setup_db, admin_user) as ac:
        first = await ac.post(
            "/api/scans/trigger",
            json={"source_id": str(src_id), "scan_type": "incremental"},
        )
        assert first.status_code == 200
        first_scan_id = first.json()["scan_id"]

        # Second press while the first is still pending.
        second = await ac.post(
            "/api/scans/trigger",
            json={"source_id": str(src_id), "scan_type": "incremental"},
        )
        assert second.status_code == 200

    # Same scan_id → no new row stacked.
    assert second.json()["scan_id"] == first_scan_id
    async with setup_db() as db:
        scans = (await db.execute(select(Scan))).scalars().all()
        assert len(scans) == 1


@pytest.mark.asyncio
async def test_trigger_creates_fresh_scan_after_terminal(setup_db, admin_user):
    """Once the prior scan completed, a new trigger creates a fresh
    row — the dedup is for *open* scans only."""
    src_id = await _seed_source(setup_db)
    async with _admin_client(setup_db, admin_user) as ac:
        first = (await ac.post(
            "/api/scans/trigger",
            json={"source_id": str(src_id), "scan_type": "incremental"},
        )).json()
        first_scan_id = first["scan_id"]

    # Mark the first scan completed manually (skip the lease/complete
    # round trip — only testing the dedup logic).
    async with setup_db() as db:
        scan = (await db.execute(
            select(Scan).where(Scan.id == uuid.UUID(first_scan_id))
        )).scalar_one()
        scan.status = "completed"
        await db.commit()

    async with _admin_client(setup_db, admin_user) as ac:
        second = (await ac.post(
            "/api/scans/trigger",
            json={"source_id": str(src_id), "scan_type": "incremental"},
        )).json()
    assert second["scan_id"] != first_scan_id

    async with setup_db() as db:
        scans = (await db.execute(select(Scan))).scalars().all()
        assert len(scans) == 2


@pytest.mark.asyncio
async def test_trigger_dedup_is_per_source(setup_db, admin_user):
    """Pending scan on src-A doesn't block triggering src-B."""
    a = await _seed_source(setup_db, name="src-a")
    b = await _seed_source(setup_db, name="src-b")
    async with _admin_client(setup_db, admin_user) as ac:
        a_resp = (await ac.post(
            "/api/scans/trigger",
            json={"source_id": str(a), "scan_type": "incremental"},
        )).json()
        b_resp = (await ac.post(
            "/api/scans/trigger",
            json={"source_id": str(b), "scan_type": "incremental"},
        )).json()
    assert a_resp["scan_id"] != b_resp["scan_id"]
    async with setup_db() as db:
        scans = (await db.execute(select(Scan))).scalars().all()
        assert len(scans) == 2
