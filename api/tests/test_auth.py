import pytest


@pytest.mark.asyncio
async def test_register_user(client):
    response = await client.post("/api/users/register", json={
        "username": "testuser",
        "password": "testpass123",
        "email": "test@example.com",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "testuser"
    assert "id" in data
    assert "password_hash" not in data


@pytest.mark.asyncio
async def test_login(client):
    await client.post("/api/users/register", json={
        "username": "loginuser",
        "password": "testpass123",
    })
    response = await client.post("/api/users/login", json={
        "username": "loginuser",
        "password": "testpass123",
    })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_register_rejects_oversize_password(client):
    """bcrypt-72-byte limit is enforced at the schema layer."""
    response = await client.post("/api/users/register", json={
        "username": "fatpw",
        "password": "a" * 73,
    })
    assert response.status_code == 422
    body = response.json()
    assert any("72-byte" in str(e.get("ctx", "")) or "72-byte" in str(e.get("msg", ""))
               for e in body.get("detail", []))


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    await client.post("/api/users/register", json={
        "username": "wrongpw",
        "password": "testpass123",
    })
    response = await client.post("/api/users/login", json={
        "username": "wrongpw",
        "password": "wrongpass",
    })
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_endpoint_requires_auth(client):
    response = await client.get("/api/users/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_endpoint_with_token(client):
    await client.post("/api/users/register", json={
        "username": "authed",
        "password": "testpass123",
    })
    login = await client.post("/api/users/login", json={
        "username": "authed",
        "password": "testpass123",
    })
    token = login.json()["access_token"]
    response = await client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["username"] == "authed"


# ── /api/auth/providers — bootstrap detection ───────────────────────────────


@pytest.mark.asyncio
async def test_providers_setup_required_on_empty_db(client):
    """Fresh deployment: zero users → setup_required=True so the web
    login page can flip into 'create the admin account' mode."""
    response = await client.get("/api/auth/providers")
    assert response.status_code == 200
    body = response.json()
    assert body["local"] is True
    assert body["setup_required"] is True
    # OIDC/LDAP keys are present even when disabled — UI relies on them.
    assert "oidc" in body
    assert "ldap" in body


@pytest.mark.asyncio
async def test_providers_setup_required_flips_after_first_user(client):
    """The flag flips False the moment any user exists, mirroring the
    one-way door enforced by POST /api/users/register."""
    register = await client.post("/api/users/register", json={
        "username": "firstadmin",
        "password": "testpass123",
        "email": "admin@local",
    })
    assert register.status_code == 201
    response = await client.get("/api/auth/providers")
    assert response.status_code == 200
    assert response.json()["setup_required"] is False


# ---------------------------------------------------------------------------
# OIDC callback (browser SSO redirect flow)
# ---------------------------------------------------------------------------

from unittest.mock import AsyncMock  # noqa: E402

_CLAIMS = {"sub": "oidc-1", "preferred_username": "ssouser", "email": "sso@example.com"}


@pytest.fixture
def oidc_on(monkeypatch):
    from akashic.config import settings
    monkeypatch.setattr(settings, "oidc_enabled", True)
    monkeypatch.setattr(settings, "frontend_url", "")
    # httpx will not send a Secure cookie to http://test
    monkeypatch.setattr(settings, "cookie_secure", False)
    mock = AsyncMock(return_value=_CLAIMS)
    monkeypatch.setattr("akashic.auth.oidc.exchange_code", mock)
    return mock


@pytest.mark.asyncio
async def test_oidc_callback_success(client, oidc_on):
    client.cookies.set("oidc_state", "s")
    r = await client.get("/api/auth/oidc/callback?code=c&state=s", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/auth/callback"
    cookies = r.headers.get_list("set-cookie")
    refresh = [c for c in cookies if c.startswith("akashic_refresh=")]
    assert refresh and "Path=/api/auth" in refresh[0]
    assert "access_token" not in r.text
    r2 = await client.post("/api/auth/refresh")
    assert r2.status_code == 200
    assert "access_token" in r2.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("cookie,url", [
    ("other", "/api/auth/oidc/callback?code=c&state=s"),
    ("s", "/api/auth/oidc/callback?state=s"),
])
async def test_oidc_callback_bad_state(client, oidc_on, cookie, url):
    client.cookies.set("oidc_state", cookie)
    r = await client.get(url, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login?error=sso_state"
    assert not any(c.startswith("akashic_refresh=") for c in r.headers.get_list("set-cookie"))


@pytest.mark.asyncio
async def test_oidc_callback_idp_error(client, oidc_on):
    client.cookies.set("oidc_state", "s")
    r = await client.get("/api/auth/oidc/callback?error=access_denied&state=s", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login?error=sso_denied"


@pytest.mark.asyncio
async def test_oidc_callback_exchange_fails(client, oidc_on):
    oidc_on.side_effect = RuntimeError("boom")
    client.cookies.set("oidc_state", "s")
    r = await client.get("/api/auth/oidc/callback?code=c&state=s", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login?error=sso_failed"


@pytest.mark.asyncio
async def test_oidc_callback_frontend_url(client, oidc_on, monkeypatch):
    from akashic.config import settings
    monkeypatch.setattr(settings, "frontend_url", "http://localhost:5173")
    client.cookies.set("oidc_state", "s")
    r = await client.get("/api/auth/oidc/callback?code=c&state=s", follow_redirects=False)
    assert r.headers["location"] == "http://localhost:5173/auth/callback"
