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
