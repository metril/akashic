"""Phase 1 — tests for the WebSocket scan stream.

Uses FastAPI's TestClient (sync) for WS testing — the async client's WS
support requires extra deps and the sync TestClient is the documented
path. Redis pub/sub is monkeypatched to a fake async-iterator so the
tests don't depend on a live broker.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from akashic.auth.jwt import create_access_token
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.scan import Scan
from akashic.models.scan_log_entry import ScanLogEntry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services import scan_pubsub


@pytest_asyncio.fixture
async def admin_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(),
            username="admin",
            email="admin@example.com",
            password_hash="x",
            role="admin",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def viewer_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(),
            username="viewer",
            email="viewer@example.com",
            password_hash="x",
            role="viewer",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def fixture_scan(setup_db) -> Scan:
    async with setup_db() as session:
        src = Source(
            id=uuid.uuid4(),
            name="src1",
            type="local",
            connection_config={"path": "/tmp"},
            status="scanning",
        )
        session.add(src)
        await session.flush()
        scan = Scan(
            id=uuid.uuid4(),
            source_id=src.id,
            scan_type="full",
            status="running",
            files_found=100,
            current_path="/tmp/foo",
            phase="walk",
            started_at=datetime.now(timezone.utc),
        )
        session.add(scan)
        await session.commit()
        await session.refresh(scan)
        return scan


@pytest.fixture
def app(setup_db, monkeypatch):
    """Build a FastAPI app with the test DB session wired in.

    Disables lifespan so the production-engine startup pass doesn't bind
    a connection pool to the TestClient worker thread (which causes
    "another operation in progress" errors across tests when the worker
    thread's pool collides with the per-test engine in setup_db)."""

    async def _override_get_db():
        async with setup_db() as session:
            yield session

    # The WS handler imports `async_session` directly (auth runs before
    # WS accept, so dependency injection can't be used). Patch the
    # module-level reference to the test session_maker.
    from akashic.routers import scan_websocket
    monkeypatch.setattr(scan_websocket, "async_session", setup_db)

    app_ = create_app()
    app_.dependency_overrides[get_db] = _override_get_db
    # Stub out lifespan — the test fixture handles schema setup itself.
    app_.router.lifespan_context = _noop_lifespan
    return app_


from contextlib import asynccontextmanager


@asynccontextmanager
async def _noop_lifespan(_app):
    yield


def _empty_subscribe(_scan_id):
    """Stand-in for `scan_pubsub.subscribe` that yields nothing — exercises
    snapshot / close paths without a live broker."""

    async def _gen():
        # Sleep for a long time so the forwards goroutine stays alive
        # while the test sends/closes. The test always closes first.
        await asyncio.sleep(60)
        if False:
            yield {}  # pragma: no cover

    return _gen()


@pytest.mark.asyncio
async def test_ws_sends_snapshot_for_admin(app, admin_user, fixture_scan, monkeypatch):
    monkeypatch.setattr(scan_pubsub, "subscribe", _empty_subscribe)
    token = create_access_token({"sub": str(admin_user.id), "role": admin_user.role})

    with TestClient(app) as client:
        with client.websocket_connect(
            f"/ws/scans/{fixture_scan.id}?token={token}"
        ) as ws:
            snapshot = ws.receive_json()
            assert snapshot["kind"] == "snapshot"
            assert snapshot["scan_id"] == str(fixture_scan.id)
            assert snapshot["status"] == "running"
            assert snapshot["current_path"] == "/tmp/foo"
            assert snapshot["files_found"] == 100
            assert "recent_lines" in snapshot


@pytest.mark.asyncio
async def test_ws_includes_recent_log_lines_in_snapshot(
    app, admin_user, fixture_scan, setup_db, monkeypatch
):
    monkeypatch.setattr(scan_pubsub, "subscribe", _empty_subscribe)
    async with setup_db() as session:
        for i in range(3):
            session.add(ScanLogEntry(
                scan_id=fixture_scan.id,
                ts=datetime.now(timezone.utc),
                level="info",
                message=f"line {i}",
            ))
        await session.commit()

    token = create_access_token({"sub": str(admin_user.id), "role": admin_user.role})
    with TestClient(app) as client:
        with client.websocket_connect(
            f"/ws/scans/{fixture_scan.id}?token={token}"
        ) as ws:
            snapshot = ws.receive_json()
            assert len(snapshot["recent_lines"]) == 3
            messages = [r["message"] for r in snapshot["recent_lines"]]
            # Lines come back oldest-first so the client can append in order.
            assert messages == ["line 0", "line 1", "line 2"]


@pytest.mark.asyncio
async def test_ws_rejects_invalid_token(app, fixture_scan):
    with TestClient(app) as client:
        with pytest.raises(Exception):
            with client.websocket_connect(
                f"/ws/scans/{fixture_scan.id}?token=garbage"
            ) as ws:
                ws.receive_json()  # forces the close to surface


@pytest.mark.asyncio
async def test_ws_rejects_viewer_without_source_access(
    app, viewer_user, fixture_scan
):
    token = create_access_token({"sub": str(viewer_user.id), "role": viewer_user.role})
    with TestClient(app) as client:
        with pytest.raises(Exception):
            with client.websocket_connect(
                f"/ws/scans/{fixture_scan.id}?token={token}"
            ) as ws:
                ws.receive_json()


@pytest.mark.asyncio
async def test_ws_404_for_unknown_scan(app, admin_user):
    token = create_access_token({"sub": str(admin_user.id), "role": admin_user.role})
    with TestClient(app) as client:
        with pytest.raises(Exception):
            with client.websocket_connect(
                f"/ws/scans/{uuid.uuid4()}?token={token}"
            ) as ws:
                ws.receive_json()
