"""Directory settings registration and secret masking."""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from akashic.auth.dependencies import get_principal
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.user import User
from akashic.services import runtime_settings as rs
from akashic.services.server_settings import invalidate_all
from tests.principals import as_principal

_KEYS = ("ldap_group_base", "authentik_url", "authentik_api_token", "directory_sync_interval_minutes")


@pytest.fixture(autouse=True)
def _reset_cache():
    invalidate_all()
    yield
    invalidate_all()


def test_directory_settings_registered():
    assert rs.REGISTRY["authentik_api_token"].secret is True
    assert rs.REGISTRY["directory_sync_interval_minutes"].default == 60
    assert rs.REGISTRY["ldap_group_base"].default == ""
    assert all(rs.REGISTRY[k].group == "directory" for k in _KEYS)
    assert ("directory", "Directory") in rs.GROUPS


@pytest.mark.asyncio
async def test_settings_api_masks_authentik_token(setup_db):
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(), username=f"admin-{uuid.uuid4().hex[:8]}", email="a@b.c",
            password_hash="x", role="admin",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    async def _db():
        async with setup_db() as session:
            yield session

    async def _user():
        return user

    app = create_app()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_principal] = as_principal(_user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.patch("/api/v1/server-settings/authentik_api_token", json={"value": "tok-abc-123"})
        assert r.status_code == 200
        assert "tok-abc-123" not in r.text
        got = await ac.get("/api/v1/server-settings/authentik_api_token")
        assert got.json()["value"] == rs.MASK and got.json()["set"] is True
        listed = (await ac.get("/api/v1/server-settings")).json()
        assert "tok-abc-123" not in str(listed)
    assert rs.rt().authentik_api_token == "tok-abc-123"
