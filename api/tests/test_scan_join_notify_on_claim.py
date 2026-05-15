"""v0.29.8 — notify_eligible_joiners fires at /api/scans/lease time.

Pre-fix the join channel was only triggered inside the
/scan-work/{id}/split_units handler. That meant scanner B sat in its
30 s long-poll for the entire duration of scanner A's mount + root
enumeration (commonly 30–60 s on SMB) before getting any wake-up.
This test asserts that the second scanner's join queue gets an LPUSH
immediately when the lease lands on the first scanner.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.scan import Scan
from akashic.models.scanner import Scanner
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services.scan_join import _join_channel
from akashic.services.scan_pubsub import _client as redis_client
from akashic.services.scanner_keys import generate_keypair, sign_jwt


def _scanner_token(sid: str, priv: str) -> str:
    now = int(time.time())
    return sign_jwt(
        priv,
        {"iss": "scanner", "sub": sid, "iat": now, "exp": now + 300},
        headers={"kid": sid},
    )


def _bearer_client(setup_db) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def admin_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(), username="admin", email="a@b.c",
            password_hash="x", role="admin",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.mark.asyncio
async def test_lease_fires_notify_eligible_joiners(setup_db, admin_user):
    """A source with max_parallel_scanners=2 + two online eligible
    scanners: when scanner A leases the scan, scanner B's join queue
    must receive one LPUSH so the long-poll wakes immediately."""
    kp_a = generate_keypair()
    kp_b = generate_keypair()
    async with setup_db() as db:
        scanner_a = Scanner(
            id=uuid.uuid4(), name="A",
            public_key_pem=kp_a.public_pem,
            key_fingerprint=kp_a.fingerprint,
            pool="default",
            last_seen_at=datetime.now(timezone.utc),
        )
        scanner_b = Scanner(
            id=uuid.uuid4(), name="B",
            public_key_pem=kp_b.public_pem,
            key_fingerprint=kp_b.fingerprint,
            pool="default",
            last_seen_at=datetime.now(timezone.utc),
        )
        src = Source(
            id=uuid.uuid4(),
            name=f"src-{uuid.uuid4().hex[:6]}",
            type="smb",
            connection_config={"host": "h", "share": "s"},
            max_parallel_scanners=2,
        )
        db.add_all([scanner_a, scanner_b, src])
        await db.flush()  # src must exist before the scan FK references it
        scan = Scan(
            id=uuid.uuid4(),
            source_id=src.id,
            scan_type="full",
            status="pending",
        )
        db.add(scan)
        await db.commit()
        scanner_b_id = scanner_b.id
        scan_id = scan.id

    redis = redis_client()
    # Clean any leftover from a previous run.
    await redis.delete(_join_channel(scanner_b_id))

    token = _scanner_token(str(scanner_a.id), kp_a.private_pem)
    async with _bearer_client(setup_db) as ac:
        lease = await ac.post(
            "/api/scans/lease", json={},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert lease.status_code == 200, lease.text
    assert lease.json()["scan_id"] == str(scan_id)

    try:
        # Scanner B's queue must have exactly one payload, addressed at
        # the same scan_id, carrying the source config so the joiner
        # can start mounting immediately.
        depth = await redis.llen(_join_channel(scanner_b_id))
        assert depth == 1, f"scanner-B join queue depth = {depth}, want 1"
        raw = await redis.rpop(_join_channel(scanner_b_id))
        payload = json.loads(raw)
        assert payload["scan_id"] == str(scan_id)
        assert payload["source"]["id"] == str(src.id)
        assert payload["source"]["max_parallel_scanners"] == 2
        assert payload["api_jwt"]  # admin user exists
    finally:
        await redis.delete(_join_channel(scanner_b_id))


@pytest.mark.asyncio
async def test_lease_no_notify_for_single_scanner_source(setup_db, admin_user):
    """max_parallel_scanners=1 → notify short-circuits → no join queue
    activity, regardless of how many other scanners are online."""
    kp_a = generate_keypair()
    kp_b = generate_keypair()
    async with setup_db() as db:
        scanner_a = Scanner(
            id=uuid.uuid4(), name="A",
            public_key_pem=kp_a.public_pem,
            key_fingerprint=kp_a.fingerprint,
            pool="default",
            last_seen_at=datetime.now(timezone.utc),
        )
        scanner_b = Scanner(
            id=uuid.uuid4(), name="B",
            public_key_pem=kp_b.public_pem,
            key_fingerprint=kp_b.fingerprint,
            pool="default",
            last_seen_at=datetime.now(timezone.utc),
        )
        src = Source(
            id=uuid.uuid4(),
            name=f"src-{uuid.uuid4().hex[:6]}",
            type="smb",
            connection_config={"host": "h", "share": "s"},
            max_parallel_scanners=1,
        )
        db.add_all([scanner_a, scanner_b, src])
        await db.flush()  # src must exist before the scan FK references it
        scan = Scan(
            id=uuid.uuid4(),
            source_id=src.id,
            scan_type="full",
            status="pending",
        )
        db.add(scan)
        await db.commit()
        scanner_b_id = scanner_b.id

    redis = redis_client()
    await redis.delete(_join_channel(scanner_b_id))

    token = _scanner_token(str(scanner_a.id), kp_a.private_pem)
    async with _bearer_client(setup_db) as ac:
        lease = await ac.post(
            "/api/scans/lease", json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert lease.status_code == 200, lease.text

    try:
        assert await redis.llen(_join_channel(scanner_b_id)) == 0
    finally:
        await redis.delete(_join_channel(scanner_b_id))
