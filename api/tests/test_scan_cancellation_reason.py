"""v0.29.8 — 409 cancellation responses carry a structured reason.

Pre-fix the heartbeat 409 body had only ``{"detail": "scan is X"}``
and the scanner unconditionally logged "scan cancelled by user". This
covers the three trigger paths and asserts the body now distinguishes
them so the scanner can log accurately:

- user-initiated cancel (POST /api/scans/{id}/cancel)
- watchdog reap (scheduler._check_stale_scans)
- terminal-complete race (POST /api/scans/{id}/complete)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from akashic.auth.dependencies import get_current_user, get_ingest_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.scan import Scan
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
async def fixture_source(setup_db, admin_user: User) -> Source:
    async with setup_db() as session:
        src = Source(
            id=uuid.uuid4(),
            name="src1",
            type="local",
            connection_config={"path": "/tmp"},
            status="scanning",
        )
        session.add(src)
        await session.commit()
        await session.refresh(src)
        return src


@pytest_asyncio.fixture
async def fixture_running_scan(setup_db, fixture_source: Source) -> Scan:
    async with setup_db() as session:
        scan = Scan(
            id=uuid.uuid4(),
            source_id=fixture_source.id,
            scan_type="full",
            status="running",
            started_at=datetime.now(timezone.utc),
            last_heartbeat_at=datetime.now(timezone.utc),
        )
        session.add(scan)
        await session.commit()
        await session.refresh(scan)
        return scan


@pytest_asyncio.fixture
async def client(setup_db, admin_user: User, monkeypatch) -> AsyncClient:
    # Patch out Redis publish — no broker in unit tests.
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(scan_pubsub, "publish", _noop)
    monkeypatch.setattr(scan_pubsub, "publish_source_event", _noop)

    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_get_current_user():
        return admin_user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    app.dependency_overrides[get_ingest_user] = _override_get_current_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def _heartbeat_body() -> dict:
    return {
        "current_path": "/x",
        "files_scanned": 1,
        "bytes_scanned": 1,
        "files_skipped": 0,
        "dirs_walked": 0,
        "dirs_queued": 0,
    }


@pytest.mark.asyncio
async def test_409_after_user_cancel_carries_reason_user(
    client: AsyncClient, fixture_running_scan: Scan,
):
    cancel = await client.post(f"/api/scans/{fixture_running_scan.id}/cancel")
    assert cancel.status_code == 200

    r = await client.post(
        f"/api/scans/{fixture_running_scan.id}/heartbeat",
        json=_heartbeat_body(),
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert isinstance(detail, dict), f"expected structured detail, got {detail!r}"
    assert detail["status"] == "cancelled"
    assert detail["reason"] == "user"


@pytest.mark.asyncio
async def test_409_after_watchdog_reap_carries_reason_watchdog(
    client: AsyncClient, fixture_running_scan: Scan, setup_db,
):
    # Simulate the watchdog's terminal write directly — the scheduler
    # job is exercised in test_stale_scan_watchdog.py; here we only
    # care that the reason is propagated to the 409 body.
    async with setup_db() as session:
        scan = (await session.execute(
            select(Scan).where(Scan.id == fixture_running_scan.id)
        )).scalar_one()
        scan.status = "failed"
        scan.error_message = "Watchdog: exceeded 60 min"
        scan.cancellation_reason = "watchdog"
        scan.completed_at = datetime.now(timezone.utc)
        await session.commit()

    r = await client.post(
        f"/api/scans/{fixture_running_scan.id}/heartbeat",
        json=_heartbeat_body(),
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["status"] == "failed"
    assert detail["reason"] == "watchdog"


@pytest.mark.asyncio
async def test_409_after_terminal_complete_carries_reason_completed(
    client: AsyncClient, fixture_running_scan: Scan, setup_db,
):
    async with setup_db() as session:
        scan = (await session.execute(
            select(Scan).where(Scan.id == fixture_running_scan.id)
        )).scalar_one()
        scan.status = "completed"
        scan.cancellation_reason = "completed"
        scan.completed_at = datetime.now(timezone.utc)
        await session.commit()

    r = await client.post(
        f"/api/scans/{fixture_running_scan.id}/heartbeat",
        json=_heartbeat_body(),
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["status"] == "completed"
    assert detail["reason"] == "completed"


@pytest.mark.asyncio
async def test_409_legacy_null_reason_treated_as_user_compatible(
    client: AsyncClient, fixture_running_scan: Scan, setup_db,
):
    """Pre-migration rows have NULL cancellation_reason. The 409
    serializer must still respond with the field present (None) so
    the scanner falls back to the legacy "by user" message instead
    of crashing on a missing key."""
    async with setup_db() as session:
        scan = (await session.execute(
            select(Scan).where(Scan.id == fixture_running_scan.id)
        )).scalar_one()
        scan.status = "cancelled"
        scan.cancellation_reason = None
        scan.completed_at = datetime.now(timezone.utc)
        await session.commit()

    r = await client.post(
        f"/api/scans/{fixture_running_scan.id}/heartbeat",
        json=_heartbeat_body(),
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["status"] == "cancelled"
    assert detail["reason"] is None
