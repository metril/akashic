"""Push-based multi-scanner join channel (v0.29.0).

Covers:

  * `notify_eligible_joiners` LPUSHes one payload per eligible-other
    scanner; respects pool / allowed_source_ids / online filters.
  * `max_parallel_scanners <= 1` short-circuits — no LPUSH at all.
  * `GET /api/scanners/{id}/scans/long-poll` returns 204 on empty,
    200 with the queued payload, 403 on scanner_id mismatch, and
    401 on non-scanner JWT.
  * LTRIM caps backlog at 50 items per scanner.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.auth.jwt import create_access_token, create_ingest_token
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.scan import Scan
from akashic.models.scanner import Scanner
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services.scan_join import (
    _join_channel,
    notify_eligible_joiners,
    wait_for_scan_join,
)
from akashic.services.scan_pubsub import _client as redis_client
from akashic.services.scanner_keys import generate_keypair, sign_jwt


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


async def _seed_admin(db_session) -> User:
    user_id = uuid.uuid4()
    admin = User(
        id=user_id, username="adminjoin", email="aj@e",
        password_hash="x", role="admin",
    )
    db_session.add(admin)
    await db_session.commit()
    return admin


async def _seed_scanner(
    db_session, *, name: str, pool: str | None = "default",
    online: bool = True, enabled: bool = True,
    allowed_source_ids=None,
) -> tuple[Scanner, str]:
    kp = generate_keypair()
    sid = uuid.uuid4()
    s = Scanner(
        id=sid,
        name=name,
        pool=pool,
        public_key_pem=kp.public_pem,
        key_fingerprint=kp.fingerprint,
        enabled=enabled,
        last_seen_at=(
            datetime.now(timezone.utc) if online
            else datetime.now(timezone.utc) - timedelta(hours=1)
        ),
        allowed_source_ids=allowed_source_ids,
    )
    db_session.add(s)
    await db_session.commit()
    return s, kp.private_pem


async def _seed_source(db_session, *, max_parallel: int = 2,
                       preferred_pool: str | None = None) -> Source:
    src = Source(
        id=uuid.uuid4(),
        name=f"join-src-{uuid.uuid4().hex[:6]}",
        type="smb",
        connection_config={"host": "h", "share": "s"},
        max_parallel_scanners=max_parallel,
        preferred_pool=preferred_pool,
    )
    db_session.add(src)
    await db_session.commit()
    return src


async def _seed_scan(db_session, source_id) -> Scan:
    scan = Scan(
        id=uuid.uuid4(), source_id=source_id, scan_type="full",
        status="running",
    )
    db_session.add(scan)
    await db_session.commit()
    return scan


@pytest.mark.asyncio
async def test_notify_eligible_joiners_lpushes_to_others_only(setup_db):
    """The lease-holder gets excluded from the notify fan-out; the
    other enabled+online scanner whose pool/allowed_source_ids match
    receives one LPUSH on its scan_join queue."""
    async with setup_db() as session:
        await _seed_admin(session)
        src = await _seed_source(session, max_parallel=2)
        scan = await _seed_scan(session, src.id)
        holder, _ = await _seed_scanner(session, name="holder")
        joiner, _ = await _seed_scanner(session, name="joiner")

        notified = await notify_eligible_joiners(
            db=session, scan=scan, source=src,
            exclude_scanner_id=holder.id,
        )
        assert notified == 1

    redis = redis_client()
    try:
        # Joiner queue should have the payload, holder queue should be empty.
        joiner_payload = await redis.rpop(_join_channel(joiner.id))
        assert joiner_payload is not None
        body = json.loads(joiner_payload)
        assert body["scan_id"] == str(scan.id)
        assert body["source"]["id"] == str(src.id)
        assert body["source"]["max_parallel_scanners"] == 2
        assert body["api_jwt"]
        assert await redis.llen(_join_channel(holder.id)) == 0
    finally:
        await redis.delete(_join_channel(joiner.id), _join_channel(holder.id))


@pytest.mark.asyncio
async def test_notify_skips_offline_disabled_and_pool_mismatched(setup_db):
    """Filter rules: skip disabled, skip offline (>2 min stale),
    skip when scanner.pool != source.preferred_pool, skip when
    scanner.allowed_source_ids doesn't include this source."""
    async with setup_db() as session:
        await _seed_admin(session)
        src = await _seed_source(
            session, max_parallel=4, preferred_pool="alpha",
        )
        scan = await _seed_scan(session, src.id)
        holder, _ = await _seed_scanner(session, name="h", pool="alpha")
        disabled, _ = await _seed_scanner(
            session, name="dis", pool="alpha", enabled=False,
        )
        offline, _ = await _seed_scanner(
            session, name="off", pool="alpha", online=False,
        )
        wrong_pool, _ = await _seed_scanner(
            session, name="wp", pool="beta",
        )
        # allowed_source_ids carves out only OTHER sources — so this
        # scanner shouldn't be told about our source.
        narrow, _ = await _seed_scanner(
            session, name="narrow", pool="alpha",
            allowed_source_ids=[uuid.uuid4()],
        )
        good, _ = await _seed_scanner(session, name="ok", pool="alpha")

        notified = await notify_eligible_joiners(
            db=session, scan=scan, source=src,
            exclude_scanner_id=holder.id,
        )
        assert notified == 1

    redis = redis_client()
    try:
        for s in (disabled, offline, wrong_pool, narrow):
            assert await redis.llen(_join_channel(s.id)) == 0, \
                f"scanner {s.name} should not have been notified"
        assert await redis.llen(_join_channel(good.id)) == 1
    finally:
        await redis.delete(*[_join_channel(s.id) for s in (
            disabled, offline, wrong_pool, narrow, good, holder,
        )])


