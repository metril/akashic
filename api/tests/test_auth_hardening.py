"""Auth hardening: SSO at_hash, LDAP empty pw, unique external ids,
refresh grace window, SSRF is_global, register race."""
import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from akashic.auth import oidc
from akashic.auth.passwords import hash_password
from akashic.services.runtime_settings import rt
from akashic.models.refresh_token import RefreshToken
from akashic.models.user import User
from akashic.routers.auth import REFRESH_COOKIE
from akashic.services.url_guard import UnsafeURL, validate_outbound_url


def _keypair(kid):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    j = jwk.construct(pub, algorithm="RS256").to_dict()
    j.update(kid=kid, use="sig", alg="RS256")
    return priv, j


@pytest.fixture(autouse=True)
def _clear():
    oidc.invalidate_cache()
    yield
    oidc.invalidate_cache()


def _claims():
    return {"sub": "u1", "iss": "https://idp.example", "aud": rt().oidc_client_id,
            "exp": int(time.time()) + 300, "nonce": "n"}


_DISCOVERY = {"token_endpoint": "https://idp.example/token", "issuer": "https://idp.example",
              "jwks_uri": "https://idp.example/jwks"}


@pytest.mark.asyncio
async def test_exchange_code_with_at_hash(monkeypatch, httpx_mock):
    priv, pub = _keypair("k1")
    monkeypatch.setattr(oidc, "_get_discovery", AsyncMock(return_value=_DISCOVERY))
    monkeypatch.setattr(oidc, "_get_jwks", AsyncMock(return_value={"keys": [pub]}))
    access = "access-token-value"
    tok = jwt.encode(_claims(), priv, algorithm="RS256", headers={"kid": "k1"}, access_token=access)
    httpx_mock.add_response(url="https://idp.example/token",
                            json={"id_token": tok, "access_token": access})
    claims = await oidc.exchange_code("c", nonce="n")
    assert "at_hash" in claims


@pytest.mark.asyncio
async def test_exchange_code_refetches_jwks_on_unknown_kid(monkeypatch, httpx_mock):
    priv_new, pub_new = _keypair("k2")
    _, pub_old = _keypair("k1")
    monkeypatch.setattr(oidc, "_get_discovery", AsyncMock(return_value=_DISCOVERY))
    oidc._jwks_cache = {"keys": [pub_old]}
    oidc._jwks_fetched_at = time.monotonic()
    tok = jwt.encode(_claims(), priv_new, algorithm="RS256", headers={"kid": "k2"})
    httpx_mock.add_response(url="https://idp.example/token", json={"id_token": tok, "access_token": "a"})
    httpx_mock.add_response(url="https://idp.example/jwks", json={"keys": [pub_new]})
    claims = await oidc.exchange_code("c", nonce="n")
    assert claims["sub"] == "u1"


def test_ldap_rejects_empty_password(monkeypatch):
    from akashic.auth import ldap as ldap_mod
    init = MagicMock()
    monkeypatch.setattr(ldap_mod.ldap, "initialize", init)
    for pw in ("", "   "):
        assert ldap_mod.authenticate_ldap("bob", pw) is None
    init.assert_not_called()


