"""v0.31.3 — SMB sources authenticating via a credential profile.

`_validate_smb_password_requirement` used to read a profile's plaintext
`credentials` column, which is NULL for every modern (v0.29.5+)
profile — its credentials live encrypted in `credentials_encrypted`.
So attaching a perfectly valid password-carrying profile to an SMB
source was rejected with "merged connection_config has no non-empty
password". These tests pin the decrypt-aware behaviour.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.credential_profile import CredentialProfile
from akashic.models.user import User
from akashic.services.credential_crypto import encrypt_credentials


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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _make_profile(setup_db, creds: dict) -> uuid.UUID:
    """Insert an SMB credential profile whose credentials are stored
    encrypted (the modern v0.29.5+ shape)."""
    pid = uuid.uuid4()
    async with setup_db() as db:
        db.add(CredentialProfile(
            id=pid, name=f"prof-{pid.hex[:6]}", type="smb",
            credentials_encrypted=encrypt_credentials(creds),
        ))
        await db.commit()
    return pid


@pytest.mark.asyncio
async def test_create_smb_source_with_encrypted_profile_password(
    client: AsyncClient, setup_db,
):
    """An SMB source with no inline password is accepted when its
    credential profile carries one — the validator must decrypt
    `credentials_encrypted`, not read the empty `credentials` column."""
    pid = await _make_profile(setup_db, {"username": "svc", "password": "prof-pw-1"})

    r = await client.post("/api/sources", json={
        "name": "smb-via-profile",
        "type": "smb",
        "connection_config": {"host": "fileserver", "username": "svc", "share": "media"},
        "credential_profile_id": str(pid),
    })
    assert r.status_code == 201, r.text
    assert r.json()["credential_profile_id"] == str(pid)


@pytest.mark.asyncio
async def test_patch_smb_source_with_profile_passes_password_validation(
    client: AsyncClient, setup_db,
):
    """The update path runs the same SMB-password validator. Editing an
    unrelated field on a profile-backed SMB source must not fail it."""
    pid = await _make_profile(setup_db, {"username": "svc", "password": "prof-pw-2"})
    create = await client.post("/api/sources", json={
        "name": "smb-edit-me",
        "type": "smb",
        "connection_config": {"host": "fs2", "username": "svc", "share": "docs"},
        "credential_profile_id": str(pid),
    })
    assert create.status_code == 201, create.text
    sid = create.json()["id"]

    patch = await client.patch(
        f"/api/sources/{sid}", json={"max_parallel_scanners": 3},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["max_parallel_scanners"] == 3


@pytest.mark.asyncio
async def test_create_smb_source_profile_without_password_still_rejected(
    client: AsyncClient, setup_db,
):
    """Regression guard: a profile that genuinely carries no password
    must still be rejected — the fix decrypts, it doesn't fail open."""
    pid = await _make_profile(setup_db, {"username": "svc"})  # no password

    r = await client.post("/api/sources", json={
        "name": "smb-no-pw",
        "type": "smb",
        "connection_config": {"host": "fs3", "username": "svc", "share": "x"},
        "credential_profile_id": str(pid),
    })
    assert r.status_code == 422, r.text
    assert "password" in r.json()["detail"].lower()
