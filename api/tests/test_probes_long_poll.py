"""On-demand probe long-poll round-trip (v0.28.0).

Cover:
  * GET /api/scanners/{id}/probes/long-poll requires the scanner JWT.
  * 204 when nothing is pending.
  * Returns the probe payload when the API publishes one mid-call.
  * The companion POST /probes/{request_id}/report writes a row to
    reachability_results and fans out to subscribers.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from akashic.auth.jwt import create_ingest_token, create_access_token
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.reachability_result import ReachabilityResult
from akashic.models.scanner import Scanner
from akashic.models.source import Source
from akashic.services.scanner_keys import sign_jwt


def _scanner_jwt(scanner_id: str, priv_pem: str) -> str:
    now = int(time.time())
    return sign_jwt(
        priv_pem,
        {"iss": "scanner", "sub": scanner_id, "iat": now, "exp": now + 300},
        headers={"kid": scanner_id},
    )


@pytest_asyncio.fixture
async def bearer_client(setup_db) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def admin_client(setup_db) -> AsyncClient:
    """Admin-authed client for fixture setup endpoints
    (POST /api/scanners). The scanner endpoints under test still
    require the scanner JWT."""
    from akashic.auth.dependencies import get_current_user, require_admin
    from akashic.models.user import User

    user_id = uuid.uuid4()

    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_admin():
        return User(
            id=user_id, username="admin", email="a@e",
            password_hash="x", role="admin",
        )

    # Seed the admin row so audit FKs resolve.
    async with setup_db() as session:
        from akashic.models.user import User as _U
        session.add(_U(
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


@pytest.mark.asyncio
async def test_long_poll_rejects_non_scanner_jwt(bearer_client, admin_client):
    """Access tokens (akashic-api audience) and ingest tokens
    (akashic-ingest audience) must NOT satisfy the scanner JWT
    dependency."""
    scn = (await admin_client.post(
        "/api/scanners", json={"name": "lp-1", "pool": "default"},
    )).json()
    sid = scn["id"]

    access = create_access_token({"sub": str(uuid.uuid4())})
    ingest = create_ingest_token(str(uuid.uuid4()))
    for tok in (access, ingest):
        r = await bearer_client.get(
            f"/api/scanners/{sid}/probes/long-poll",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 401, f"got {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_long_poll_returns_204_when_no_probe_pending(
    bearer_client, admin_client, monkeypatch,
):
    """Empty long-poll → 204. Patches the wait timeout down to ~250ms
    so the test is quick."""
    scn = (await admin_client.post(
        "/api/scanners", json={"name": "lp-2", "pool": "default"},
    )).json()
    tok = _scanner_jwt(scn["id"], scn["private_key_pem"])

    from akashic.services import probe_dispatch
    real_wait = probe_dispatch.wait_for_probe

    async def fast_wait(scanner_id, timeout_s=30.0):
        return await real_wait(scanner_id, timeout_s=0.25)

    monkeypatch.setattr(probe_dispatch, "wait_for_probe", fast_wait)
    # The endpoint imports wait_for_probe inside its handler:
    import akashic.routers.scanners as routers_scanners  # noqa: F401

    r = await bearer_client.get(
        f"/api/scanners/{scn['id']}/probes/long-poll",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_long_poll_delivers_published_probe(
    bearer_client, admin_client, setup_db,
):
    """Publish a probe to the scanner's channel mid-call; the long-
    poll handler should return it within a beat.

    We retry the publish a couple of times to absorb the small window
    between the handler's `subscribe` returning and Redis confirming
    the subscription — without an active subscriber, pub/sub silently
    drops the message.
    """
    scn = (await admin_client.post(
        "/api/scanners", json={"name": "lp-3", "pool": "default"},
    )).json()
    tok = _scanner_jwt(scn["id"], scn["private_key_pem"])

    # Seed a source so the report endpoint has a valid source_id.
    src_id = uuid.uuid4()
    async with setup_db() as session:
        session.add(Source(
            id=src_id, name="lp-src", type="smb",
            connection_config={"host": "h", "share": "s"},
        ))
        await session.commit()

    from akashic.services.scan_pubsub import _client as redis_client
    from akashic.services.probe_dispatch import _probe_channel

    request_id = uuid.uuid4()
    probe = {
        "request_id": str(request_id),
        "source_id": str(src_id),
        "source_type": "smb",
        "connection_config": {"host": "h"},
    }
    channel = _probe_channel(scn["id"])

    # Retry publish until at least one subscriber is listening — Redis
    # `PUBLISH` returns the count of receivers, so we know when the
    # handler's `subscribe` has registered. Cap at 30 attempts (~3 s)
    # so a real bug fails the test instead of hanging.
    async def publish_when_subscribed():
        await asyncio.sleep(0.05)
        for _ in range(30):
            n = await redis_client().publish(channel, json.dumps(probe))
            if n > 0:
                return
            await asyncio.sleep(0.1)

    task = asyncio.create_task(publish_when_subscribed())
    r = await bearer_client.get(
        f"/api/scanners/{scn['id']}/probes/long-poll",
        headers={"Authorization": f"Bearer {tok}"},
    )
    await task
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["request_id"] == str(request_id)
    assert body["source_id"] == str(src_id)


@pytest.mark.asyncio
async def test_report_persists_result_and_publishes(
    bearer_client, admin_client, setup_db,
):
    """POST /probes/{request_id}/report writes a reachability_results
    row attributed to the scanner and publishes to the per-source
    fan-out channel."""
    scn = (await admin_client.post(
        "/api/scanners", json={"name": "lp-4", "pool": "default"},
    )).json()
    tok = _scanner_jwt(scn["id"], scn["private_key_pem"])

    src_id = uuid.uuid4()
    async with setup_db() as session:
        session.add(Source(
            id=src_id, name="report-src", type="smb",
            connection_config={"host": "h", "share": "s"},
        ))
        await session.commit()

    request_id = uuid.uuid4()
    r = await bearer_client.post(
        f"/api/scanners/{scn['id']}/probes/{request_id}/report",
        json={
            "ok": True, "step": None, "error": None,
            "source_id": str(src_id),
        },
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 204, r.text

    async with setup_db() as session:
        rows = (await session.execute(
            select(ReachabilityResult).where(
                ReachabilityResult.source_id == src_id,
            )
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].ok is True
    assert rows[0].scanner_id == uuid.UUID(scn["id"])
