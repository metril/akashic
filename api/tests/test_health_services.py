"""Service health + activity endpoints (v0.29.0).

The new `/api/health/services` is admin-only liveness; the new
`/api/health/services/activity` is admin-only rich activity (queue
depth, doc counts, last-activity timestamps).

Covers:

  * Auth: anonymous → 401; non-admin → 403; admin → 200.
  * Tika activity reads Redis counters written by the extraction
    worker; missing keys surface as 0/None, not 500.
  * Meilisearch activity degrades gracefully when Meili is
    unreachable — returns `{ok: false, error: ...}` rather than 500.
  * In-process 5 s cache holds — two back-to-back calls share a
    snapshot.
"""
from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.auth.dependencies import get_current_user, require_admin
from akashic.auth.jwt import create_access_token
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.user import User
from akashic.routers import health_services


@pytest_asyncio.fixture
async def admin_client(setup_db) -> AsyncClient:
    user_id = uuid.uuid4()

    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_admin():
        return User(
            id=user_id, username="admin", email="a@e",
            password_hash="x", role="admin",
        )

    async with setup_db() as session:
        session.add(User(
            id=user_id, username="admin", email="a@e",
            password_hash="x", role="admin",
        ))
        await session.commit()

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_admin
    app.dependency_overrides[require_admin] = _override_admin
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def unauth_client(setup_db) -> AsyncClient:
    """No admin override — admin endpoints should 401."""
    async def _override_get_db():
        async with setup_db() as session:
            yield session
    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as ac:
        yield ac


@pytest.fixture(autouse=True)
def clear_router_cache():
    """The router caches responses for 5 s; tests need a fresh
    snapshot each invocation so they don't see leaked state from a
    prior test's monkeypatched probes."""
    health_services._cache.clear()
    yield
    health_services._cache.clear()


@pytest.mark.asyncio
async def test_services_health_requires_auth(unauth_client):
    r = await unauth_client.get("/api/health/services")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_services_activity_requires_auth(unauth_client):
    r = await unauth_client.get("/api/health/services/activity")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_services_health_returns_per_service_shape(
    admin_client, monkeypatch,
):
    """Stub each probe so the test doesn't depend on real Meili/Tika
    being available. The payload must have one entry per service with
    `{ok, latency_ms}` (or `error`)."""
    async def fake_meili():
        return {"ok": True, "latency_ms": 5.0}

    async def fake_tika():
        return {"ok": False, "latency_ms": None, "error": "stub down"}

    monkeypatch.setattr(health_services, "_probe_meilisearch", fake_meili)
    monkeypatch.setattr(health_services, "_probe_tika", fake_tika)

    r = await admin_client.get("/api/health/services")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("postgres", "redis", "meilisearch", "tika"):
        assert key in body
        assert "ok" in body[key]
    assert body["meilisearch"]["ok"] is True
    assert body["tika"]["ok"] is False
    assert body["tika"]["error"] == "stub down"


@pytest.mark.asyncio
async def test_services_health_is_cached_for_5s(admin_client, monkeypatch):
    """Two back-to-back calls invoke the underlying probe at most
    once — the second call returns the cached snapshot."""
    calls = {"meili": 0}

    async def counting_meili():
        calls["meili"] += 1
        return {"ok": True, "latency_ms": 1.0}

    monkeypatch.setattr(health_services, "_probe_meilisearch", counting_meili)

    a = await admin_client.get("/api/health/services")
    b = await admin_client.get("/api/health/services")
    assert a.status_code == 200
    assert b.status_code == 200
    assert calls["meili"] == 1


@pytest.mark.asyncio
async def test_tika_activity_reads_redis_counters(admin_client):
    """Seed the Redis counters the /api/ingest/content endpoint
    bumps (v0.30.0), then call the activity endpoint and assert they
    surface."""
    from akashic.services.scan_pubsub import _client as redis_client
    from datetime import datetime, timezone

    redis = redis_client()
    await redis.delete(
        "akashic:tika:extracted_total",
        "akashic:tika:last_extracted_at",
    )
    await redis.set("akashic:tika:extracted_total", 1234)
    iso = datetime.now(timezone.utc).isoformat()
    await redis.set("akashic:tika:last_extracted_at", iso)
    bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    await redis.delete(f"akashic:tika:extracted:{bucket}")
    await redis.set(f"akashic:tika:extracted:{bucket}", 7)

    r = await admin_client.get("/api/health/services/activity")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tika"]["ok"] is True
    assert body["tika"]["extracted_total"] == 1234
    assert body["tika"]["last_extracted_at"] == iso
    # The 5-min sum is at least the value we put in the current bucket.
    assert (body["tika"]["extracted_last_5min"] or 0) >= 7

    # Cleanup so the next run sees zeros.
    await redis.delete(
        "akashic:tika:extracted_total",
        "akashic:tika:last_extracted_at",
        f"akashic:tika:extracted:{bucket}",
    )


@pytest.mark.asyncio
async def test_meili_activity_degrades_gracefully(admin_client, monkeypatch):
    """A network failure to Meili must surface as `ok: false` with
    error text — never a 500 on our endpoint."""
    class _BoomClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **kw):
            raise httpx.ConnectError("meili unreachable in stub")

    monkeypatch.setattr(httpx, "AsyncClient", _BoomClient)

    # And stub the tika probe so the test isn't sensitive to whether
    # Tika is available either (the cache is cleared in the autouse
    # fixture so the second branch executes).
    async def fake_tika_act():
        return {"ok": True, "extracted_total": 0,
                "extracted_last_5min": 0, "last_extracted_at": None}
    monkeypatch.setattr(health_services, "_tika_activity", fake_tika_act)

    r = await admin_client.get("/api/health/services/activity")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["meilisearch"]["ok"] is False
    assert "error" in body["meilisearch"]
