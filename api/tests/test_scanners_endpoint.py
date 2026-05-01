"""Scanner CRUD + handshake endpoint coverage."""
from __future__ import annotations

import time
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.user import User
from akashic.protocol import PROTOCOL_VERSION
from akashic.services.scanner_keys import sign_jwt


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


def _build_admin_client(setup_db, user: User) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_get_current_user():
        return user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _build_unauth_client(setup_db) -> AsyncClient:
    """No auth override — the api enforces its real auth (we'll use
    scanner JWTs in the Authorization header)."""
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _scanner_token(scanner_id: str, private_pem: str, exp_offset: int = 300) -> str:
    now = int(time.time())
    return sign_jwt(
        private_pem,
        {
            "iss": "scanner",
            "sub": scanner_id,
            "iat": now,
            "exp": now + exp_offset,
        },
        headers={"kid": scanner_id},
    )


@pytest_asyncio.fixture
async def admin_client(setup_db, admin_user: User):
    async with _build_admin_client(setup_db, admin_user) as ac:
        yield ac


@pytest.mark.asyncio
async def test_create_scanner_returns_keypair_once(admin_client: AsyncClient):
    r = await admin_client.post(
        "/api/scanners",
        json={"name": "amsterdam-1", "pool": "site-amsterdam"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "amsterdam-1"
    assert body["pool"] == "site-amsterdam"
    assert body["protocol_version"] == PROTOCOL_VERSION
    assert "PRIVATE KEY" in body["private_key_pem"]
    assert "PUBLIC KEY" in body["public_key_pem"]
    assert len(body["key_fingerprint"]) == 64

    # Listing doesn't expose the private key.
    listed = (await admin_client.get("/api/scanners")).json()
    assert any(s["id"] == body["id"] for s in listed)
    for s in listed:
        assert "private_key_pem" not in s


@pytest.mark.asyncio
async def test_create_scanner_rejects_duplicate_name(admin_client: AsyncClient):
    await admin_client.post("/api/scanners", json={"name": "dup", "pool": "default"})
    r = await admin_client.post("/api/scanners", json={"name": "dup", "pool": "default"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_admin_endpoints_forbidden_for_non_admin(setup_db):
    async with setup_db() as session:
        viewer = User(
            id=uuid.uuid4(), username="viewer",
            email="v@b.c", password_hash="x", role="viewer",
        )
        session.add(viewer)
        await session.commit()
    async with _build_admin_client(setup_db, viewer) as ac:
        r = await ac.post("/api/scanners", json={"name": "no", "pool": "default"})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_handshake_accepts_in_range_protocol(setup_db, admin_client: AsyncClient):
    create = await admin_client.post(
        "/api/scanners", json={"name": "s1", "pool": "default"},
    )
    body = create.json()
    sid = body["id"]
    token = _scanner_token(sid, body["private_key_pem"])

    async with _build_unauth_client(setup_db) as ac:
        r = await ac.post(
            "/api/scanners/handshake",
            json={
                "protocol_version": PROTOCOL_VERSION,
                "version": "0.2.0",
                "hostname": "scanner-01",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] is True
    assert r.json()["server_protocol_version"] == PROTOCOL_VERSION


@pytest.mark.asyncio
async def test_handshake_rejects_out_of_range_protocol(
    setup_db, admin_client: AsyncClient,
):
    create = await admin_client.post(
        "/api/scanners", json={"name": "s1", "pool": "default"},
    )
    body = create.json()
    token = _scanner_token(body["id"], body["private_key_pem"])

    async with _build_unauth_client(setup_db) as ac:
        r = await ac.post(
            "/api/scanners/handshake",
            json={"protocol_version": 999, "version": "future", "hostname": None},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 426  # Upgrade Required
    assert r.json()["accepted"] is False


@pytest.mark.asyncio
async def test_rotate_replaces_keypair(setup_db, admin_client: AsyncClient):
    """Rotating mints a new key and the OLD key stops authenticating
    immediately — the JWT verification path looks up the current
    public_key_pem on every request, so the old private key's
    signatures no longer verify."""
    create = await admin_client.post(
        "/api/scanners", json={"name": "s1", "pool": "default"},
    )
    body = create.json()
    sid = body["id"]
    old_token = _scanner_token(sid, body["private_key_pem"])

    rotate = await admin_client.post(f"/api/scanners/{sid}/rotate")
    assert rotate.status_code == 200
    new_body = rotate.json()
    assert new_body["key_fingerprint"] != body["key_fingerprint"]
    new_token = _scanner_token(sid, new_body["private_key_pem"])

    async with _build_unauth_client(setup_db) as ac:
        # Old token: 401 (signature no longer matches the rotated key).
        r_old = await ac.post(
            "/api/scanners/handshake",
            json={"protocol_version": PROTOCOL_VERSION},
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert r_old.status_code == 401
        # New token: works.
        r_new = await ac.post(
            "/api/scanners/handshake",
            json={"protocol_version": PROTOCOL_VERSION},
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert r_new.status_code == 200


@pytest.mark.asyncio
async def test_disabled_scanner_cannot_authenticate(setup_db, admin_client: AsyncClient):
    create = await admin_client.post(
        "/api/scanners", json={"name": "s1", "pool": "default"},
    )
    body = create.json()
    sid = body["id"]
    token = _scanner_token(sid, body["private_key_pem"])

    await admin_client.patch(f"/api/scanners/{sid}", json={"enabled": False})

    async with _build_unauth_client(setup_db) as ac:
        r = await ac.post(
            "/api/scanners/handshake",
            json={"protocol_version": PROTOCOL_VERSION},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_expired_token_is_rejected(setup_db, admin_client: AsyncClient):
    create = await admin_client.post(
        "/api/scanners", json={"name": "s1", "pool": "default"},
    )
    body = create.json()
    # exp_offset=-300 → expired 5 minutes ago.
    token = _scanner_token(body["id"], body["private_key_pem"], exp_offset=-300)
    async with _build_unauth_client(setup_db) as ac:
        r = await ac.post(
            "/api/scanners/handshake",
            json={"protocol_version": PROTOCOL_VERSION},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401
