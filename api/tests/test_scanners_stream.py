"""Admin-only /ws/scanners stream — snapshot + push events."""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from akashic.auth.jwt import create_access_token
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.scanner_discovery_request import ScannerDiscoveryRequest
from akashic.models.user import User
from akashic.services import scan_pubsub


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


@pytest_asyncio.fixture
async def viewer_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(), username="v", email="v@b.c",
            password_hash="x", role="viewer",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@asynccontextmanager
async def _noop_lifespan(_app):
    yield


@pytest.fixture
def app(setup_db, monkeypatch):
    """FastAPI app wired to the test DB session, lifespan stubbed."""
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    from akashic.routers import scan_websocket
    monkeypatch.setattr(scan_websocket, "async_session", setup_db)

    app_ = create_app()
    app_.dependency_overrides[get_db] = _override_get_db
    app_.router.lifespan_context = _noop_lifespan
    return app_


def _silent_subscribe_scanners():
    """Stand-in for `subscribe_scanners` that just blocks until cancel."""
    async def _gen():
        await asyncio.sleep(60)
        if False:
            yield {}  # pragma: no cover

    return _gen()


@pytest.mark.asyncio
async def test_ws_rejects_non_admin(app, viewer_user):
    token = create_access_token({"sub": str(viewer_user.id), "role": "viewer"})
    with TestClient(app) as client:
        with pytest.raises(Exception):
            with client.websocket_connect(f"/ws/scanners?token={token}") as ws:
                ws.receive_json()


@pytest.mark.asyncio
async def test_ws_rejects_invalid_token(app):
    with TestClient(app) as client:
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/scanners?token=garbage") as ws:
                ws.receive_json()


@pytest.mark.asyncio
async def test_ws_snapshot_includes_pending_discoveries(
    app, admin_user, setup_db, monkeypatch,
):
    monkeypatch.setattr(scan_pubsub, "subscribe_scanners", _silent_subscribe_scanners)
    # Seed two pending + one decided so the snapshot contains only the
    # pending ones.
    now = datetime.now(timezone.utc)
    async with setup_db() as session:
        for code in ("AAAA-BBBB", "CCCC-DDDD"):
            session.add(ScannerDiscoveryRequest(
                public_key_pem="-----BEGIN PUBLIC KEY-----\nx\n-----END PUBLIC KEY-----",
                key_fingerprint=uuid.uuid4().hex + uuid.uuid4().hex[:32],
                pairing_code=code,
                expires_at=now + timedelta(minutes=10),
            ))
        session.add(ScannerDiscoveryRequest(
            public_key_pem="-----BEGIN PUBLIC KEY-----\ny\n-----END PUBLIC KEY-----",
            key_fingerprint=uuid.uuid4().hex + uuid.uuid4().hex[:32],
            pairing_code="ZZZZ-9999",
            expires_at=now + timedelta(minutes=10),
            status="approved",
            decided_at=now,
        ))
        await session.commit()

    token = create_access_token({"sub": str(admin_user.id), "role": "admin"})
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/scanners?token={token}") as ws:
            snapshot = ws.receive_json()
            assert snapshot["kind"] == "snapshot"
            codes = {r["pairing_code"] for r in snapshot["pending_discoveries"]}
            assert codes == {"AAAA-BBBB", "CCCC-DDDD"}


@pytest.mark.asyncio
async def test_ws_forwards_pubsub_events(app, admin_user, monkeypatch):
    """Push a fake event via the patched subscriber and ensure the
    client receives it after the snapshot frame."""
    queue: asyncio.Queue = asyncio.Queue()

    async def _gen():
        while True:
            yield await queue.get()

    monkeypatch.setattr(scan_pubsub, "subscribe_scanners", lambda: _gen())

    token = create_access_token({"sub": str(admin_user.id), "role": "admin"})
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/scanners?token={token}") as ws:
            snapshot = ws.receive_json()
            assert snapshot["kind"] == "snapshot"

            # `setting.changed` is internal cache-bust noise — ensure it
            # does NOT reach the client. Drop a real scanner event after
            # it; the client should see only the real one.
            queue.put_nowait({"kind": "setting.changed", "key": "discovery_enabled"})
            queue.put_nowait({
                "kind": "scanner.discovery_requested",
                "discovery_id": "abc",
                "pairing_code": "AAAA-BBBB",
            })

            event = ws.receive_json()
            assert event["kind"] == "scanner.discovery_requested"
            assert event["pairing_code"] == "AAAA-BBBB"