@pytest.mark.asyncio
async def test_notify_short_circuits_when_max_parallel_is_one(setup_db):
    """Single-scanner sources don't benefit from a join channel — the
    notify helper bails before enumerating scanners so the queue stays
    completely empty."""
    async with setup_db() as session:
        await _seed_admin(session)
        src = await _seed_source(session, max_parallel=1)
        scan = await _seed_scan(session, src.id)
        holder, _ = await _seed_scanner(session, name="solo")
        other, _ = await _seed_scanner(session, name="other")

        notified = await notify_eligible_joiners(
            db=session, scan=scan, source=src,
            exclude_scanner_id=holder.id,
        )
        assert notified == 0

    redis = redis_client()
    try:
        assert await redis.llen(_join_channel(other.id)) == 0
    finally:
        await redis.delete(_join_channel(other.id))


@pytest.mark.asyncio
async def test_long_poll_returns_payload_pushed_before_subscribe(setup_db):
    """Durable list semantics — same property the v0.28.2 probe queue
    has. A payload LPUSH'd before any consumer subscribes is picked up
    by the next BRPOP."""
    scanner_id = uuid.uuid4()
    redis = redis_client()
    chan = _join_channel(scanner_id)
    payload = {"scan_id": str(uuid.uuid4()), "source": {"id": "x"}}
    await redis.lpush(chan, json.dumps(payload))

    got = await wait_for_scan_join(scanner_id, timeout_s=2.0)
    assert got is not None
    assert got["scan_id"] == payload["scan_id"]


@pytest.mark.asyncio
async def test_long_poll_returns_none_on_timeout(setup_db):
    scanner_id = uuid.uuid4()
    got = await wait_for_scan_join(scanner_id, timeout_s=1.0)
    assert got is None


@pytest.mark.asyncio
async def test_long_poll_endpoint_rejects_non_scanner_jwt(
    bearer_client, setup_db,
):
    """Both access and ingest tokens must 401 against the scanner
    long-poll dependency."""
    async with setup_db() as session:
        s, _ = await _seed_scanner(session, name="ep-auth")

    access = create_access_token({"sub": str(uuid.uuid4())})
    ingest = create_ingest_token(str(uuid.uuid4()))
    for tok in (access, ingest):
        r = await bearer_client.get(
            f"/api/scanners/{s.id}/scans/long-poll",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 401, f"got {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_long_poll_endpoint_rejects_scanner_id_mismatch(
    bearer_client, setup_db,
):
    """A scanner's JWT can only subscribe to its own queue."""
    async with setup_db() as session:
        a, a_priv = await _seed_scanner(session, name="ep-a")
        b, _ = await _seed_scanner(session, name="ep-b")
    a_tok = _scanner_jwt(str(a.id), a_priv)
    r = await bearer_client.get(
        f"/api/scanners/{b.id}/scans/long-poll",
        headers={"Authorization": f"Bearer {a_tok}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_long_poll_endpoint_204_on_timeout(
    bearer_client, setup_db, monkeypatch,
):
    """Empty queue → 204. Monkeypatch the wait helper to use a sub-
    second timeout so the test doesn't hold for the full 30 s."""
    async with setup_db() as session:
        s, priv = await _seed_scanner(session, name="ep-204")
    tok = _scanner_jwt(str(s.id), priv)

    from akashic.services import scan_join
    real_wait = scan_join.wait_for_scan_join

    async def fast_wait(scanner_id, timeout_s=30.0):
        return await real_wait(scanner_id, timeout_s=1.0)

    monkeypatch.setattr(scan_join, "wait_for_scan_join", fast_wait)

    r = await bearer_client.get(
        f"/api/scanners/{s.id}/scans/long-poll",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_long_poll_endpoint_returns_queued_payload(
    bearer_client, setup_db,
):
    """LPUSH a payload first, then call the endpoint — BRPOP picks it
    up and the handler returns 200 with the body."""
    async with setup_db() as session:
        s, priv = await _seed_scanner(session, name="ep-deliver")
    tok = _scanner_jwt(str(s.id), priv)

    redis = redis_client()
    payload = {
        "scan_id": str(uuid.uuid4()),
        "scan_type": "full",
        "source": {"id": str(uuid.uuid4()), "type": "smb"},
        "api_jwt": "stub",
    }
    await redis.lpush(_join_channel(s.id), json.dumps(payload))

    r = await bearer_client.get(
        f"/api/scanners/{s.id}/scans/long-poll",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scan_id"] == payload["scan_id"]
    assert body["source"]["id"] == payload["source"]["id"]


@pytest.mark.asyncio
async def test_join_queue_is_ltrim_capped_at_50(setup_db):
    """A pre-filled queue of 100 stale payloads is trimmed to 50 after
    one notify fan-out — matches probe_dispatch's behavior, prevents
    unbounded backlog for an offline scanner."""
    async with setup_db() as session:
        await _seed_admin(session)
        src = await _seed_source(session, max_parallel=2)
        scan = await _seed_scan(session, src.id)
        holder, _ = await _seed_scanner(session, name="cap-h")
        joiner, _ = await _seed_scanner(session, name="cap-j")

        redis = redis_client()
        chan = _join_channel(joiner.id)
        # Pre-fill with 100 stale payloads.
        for i in range(100):
            await redis.lpush(chan, json.dumps({"stale": i}))

        await notify_eligible_joiners(
            db=session, scan=scan, source=src,
            exclude_scanner_id=holder.id,
        )
        # One notify pushed 1 real entry on top of 100 stale → 101 total
        # → LTRIM 0 49 keeps 50 (newest).
        llen = await redis.llen(chan)
        assert llen == 50, f"expected 50 after LTRIM, got {llen}"
        await redis.delete(chan, _join_channel(holder.id))
