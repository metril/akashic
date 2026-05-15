"""v0.29.5 — API-side empty-password validation for SMB.

Covers the validators added in:
  - schemas/credential_profile.py:assert_smb_has_password
  - routers/sources.py:_validate_smb_password_requirement

Both prevent a broken credential set from reaching the DB, so the
scanner-side rejection (probe + connector) becomes a defence-in-depth
guard rather than the only barrier.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.auth.dependencies import get_current_user, require_admin
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.user import User


@pytest_asyncio.fixture
async def admin_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(), username="admin", email="admin@example.com",
            password_hash="x", role="admin",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def client(setup_db, admin_user: User) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_get_current_user():
        return admin_user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    app.dependency_overrides[require_admin] = _override_get_current_user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_smb_profile_without_password_returns_422(client: AsyncClient):
    r = await client.post(
        "/api/credential-profiles",
        json={"name": "smb-bad", "type": "smb",
              "credentials": {"username": "alice"}},
    )
    assert r.status_code == 422, r.text
    assert "non-empty password" in r.json()["detail"].lower() \
        or "non-empty password" in r.text.lower()


@pytest.mark.asyncio
async def test_smb_profile_with_empty_string_password_returns_422(client: AsyncClient):
    r = await client.post(
        "/api/credential-profiles",
        json={"name": "smb-empty", "type": "smb",
              "credentials": {"username": "alice", "password": ""}},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_smb_profile_with_password_succeeds(client: AsyncClient):
    r = await client.post(
        "/api/credential-profiles",
        json={"name": "smb-ok", "type": "smb",
              "credentials": {"username": "alice", "password": "s3cret"}},
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_smb_profile_patch_strips_password_returns_422(client: AsyncClient):
    """Update path: PATCHing the password to empty must be rejected.
    Sentinel handling means "***" is a no-op; "" is a real empty."""
    r = await client.post(
        "/api/credential-profiles",
        json={"name": "smb-patch", "type": "smb",
              "credentials": {"username": "a", "password": "real"}},
    )
    assert r.status_code == 201
    profile_id = r.json()["id"]

    r2 = await client.patch(
        f"/api/credential-profiles/{profile_id}",
        json={"credentials": {"password": ""}},
    )
    assert r2.status_code == 422, r2.text


@pytest.mark.asyncio
async def test_nfs_profile_with_empty_credentials_succeeds(client: AsyncClient):
    """Only SMB is gated — NFS / S3 / other profiles can still have
    empty credential dicts (their own probes handle missing fields)."""
    r = await client.post(
        "/api/credential-profiles",
        json={"name": "nfs-empty-ok", "type": "nfs", "credentials": {}},
    )
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_smb_source_without_password_anywhere_returns_422(client: AsyncClient):
    """Inline SMB source (no profile, no host) without a password
    must be rejected — same bypass surface as a profile without one."""
    r = await client.post(
        "/api/sources",
        json={
            "name": f"smb-src-{uuid.uuid4().hex[:8]}",
            "type": "smb",
            "connection_config": {
                "host": "smb.example.com",
                "username": "alice",
                "share": "share1",
                # password absent
            },
        },
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_smb_source_with_allow_empty_password_opt_in_succeeds(
    client: AsyncClient,
):
    """Explicit opt-in via connection_config.allow_empty_password
    permits saving an empty-password SMB source."""
    r = await client.post(
        "/api/sources",
        json={
            "name": f"smb-src-anon-{uuid.uuid4().hex[:8]}",
            "type": "smb",
            "connection_config": {
                "host": "smb.example.com",
                "username": "alice",
                "share": "public",
                "allow_empty_password": True,
            },
        },
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_smb_source_with_inline_password_succeeds(client: AsyncClient):
    r = await client.post(
        "/api/sources",
        json={
            "name": f"smb-src-pw-{uuid.uuid4().hex[:8]}",
            "type": "smb",
            "connection_config": {
                "host": "smb.example.com",
                "username": "alice",
                "password": "s3cret",
                "share": "share1",
            },
        },
    )
    assert r.status_code == 201, r.text