@pytest.mark.asyncio
async def test_ldap_login_empty_password_422(client):
    r = await client.post("/api/v1/auth/ldap/login", json={"username": "bob", "password": ""})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_unique_provider_external_id(db_session):
    db_session.add(User(username="a", auth_provider="oidc", external_id="x"))
    await db_session.commit()
    db_session.add(User(username="b", auth_provider="oidc", external_id="x"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_oidc_provision_recovers_from_race(db_session, monkeypatch):
    """Concurrent first logins: the insert loses the unique race and the
    existing row is returned."""
    existing = User(username="winner", auth_provider="oidc", external_id="sub9", role="viewer")
    db_session.add(existing)
    await db_session.commit()
    existing_id = existing.id

    real_execute = db_session.execute
    calls = {"n": 0}

    async def fake_execute(stmt, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:  # initial lookup misses (race)
            r = MagicMock()
            r.scalar_one_or_none.return_value = None
            return r
        return await real_execute(stmt, *a, **kw)

    monkeypatch.setattr(db_session, "execute", fake_execute)
    user = await oidc.get_or_create_user(db_session, {"sub": "sub9", "preferred_username": "loser"})
    assert user.id == existing_id


async def _seed_login(client, db):
    u = User(id=uuid.uuid4(), username=f"u{uuid.uuid4().hex[:6]}",
             password_hash=hash_password("hunter2hunter2"), role="user")
    db.add(u)
    await db.commit()
    r = await client.post("/api/v1/users/login", json={"username": u.username, "password": "hunter2hunter2"})
    return r.cookies.get(REFRESH_COOKIE)


@pytest.mark.asyncio
async def test_refresh_grace_window_allows_recent_rotated_token(client, db_session):
    c1 = await _seed_login(client, db_session)
    r1 = await client.post("/api/v1/auth/refresh", cookies={REFRESH_COOKIE: c1})
    assert r1.status_code == 200
    # Second tab presents the same (just rotated) token: allowed, chain survives.
    r2 = await client.post("/api/v1/auth/refresh", cookies={REFRESH_COOKIE: c1})
    assert r2.status_code == 200
    c3 = r2.cookies.get(REFRESH_COOKIE)
    r3 = await client.post("/api/v1/auth/refresh", cookies={REFRESH_COOKIE: c3})
    assert r3.status_code == 200


@pytest.mark.asyncio
async def test_refresh_replay_after_grace_revokes_chain(client, db_session):
    c1 = await _seed_login(client, db_session)
    r1 = await client.post("/api/v1/auth/refresh", cookies={REFRESH_COOKIE: c1})
    assert r1.status_code == 200
    rows = (await db_session.execute(
        select(RefreshToken).where(RefreshToken.revoke_reason == "rotated"))).scalars().all()
    for row in rows:
        row.revoked_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    await db_session.commit()
    r2 = await client.post("/api/v1/auth/refresh", cookies={REFRESH_COOKIE: c1})
    assert r2.status_code == 401


@pytest.mark.parametrize("url", [
    "http://100.64.0.1/", "http://192.0.0.1/", "http://198.18.0.1/",
    "http://[::ffff:127.0.0.1]/", "http://[::ffff:10.0.0.1]/", "http://[64:ff9b::1]/",
])
def test_non_global_ips_blocked(url):
    with pytest.raises(UnsafeURL):
        validate_outbound_url(url)


def test_schema_does_no_dns():
    import socket
    from unittest import mock
    from akashic.schemas.webhook import WebhookCreate

    def boom(*a, **k):
        raise AssertionError("sync DNS in schema")
    # Scoped patch: autouse DB fixtures tear down (and connect) after the test body.
    with mock.patch.object(socket, "getaddrinfo", boom):
        WebhookCreate(event_type="x", url="https://hooks.example.com/x", secret="s")


@pytest.mark.asyncio
async def test_webhook_route_rejects_private_dns(client, monkeypatch):
    await client.post("/api/v1/users/register", json={"username": "alice", "password": "testpass123"})
    login = await client.post("/api/v1/users/login", json={"username": "alice", "password": "testpass123"})
    tok = login.json()["access_token"]

    loop = asyncio.get_running_loop()
    real = loop.getaddrinfo

    async def fake_getaddrinfo(host, port, *a, **k):
        if host == "evil.example.com":
            return [(2, 1, 6, "", ("10.0.0.5", 0))]
        return await real(host, port, *a, **k)
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    r = await client.post("/api/v1/webhooks", headers={"Authorization": f"Bearer {tok}"},
                          json={"event_type": "scan_completed", "url": "https://evil.example.com/h",
                                "secret": "s"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_register_uses_advisory_lock(client, db_session):
    from sqlalchemy import event
    stmts = []
    bind = db_session.get_bind()
    eng = getattr(bind, "sync_engine", bind)

    def grab(conn, cursor, statement, *a):
        stmts.append(statement)
    event.listen(eng, "before_cursor_execute", grab)
    try:
        r = await client.post("/api/v1/users/register", json={"username": "first", "password": "testpass123"})
    finally:
        event.remove(eng, "before_cursor_execute", grab)
    assert r.status_code == 201
    assert any("pg_advisory_xact_lock" in s for s in stmts)


@pytest.mark.asyncio
async def test_refresh_grace_is_single_use(client, db_session):
    c1 = await _seed_login(client, db_session)
    assert (await client.post("/api/v1/auth/refresh", cookies={REFRESH_COOKIE: c1})).status_code == 200
    assert (await client.post("/api/v1/auth/refresh", cookies={REFRESH_COOKIE: c1})).status_code == 200  # grace
    # Second reuse of the same rotated token: replay -> 401, chain revoked.
    assert (await client.post("/api/v1/auth/refresh", cookies={REFRESH_COOKIE: c1})).status_code == 401


def _hide_username_check_once(db_session, monkeypatch):
    real_execute = db_session.execute
    calls = {"n": 0}

    async def fake_execute(stmt, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:  # username-availability check misses (race)
            r = MagicMock()
            r.scalar_one_or_none.return_value = None
            return r
        return await real_execute(stmt, *a, **kw)

    monkeypatch.setattr(db_session, "execute", fake_execute)


@pytest.mark.asyncio
async def test_oidc_provision_retries_on_username_collision(db_session, monkeypatch):
    db_session.add(User(username="taken", auth_provider="local", role="viewer"))
    await db_session.commit()
    _hide_username_check_once(db_session, monkeypatch)
    user = await oidc.get_or_create_user(db_session, {"sub": "s-new", "preferred_username": "taken"})
    assert user.external_id == "s-new" and user.username == "taken_1"


@pytest.mark.asyncio
async def test_ldap_provision_retries_on_username_collision(db_session, monkeypatch):
    from akashic.auth import ldap as ldap_auth

    db_session.add(User(username="taken", auth_provider="local", role="viewer"))
    await db_session.commit()
    _hide_username_check_once(db_session, monkeypatch)
    user = await ldap_auth.get_or_create_user(db_session, {"dn": "cn=x", "uid": "taken"})
    assert user.external_id == "cn=x" and user.username == "taken_1"
