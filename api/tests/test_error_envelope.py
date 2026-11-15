import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from akashic.main import create_app


def _check(body, code):
    assert set(body) == {"code", "message", "detail", "request_id"}
    assert body["code"] == code and body["request_id"]


@pytest.mark.asyncio
async def test_unauthorized_envelope(client):
    r = await client.get("/api/v1/users/me")
    assert r.status_code == 401  # HTTPBearer rejects a missing header
    body = r.json()
    _check(body, "unauthorized")
    assert body["request_id"] == r.headers["x-request-id"]


@pytest.mark.asyncio
async def test_not_found_envelope(client):
    r = await client.get("/api/v1/nope")
    assert r.status_code == 404
    _check(r.json(), "not_found")
    assert r.json()["detail"] is None


@pytest.mark.asyncio
async def test_validation_envelope(client):
    r = await client.post("/api/v1/users/login", json={})
    assert r.status_code == 422
    body = r.json()
    _check(body, "validation_error")
    assert isinstance(body["detail"], list) and body["detail"][0]["loc"]


@pytest.mark.asyncio
async def test_http_exception_headers_preserved():
    from fastapi import HTTPException

    app = create_app()

    @app.get("/gated")
    async def gated():
        raise HTTPException(401, "nope", headers={"WWW-Authenticate": "Bearer"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/gated")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    _check(r.json(), "unauthorized")
    assert r.json()["message"] == "nope"


@pytest.mark.asyncio
async def test_unhandled_error_envelope_hides_internals():
    app = create_app()

    class Out(BaseModel):
        ok: bool

    @app.get("/boom", response_model=Out)
    async def boom():
        raise RuntimeError("secret internals")

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test",
    ) as ac:
        r = await ac.get("/boom")
    assert r.status_code == 500
    body = r.json()
    _check(body, "internal_error")
    assert "secret" not in r.text
    assert r.headers["x-request-id"] == body["request_id"]


@pytest.mark.asyncio
async def test_gzip_rejection_uses_envelope(client):
    r = await client.post(
        "/api/v1/users/login", content=b"not gzip",
        headers={"content-encoding": "gzip", "content-type": "application/json"},
    )
    assert r.status_code == 400
    _check(r.json(), "bad_request")


@pytest.mark.asyncio
async def test_docs_and_spec_are_public(client):
    docs = await client.get("/api/docs")
    assert docs.status_code == 200 and "swagger-ui-bundle.js" in docs.text
    assert "<script>" not in docs.text  # CSP is script-src 'self'
    assert (await client.get("/api/docs/assets/swagger-ui-bundle.js")).status_code == 200
    spec = await client.get("/api/v1/openapi.json")
    assert spec.status_code == 200 and spec.json()["openapi"].startswith("3.")
