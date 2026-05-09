"""Phase 1 — tests for the scan-progress HTTP endpoints.

Heartbeat / log / stderr POSTs and the GET log backfill. The Redis
pub/sub publish path is patched out so the tests don't need a live
broker — fan-out is verified separately in the WebSocket tests.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.auth.dependencies import get_current_user, get_ingest_user
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
async def fixture_scan(setup_db, fixture_source: Source) -> Scan:
    async with setup_db() as session:
        scan = Scan(
            id=uuid.uuid4(),
            source_id=fixture_source.id,
            scan_type="full",
            status="pending",
        )
        session.add(scan)
        await session.commit()
        await session.refresh(scan)
        return scan


@pytest_asyncio.fixture
async def client(setup_db, admin_user: User, monkeypatch, request) -> AsyncClient:
    # Skip Redis publish — the test-time API has no broker. Each call is
    # a no-op coroutine. Captures published payloads on the request node
    # so individual tests can assert on them when relevant.
    captured_per_scan: list[tuple[uuid.UUID, dict]] = []
    captured_source_events: list[dict] = []

    async def _capture_scan_publish(scan_id, event):
        captured_per_scan.append((scan_id, event))

    async def _capture_source_publish(event):
        captured_source_events.append(event)

    monkeypatch.setattr(scan_pubsub, "publish", _capture_scan_publish)
    monkeypatch.setattr(
        scan_pubsub, "publish_source_event", _capture_source_publish,
    )
    request.node._captured_per_scan = captured_per_scan
    request.node._captured_source_events = captured_source_events

    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_get_current_user():
        return admin_user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    # v0.27.1 — heartbeat / log / stderr POSTs now require the
    # ingest-audience JWT (the scanner agent's lease-time token).
    # Reuse the admin override so existing tests don't have to mint a
    # real ingest token on every call.
    app.dependency_overrides[get_ingest_user] = _override_get_current_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_heartbeat_updates_scan_fields(
    client: AsyncClient, fixture_scan: Scan, setup_db
):
    r = await client.post(
        f"/api/scans/{fixture_scan.id}/heartbeat",
        json={
            "current_path": "/tmp/foo",
            "files_scanned": 100,
            "bytes_scanned": 4096,
            "files_skipped": 2,
            "dirs_walked": 5,
            "dirs_queued": 3,
            "total_estimated": 1000,
            "phase": "walk",
        },
    )
    assert r.status_code == 204

    async with setup_db() as session:
        from sqlalchemy import select
        scan = (
            await session.execute(select(Scan).where(Scan.id == fixture_scan.id))
        ).scalar_one()
        assert scan.current_path == "/tmp/foo"
        assert scan.bytes_scanned_so_far == 4096
        assert scan.files_skipped == 2
        assert scan.dirs_walked == 5
        assert scan.dirs_queued == 3
        assert scan.total_estimated == 1000
        assert scan.phase == "walk"
        assert scan.last_heartbeat_at is not None
        # First heartbeat flips pending → running automatically.
        assert scan.status == "running"


@pytest.mark.asyncio
async def test_heartbeat_publishes_scan_state_to_sources_channel(
    client: AsyncClient, fixture_scan: Scan, fixture_source, request,
):
    """v0.4.7: heartbeat now ALSO publishes a scan.state to the
    sources channel so SourceCard / SourceDetail panel see live
    files_found + current_path advancing without anyone needing to
    open the per-scan Live Log panel. Before this, scan.state events
    only fired at lease + complete (twice per scan total) and the
    source card showed "0 files scanned" for the entire duration."""
    r = await client.post(
        f"/api/scans/{fixture_scan.id}/heartbeat",
        json={
            "current_path": "/data/movies/scan",
            "files_scanned": 42,
            "bytes_scanned": 4096,
            "files_skipped": 0,
            "dirs_walked": 3,
            "dirs_queued": 7,
            "total_estimated": 1000,
            "phase": "walk",
        },
    )
    assert r.status_code == 204

    captured = request.node._captured_source_events
    assert len(captured) == 1
    event = captured[0]
    assert event["kind"] == "scan.state"
    assert event["source_id"] == str(fixture_source.id)
    assert event["scan_id"] == str(fixture_scan.id)
    # First heartbeat flips pending → running before the publish.
    assert event["scan_status"] == "running"
    assert event["source_status"] == "scanning"
    assert event["scan_type"] == "full"
    assert event["current_path"] == "/data/movies/scan"
    # files_found is sourced from the DB column, not the heartbeat
    # body — heartbeat carries files_scanned (a counter) but the
    # source-of-truth for total indexed files is files_found, which
    # the batch ingest path increments. Newly-pending scan has 0.
    assert event["files_found"] == 0


@pytest.mark.asyncio
async def test_heartbeat_404_unknown_scan(client: AsyncClient):
    r = await client.post(
        f"/api/scans/{uuid.uuid4()}/heartbeat",
        json={"files_scanned": 0, "bytes_scanned": 0},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_log_post_inserts_rows(
    client: AsyncClient, fixture_scan: Scan, setup_db
):
    now = datetime.now(timezone.utc)
    r = await client.post(
        f"/api/scans/{fixture_scan.id}/log",
        json={
            "lines": [
                {"ts": now.isoformat(), "level": "info", "message": "starting"},
                {"ts": (now + timedelta(milliseconds=10)).isoformat(),
                 "level": "warn", "message": "permission denied on /etc/shadow"},
            ]
        },
    )
    assert r.status_code == 204

    async with setup_db() as session:
        from sqlalchemy import select
        rows = (
            await session.execute(
                select(ScanLogEntry).where(ScanLogEntry.scan_id == fixture_scan.id)
            )
        ).scalars().all()
        assert len(rows) == 2
        levels = {r.level for r in rows}
        assert levels == {"info", "warn"}


@pytest.mark.asyncio
async def test_stderr_post_inserts_rows_with_level_stderr(
    client: AsyncClient, fixture_scan: Scan, setup_db
):
    now = datetime.now(timezone.utc)
    r = await client.post(
        f"/api/scans/{fixture_scan.id}/stderr",
        json={
            "chunks": [
                {"ts": now.isoformat(), "chunk": "panic: stack trace…\n"},
            ]
        },
    )
    assert r.status_code == 204

    async with setup_db() as session:
        from sqlalchemy import select
        rows = (
            await session.execute(
                select(ScanLogEntry).where(ScanLogEntry.scan_id == fixture_scan.id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].level == "stderr"
        assert "panic" in rows[0].message


@pytest.mark.asyncio
async def test_get_log_filters_by_kind_and_since(
    client: AsyncClient, fixture_scan: Scan, setup_db
):
    base = datetime.now(timezone.utc).replace(microsecond=0)
    async with setup_db() as session:
        session.add_all([
            ScanLogEntry(
                scan_id=fixture_scan.id, ts=base, level="info",
                message="early structured",
            ),
            ScanLogEntry(
                scan_id=fixture_scan.id, ts=base + timedelta(seconds=1),
                level="stderr", message="raw chunk",
            ),
            ScanLogEntry(
                scan_id=fixture_scan.id, ts=base + timedelta(seconds=2),
                level="error", message="late error",
            ),
        ])
        await session.commit()

    r = await client.get(f"/api/scans/{fixture_scan.id}/log?kind=structured")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert all(row["level"] != "stderr" for row in rows)

    r = await client.get(f"/api/scans/{fixture_scan.id}/log?kind=stderr")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["level"] == "stderr"

    from urllib.parse import quote
    since = quote((base + timedelta(milliseconds=500)).isoformat())
    r = await client.get(f"/api/scans/{fixture_scan.id}/log?since={since}")
    assert r.status_code == 200
    rows = r.json()
    # Original `base` row excluded; the +1s and +2s rows kept.
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_log_batch_size_capped(
    client: AsyncClient, fixture_scan: Scan
):
    """The schema-level cap rejects batches above 200 lines so a runaway
    scanner can't flood the API."""
    now = datetime.now(timezone.utc).isoformat()
    body = {
        "lines": [
            {"ts": now, "level": "info", "message": f"flood {i}"} for i in range(201)
        ]
    }
    r = await client.post(f"/api/scans/{fixture_scan.id}/log", json=body)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_empty_log_post_is_noop(client: AsyncClient, fixture_scan: Scan):
    r = await client.post(
        f"/api/scans/{fixture_scan.id}/log",
        json={"lines": []},
    )
    assert r.status_code == 204
