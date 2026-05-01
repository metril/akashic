"""Phase 8 — refresh-token rotation, replay detection, and logout.

The contract this enforces:

1. Login mints a refresh cookie. /api/auth/refresh returns a new
   access token and rotates the cookie.
2. Replaying an already-rotated token revokes the entire chain. Both
   the attacker and the legitimate user lose access.
3. /api/auth/logout revokes the chain idempotently.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth import refresh as refresh_service
from akashic.auth.passwords import hash_password
from akashic.models.refresh_token import RefreshToken
from akashic.models.user import User
from akashic.routers.auth import REFRESH_COOKIE


async def _seed_user(db: AsyncSession, *, role: str = "user") -> User:
    user = User(
        id=uuid.uuid4(), username=f"user_{uuid.uuid4().hex[:6]}",
        email="u@e", password_hash=hash_password("hunter2hunter2"), role=role,
    )
    db.add(user)
    await db.commit()
    return user


@pytest.mark.asyncio
async def test_login_sets_refresh_cookie(
    client: AsyncClient, db_session: AsyncSession,
):
    user = await _seed_user(db_session)
    resp = await client.post(
        "/api/users/login",
        json={"username": user.username, "password": "hunter2hunter2"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.cookies.get(REFRESH_COOKIE) is not None

    rows = (await db_session.execute(select(RefreshToken))).scalars().all()
    assert len(rows) == 1
    assert rows[0].user_id == user.id
    assert rows[0].revoked_at is None


@pytest.mark.asyncio
async def test_refresh_rotates_token(
    client: AsyncClient, db_session: AsyncSession,
):
    """Calling /refresh returns a new access JWT, sets a new cookie, and
    marks the old refresh row as `rotated`."""
    user = await _seed_user(db_session)

    login_resp = await client.post(
        "/api/users/login",
        json={"username": user.username, "password": "hunter2hunter2"},
    )
    cookie_v1 = login_resp.cookies.get(REFRESH_COOKIE)
    assert cookie_v1

    refresh_resp = await client.post(
        "/api/auth/refresh",
        cookies={REFRESH_COOKIE: cookie_v1},
    )
    assert refresh_resp.status_code == 200, refresh_resp.text
    body = refresh_resp.json()
    assert "access_token" in body
    cookie_v2 = refresh_resp.cookies.get(REFRESH_COOKIE)
    assert cookie_v2
    assert cookie_v2 != cookie_v1

    rows = (await db_session.execute(
        select(RefreshToken).order_by(RefreshToken.issued_at.asc())
    )).scalars().all()
    assert len(rows) == 2
    assert rows[0].chain_id == rows[1].chain_id
    assert rows[0].revoked_at is not None
    assert rows[0].revoke_reason == "rotated"
    assert rows[1].revoked_at is None


@pytest.mark.asyncio
async def test_refresh_replay_revokes_chain(
    client: AsyncClient, db_session: AsyncSession,
):
    """Presenting an already-rotated token must kill the whole chain.
    The legitimate session goes too — failing closed beats a replay
    silently extending an attacker's stolen cookie."""
    user = await _seed_user(db_session)

    login_resp = await client.post(
        "/api/users/login",
        json={"username": user.username, "password": "hunter2hunter2"},
    )
    cookie_v1 = login_resp.cookies.get(REFRESH_COOKIE)

    # First rotation succeeds.
    rot1 = await client.post(
        "/api/auth/refresh",
        cookies={REFRESH_COOKIE: cookie_v1},
    )
    assert rot1.status_code == 200

    # Second use of the original cookie — replay.
    replay = await client.post(
        "/api/auth/refresh",
        cookies={REFRESH_COOKIE: cookie_v1},
    )
    assert replay.status_code == 401

    # Both rows in the chain are now revoked.
    rows = (await db_session.execute(select(RefreshToken))).scalars().all()
    assert len(rows) == 2
    assert all(r.revoked_at is not None for r in rows)
    # At least one row is marked replayed.
    assert any(r.revoke_reason == "replayed" for r in rows)

    # The post-replay legitimate cookie is also dead.
    cookie_v2 = rot1.cookies.get(REFRESH_COOKIE)
    follow_up = await client.post(
        "/api/auth/refresh",
        cookies={REFRESH_COOKIE: cookie_v2},
    )
    assert follow_up.status_code == 401


@pytest.mark.asyncio
async def test_refresh_no_cookie_returns_401(client: AsyncClient):
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_token(
    client: AsyncClient, db_session: AsyncSession,
):
    user = await _seed_user(db_session)
    login_resp = await client.post(
        "/api/users/login",
        json={"username": user.username, "password": "hunter2hunter2"},
    )
    cookie = login_resp.cookies.get(REFRESH_COOKIE)

    logout_resp = await client.post(
        "/api/auth/logout",
        cookies={REFRESH_COOKIE: cookie},
    )
    assert logout_resp.status_code == 200

    # Token is now revoked — using it for refresh fails.
    refresh_resp = await client.post(
        "/api/auth/refresh",
        cookies={REFRESH_COOKIE: cookie},
    )
    assert refresh_resp.status_code == 401

    row = (await db_session.execute(
        select(RefreshToken).where(RefreshToken.user_id == user.id)
    )).scalar_one()
    assert row.revoked_at is not None
    assert row.revoke_reason == "logout"


@pytest.mark.asyncio
async def test_expired_refresh_token_rejected(db_session: AsyncSession):
    """Direct service-level test — bypasses HTTP because expiring a
    cookie via wall-clock isn't friendly to fast tests."""
    user = await _seed_user(db_session)

    # Mint, then back-date the row.
    plain, row = await refresh_service.mint(user.id, db_session)
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    result = await refresh_service.rotate(plain, db_session)
    assert result is None
