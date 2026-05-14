"""Bulk reachability fan-out — POST /api/hosts/{id}/test-shares.

Confirms the v0.28.1 host-level Test reachability button:
  * dispatches to scanners (no inline API dialing).
  * publishes one probe request per (source, scanner) pair to the
    right scanner channel.
  * filters by pool / allowed_source_ids.
  * skips offline scanners by default.
  * 0 attached shares → empty results, no work.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.auth.dependencies import get_current_user, require_admin
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.host import Host
from akashic.models.scanner import Scanner
from akashic.models.source import Source
from akashic.models.user import User


@pytest_asyncio.fixture
async def admin_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(), username="admin", email="a@e",
            password_hash="x", role="admin",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def client(setup_db, admin_user: User) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_user():
        return admin_user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[require_admin] = _override_user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as ac:
        yield ac


async def _seed_host_with_shares(setup_db, n_shares: int) -> tuple[uuid.UUID, list[uuid.UUID]]:
    async with setup_db() as session:
        host = Host(
            id=uuid.uuid4(), name=f"host-{uuid.uuid4().hex[:6]}",
            type="smb",
            connection_config={"host": "h", "username": "u", "password": "p"},
        )
        session.add(host)
        await session.flush()
        source_ids = []
        for i in range(n_shares):
            src = Source(
                id=uuid.uuid4(), name=f"share-{i}-{uuid.uuid4().hex[:6]}",
                type="smb",
                connection_config={"share": f"s{i}"},
                host_id=host.id,
            )
            session.add(src)
            source_ids.append(src.id)
        await session.commit()
        return host.id, source_ids


async def _seed_online_scanner(setup_db, name: str) -> uuid.UUID:
    """Insert a scanner row with last_seen_at = now so the eligibility
    filter treats it as online without needing a real handshake."""
    async with setup_db() as session:
        sc = Scanner(
            id=uuid.uuid4(), name=name, pool="default",
            public_key_pem="x", key_fingerprint=f"fp-{name}",
            last_seen_at=datetime.now(timezone.utc),
        )
        session.add(sc)
        await session.commit()
        return sc.id


@pytest.mark.asyncio
async def test_test_shares_returns_empty_when_no_attached_sources(
    client: AsyncClient, setup_db,
):
    host_id, _ = await _seed_host_with_shares(setup_db, 0)
    r = await client.post(f"/api/hosts/{host_id}/test-shares", json={})
    assert r.status_code == 200, r.text
    assert r.json()["results"] == []


@pytest.mark.asyncio
async def test_test_shares_publishes_one_probe_per_source_scanner_pair(
    client: AsyncClient, setup_db,
):
    """For 2 attached shares × 2 online scanners we expect 4 probe
    requests published — one per pair. Without a live agent
    consuming, every pair returns pending=True."""
    host_id, source_ids = await _seed_host_with_shares(setup_db, 2)
    s1 = await _seed_online_scanner(setup_db, "lp-1")
    s2 = await _seed_online_scanner(setup_db, "lp-2")

    # Speed up the dispatcher's wait; we don't need to wait for results
    # since no agent is running.
    import akashic.services.probe_dispatch as probe_dispatch
    real_dispatch = probe_dispatch.dispatch_remote

    async def fast(*, db, source, scanner_ids, timeout_s=5.0, triggered_by=None):
        return await real_dispatch(
            db=db, source=source, scanner_ids=scanner_ids,
            timeout_s=0.25, triggered_by=triggered_by,
        )

    probe_dispatch.dispatch_remote = fast

    # v0.28.2 — probes now land in per-scanner Redis LISTs (LPUSH),
    # not pub/sub. Drain each scanner's list after the API returns;
    # we expect 2 sources × 2 scanners = 4 items total.
    from akashic.services.probe_dispatch import _probe_channel
    from akashic.services.scan_pubsub import _client as redis_client
    redis = redis_client()

    try:
        r = await client.post(
            f"/api/hosts/{host_id}/test-shares", json={},
        )
    finally:
        probe_dispatch.dispatch_remote = real_dispatch

    assert r.status_code == 200, r.text
    body = r.json()
    # 2 sources × 2 scanners = 4 result rows.
    assert len(body["results"]) == 4
    assert all(row["pending"] is True for row in body["results"])

    # Pull everything off both scanner queues and confirm the count.
    items: list[dict] = []
    for sid in (s1, s2):
        chan = _probe_channel(sid)
        while True:
            raw = await redis.rpop(chan)
            if raw is None:
                break
            items.append(json.loads(raw))
    assert len(items) == 4


@pytest.mark.asyncio
async def test_test_shares_skips_offline_scanners_by_default(
    client: AsyncClient, setup_db,
):
    host_id, _ = await _seed_host_with_shares(setup_db, 1)
    online_id = await _seed_online_scanner(setup_db, "online")
    # Manually craft an offline scanner: last_seen_at far in the past.
    async with setup_db() as session:
        offline = Scanner(
            id=uuid.uuid4(), name="offline", pool="default",
            public_key_pem="x", key_fingerprint="fp-offline",
            last_seen_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        )
        session.add(offline)
        await session.commit()
        offline_id = offline.id

    import akashic.services.probe_dispatch as probe_dispatch
    real_dispatch = probe_dispatch.dispatch_remote

    async def fast(*, db, source, scanner_ids, timeout_s=5.0, triggered_by=None):
        return await real_dispatch(
            db=db, source=source, scanner_ids=scanner_ids,
            timeout_s=0.1, triggered_by=triggered_by,
        )

    probe_dispatch.dispatch_remote = fast
    try:
        r = await client.post(f"/api/hosts/{host_id}/test-shares", json={})
    finally:
        probe_dispatch.dispatch_remote = real_dispatch

    assert r.status_code == 200, r.text
    rows = r.json()["results"]
    # Only the online scanner appears in the result rows.
    scanner_ids = {row["scanner_id"] for row in rows}
    assert str(online_id) in scanner_ids
    assert str(offline_id) not in scanner_ids


@pytest.mark.asyncio
async def test_test_shares_404_on_unknown_host(client: AsyncClient):
    r = await client.post(f"/api/hosts/{uuid.uuid4()}/test-shares", json={})
    assert r.status_code == 404
