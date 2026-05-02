"""GET/PATCH /api/server-settings + cache invalidation."""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.user import User
from akashic.services.server_settings import (
    KEY_DISCOVERY_ENABLED, get_setting, invalidate_all,
)


@pytest_asyncio.fixture(autouse=True)
def _reset_cache():
    invalidate_all()
    yield
    invalidate_all()


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


@pytest.mark.asyncio
async def test_admin_can_set_and_read_setting(setup_db, admin_user):
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.patch(
            f"/api/server-settings/{KEY_DISCOVERY_ENABLED}",
            json={"value": True},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["key"] == KEY_DISCOVERY_ENABLED
        assert body["value"] is True

        listed = (await ac.get("/api/server-settings")).json()
        assert {"key": KEY_DISCOVERY_ENABLED, "value": True} in listed


@pytest.mark.asyncio
async def test_non_admin_cannot_patch(setup_db):
    async with setup_db() as session:
        viewer = User(
            id=uuid.uuid4(), username="v", email="v@b.c",
            password_hash="x", role="viewer",
        )
        session.add(viewer)
        await session.commit()
    async with _admin_client(setup_db, viewer) as ac:
        r = await ac.patch(
            f"/api/server-settings/{KEY_DISCOVERY_ENABLED}",
            json={"value": True},
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_unknown_setting_returns_404(setup_db, admin_user):
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.get("/api/server-settings/no_such_key")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_cache_busts_after_patch(setup_db, admin_user):
    """First read primes cache with the old value; PATCH must
    invalidate so the next read returns the new value, not the
    stale cached one."""
    async with setup_db() as session:
        # Default-when-missing: function returns the supplied default.
        v = await get_setting(session, KEY_DISCOVERY_ENABLED, default=False)
        assert v is False

    async with _admin_client(setup_db, admin_user) as ac:
        await ac.patch(
            f"/api/server-settings/{KEY_DISCOVERY_ENABLED}",
            json={"value": True},
        )

    async with setup_db() as session:
        v = await get_setting(session, KEY_DISCOVERY_ENABLED, default=False)
        assert v is True
