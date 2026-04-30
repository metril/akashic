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
