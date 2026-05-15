"""Credential profiles CRUD + reference protection (v0.5.9).

Covers:
- POST/GET/PATCH/DELETE happy paths
- Secret scrubbing on response
- Sentinel-aware update preserves stored secrets
- Type mismatch on assign returns 400
- Delete-while-referenced returns 409
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from akashic.auth.dependencies import get_current_user, require_admin
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.credential_profile import CredentialProfile
from akashic.models.user import User


@pytest_asyncio.fixture
async def admin_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(),
            username="admin",
            email="admin@example.com",
            password_hash="x",
            role="admin",
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
async def test_create_profile_scrubs_secrets_on_response(client: AsyncClient):
    r = await client.post(
        "/api/credential-profiles",
        json={
            "name": "smb-prod",
            "type": "smb",
            "credentials": {"username": "deploy", "password": "shhh"},
            "description": "production SMB",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "smb-prod"
    assert body["type"] == "smb"
    # Secret-scrubbed response — username passes through, password masked.
    assert body["credentials"]["username"] == "deploy"
    assert body["credentials"]["password"] == "***"


@pytest.mark.asyncio
async def test_create_unsupported_type_returns_400(client: AsyncClient):
    r = await client.post(
        "/api/credential-profiles",
        json={"name": "x", "type": "ftp", "credentials": {}},
    )
    assert r.status_code == 400
    assert "Unsupported type" in r.json()["detail"]


@pytest.mark.asyncio
async def test_duplicate_name_returns_409(client: AsyncClient):
    # v0.29.5 — SMB profiles require a non-empty password (see
    # schemas/credential_profile.py:assert_smb_has_password). Both
    # payloads now carry one.
    r = await client.post(
        "/api/credential-profiles",
        json={"name": "dup", "type": "smb", "credentials": {"username": "u", "password": "p1"}},
    )
    assert r.status_code == 201
    r2 = await client.post(
        "/api/credential-profiles",
        json={"name": "dup", "type": "smb", "credentials": {"username": "v", "password": "p2"}},
    )
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_list_filters_by_type(client: AsyncClient):
    # SMB requires a password (v0.29.5); NFS profile contents are
    # unconstrained here.
    await client.post(
        "/api/credential-profiles",
        json={"name": "smb-1", "type": "smb", "credentials": {"username": "u", "password": "p"}},
    )
    await client.post(
        "/api/credential-profiles",
        json={"name": "nfs-1", "type": "nfs", "credentials": {}},
    )
    r = await client.get("/api/credential-profiles?type=smb")
    assert r.status_code == 200
    body = r.json()
    assert all(p["type"] == "smb" for p in body)
    assert any(p["name"] == "smb-1" for p in body)


@pytest.mark.asyncio
async def test_sentinel_update_preserves_stored_secret(
    client: AsyncClient, setup_db,
):
    r = await client.post(
        "/api/credential-profiles",
        json={
            "name": "rotate-me",
            "type": "smb",
            "credentials": {"username": "u", "password": "real-secret"},
        },
    )
    profile_id = r.json()["id"]

    # Update with the masked sentinel — password should NOT be replaced
    # by "***" in storage.
    r = await client.patch(
        f"/api/credential-profiles/{profile_id}",
        json={"credentials": {"password": "***", "username": "newer"}},
    )
    assert r.status_code == 200, r.text

    async with setup_db() as session:
        stored = (await session.execute(
            select(CredentialProfile).where(CredentialProfile.id == uuid.UUID(profile_id))
        )).scalar_one()
    # v0.29.5 — credentials are now stored encrypted-at-rest.
    # `stored.credentials` is NULL post-write; decrypt the ciphertext
    # column to assert against the stored values.
    from akashic.services.credential_crypto import decrypt_credentials
    plain = decrypt_credentials(bytes(stored.credentials_encrypted))
    assert plain["password"] == "real-secret"
    assert plain["username"] == "newer"


@pytest.mark.asyncio
async def test_assign_mismatched_profile_to_host_returns_400(
    client: AsyncClient,
):
    rp = await client.post(
        "/api/credential-profiles",
        json={"name": "nfs-only", "type": "nfs", "credentials": {"username": "u"}},
    )
    profile_id = rp.json()["id"]
    rh = await client.post(
        "/api/hosts",
        json={
            "name": "smb-host",
            "type": "smb",
            "connection_config": {
                "host": "h", "username": "u",
            },
            "credential_profile_id": profile_id,
        },
    )
    assert rh.status_code == 400
    assert "type" in rh.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_while_referenced_returns_409(client: AsyncClient):
    rp = await client.post(
        "/api/credential-profiles",
        json={"name": "smb-attached", "type": "smb", "credentials": {"username": "u", "password": "p"}},
    )
    profile_id = rp.json()["id"]
    rh = await client.post(
        "/api/hosts",
        json={
            "name": "smb-host-2",
            "type": "smb",
            "connection_config": {
                "host": "h", "username": "u",
            },
            "credential_profile_id": profile_id,
        },
    )
    assert rh.status_code == 201

    rd = await client.delete(f"/api/credential-profiles/{profile_id}")
    assert rd.status_code == 409
    assert "host" in rd.json()["detail"].lower()
