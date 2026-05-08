"""Scanner JWT replay protection (review I7).

Two angles covered:
1. Unit test of claim_jti's TTL math + missing-jti behaviour with a
   stubbed Redis client.
2. Endpoint-level replay test against /api/scans/lease — the second
   request with the same jti must 401.
"""
import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.database import get_db
from akashic.main import create_app
from akashic.models.user import User
from akashic.services import scanner_jti
from akashic.services.scanner_keys import sign_jwt


@pytest_asyncio.fixture
async def admin_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(), username="admin-jti", email="a@jti.test",
            password_hash="x", role="admin",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


# ───── unit tests for claim_jti ─────


@pytest.mark.asyncio
async def test_claim_jti_first_seen_returns_true():
    fake = AsyncMock()
    fake.set = AsyncMock(return_value=True)  # NX succeeded
    with patch.object(scanner_jti, "_client", lambda: fake):
        ok = await scanner_jti.claim_jti("scanner-1", "jti-abc", int(time.time()) + 300)
    assert ok is True
    fake.set.assert_awaited_once()
    args, kwargs = fake.set.call_args
    assert kwargs["nx"] is True
    assert kwargs["ex"] >= 60  # TTL floor


@pytest.mark.asyncio
async def test_claim_jti_replay_returns_false():
    fake = AsyncMock()
    fake.set = AsyncMock(return_value=None)  # NX failed (key existed)
    with patch.object(scanner_jti, "_client", lambda: fake):
        ok = await scanner_jti.claim_jti("scanner-1", "jti-abc", int(time.time()) + 300)
    assert ok is False


@pytest.mark.asyncio
async def test_claim_jti_missing_jti_fails_open():
    """Tokens minted before the jti rollout (no jti claim) are allowed
    through — this is the back-compat branch that lets a mid-deploy
    scanner keep working."""
    fake = AsyncMock()
    with patch.object(scanner_jti, "_client", lambda: fake):
        ok = await scanner_jti.claim_jti("scanner-1", "", int(time.time()) + 300)
    assert ok is True
    fake.set.assert_not_called()


@pytest.mark.asyncio
async def test_claim_jti_redis_failure_fails_open():
    fake = AsyncMock()
    fake.set = AsyncMock(side_effect=ConnectionError("redis down"))
    with patch.object(scanner_jti, "_client", lambda: fake):
        ok = await scanner_jti.claim_jti("scanner-1", "jti-abc", int(time.time()) + 300)
    assert ok is True


@pytest.mark.asyncio
async def test_claim_jti_ttl_floor():
    """Near-expiry tokens still get a 60s minimum TTL so a replay
    arriving in the last second is still blocked."""
    fake = AsyncMock()
    fake.set = AsyncMock(return_value=True)
    with patch.object(scanner_jti, "_client", lambda: fake):
        await scanner_jti.claim_jti("scanner-1", "jti-near", int(time.time()) + 1)
    _, kwargs = fake.set.call_args
    assert kwargs["ex"] >= 60


# ───── endpoint-level replay test ─────


@pytest.mark.asyncio
async def test_scanner_jwt_replay_returns_401(setup_db, admin_user):
    """End-to-end: mint one JWT, present it twice. First call must
    pass; second call must 401 with `token replay detected`."""

    async def _override_get_db():
        async with setup_db() as session:
            yield session

    # In-memory dict-backed redis stub — survives across the two
    # client calls in the test.
    seen: dict[str, str] = {}

    async def _fake_set(key, value, *, nx=False, ex=None):
        if nx and key in seen:
            return None
        seen[key] = value
        return True

    fake = AsyncMock()
    fake.set = AsyncMock(side_effect=_fake_set)

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db

    # Need an admin client to register a scanner first.
    from akashic.auth.dependencies import get_current_user

    async def _override_admin():
        return admin_user

    admin_app = create_app()
    admin_app.dependency_overrides[get_db] = _override_get_db
    admin_app.dependency_overrides[get_current_user] = _override_admin

    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://t") as ac:
        scn = (await ac.post(
            "/api/scanners",
            json={"name": f"s-{uuid.uuid4().hex[:6]}", "pool": "default"},
        )).json()

    sid = scn["id"]
    priv = scn["private_key_pem"]
    now = int(time.time())
    jti = uuid.uuid4().hex
    token = sign_jwt(
        priv,
        {"iss": "scanner", "sub": sid, "iat": now, "exp": now + 300, "jti": jti},
        headers={"kid": sid},
    )

    with patch.object(scanner_jti, "_client", lambda: fake):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            r1 = await ac.post(
                "/api/scans/lease", json={},
                headers={"Authorization": f"Bearer {token}"},
            )
            r2 = await ac.post(
                "/api/scans/lease", json={},
                headers={"Authorization": f"Bearer {token}"},
            )

    # First call: 200 (no scan available) or 204 — auth succeeded.
    assert r1.status_code in (200, 204), r1.text
    # Second call: 401 with replay detail.
    assert r2.status_code == 401
    assert "replay" in r2.json()["detail"].lower()
