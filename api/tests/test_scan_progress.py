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
async def test_heartbeat_renews_the_scan_lease(
    client: AsyncClient, fixture_scan: Scan, setup_db
):
    """v0.29.10 — every heartbeat must extend `lease_expires_at`.
    Pre-fix the lease was set once at claim time and never renewed,
    so any scan running >60 s had an expired lease while still
    heartbeating; `_requeue_orphan_leases` then re-queued the healthy
    scan and a second scanner double-leased it."""
    from sqlalchemy import select
    from akashic.routers.scanners import _LEASE_DURATION_SECONDS

    # Simulate a scan whose lease already expired (the pre-fix steady
    # state for any long-running scan).
    async with setup_db() as session:
        scan = (
            await session.execute(select(Scan).where(Scan.id == fixture_scan.id))
        ).scalar_one()
        scan.status = "running"
        scan.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await session.commit()

    before = datetime.now(timezone.utc)
    r = await client.post(
        f"/api/scans/{fixture_scan.id}/heartbeat",
        json={
            "files_scanned": 1,
            "bytes_scanned": 1,
            "files_skipped": 0,
            "dirs_walked": 0,
            "dirs_queued": 0,
        },
    )
    assert r.status_code == 204

    async with setup_db() as session:
        scan = (
            await session.execute(select(Scan).where(Scan.id == fixture_scan.id))
        ).scalar_one()
        # Lease pushed back into the future, ≈ now + _LEASE_DURATION_SECONDS.
        assert scan.lease_expires_at is not None
        assert scan.lease_expires_at > before, (
            "heartbeat did not renew an expired lease"
        )
        expected = before + timedelta(seconds=_LEASE_DURATION_SECONDS)
        # Generous window — the heartbeat handler stamps `now` itself.
        assert abs((scan.lease_expires_at - expected).total_seconds()) < 30


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
async def test_heartbeat_scan_state_carries_scanner_name(
    client: AsyncClient, fixture_scan: Scan, setup_db, request,
):
    """v0.30.1 — the live heartbeat scan.state broadcast must resolve
    the assigned scanner's name. Pre-fix it hardcoded
    `scanner_name: None`, which blanked the name in the UI right after
    the (correctly-populated) WS snapshot frame."""
    from sqlalchemy import select

    from akashic.models.scanner import Scanner

    scanner_id = uuid.uuid4()
    async with setup_db() as session:
        session.add(Scanner(
            id=scanner_id, name="hb-attribution", pool="default",
            public_key_pem="x", key_fingerprint=f"fp-{scanner_id}",
        ))
        scan = (
            await session.execute(select(Scan).where(Scan.id == fixture_scan.id))
        ).scalar_one()
        scan.assigned_scanner_id = scanner_id
        await session.commit()

    r = await client.post(
        f"/api/scans/{fixture_scan.id}/heartbeat",
        json={"files_scanned": 1, "bytes_scanned": 1, "files_skipped": 0,
              "dirs_walked": 0, "dirs_queued": 0},
    )
    assert r.status_code == 204

    captured = request.node._captured_source_events
    assert len(captured) == 1
    event = captured[0]
    assert event["scanner_id"] == str(scanner_id)
    assert event["scanner_name"] == "hb-attribution"


@pytest.mark.asyncio
async def test_heartbeat_scan_state_scanner_name_none_when_unassigned(
    client: AsyncClient, fixture_scan: Scan, request,
):
    """An unassigned scan (no scanner has leased it yet) broadcasts
    `scanner_name: None` — correct, not the v0.30.1 bug."""
    r = await client.post(
        f"/api/scans/{fixture_scan.id}/heartbeat",
        json={"files_scanned": 1, "bytes_scanned": 1, "files_skipped": 0,
              "dirs_walked": 0, "dirs_queued": 0},
    )
    assert r.status_code == 204
    captured = request.node._captured_source_events
    assert len(captured) == 1
    assert captured[0]["scanner_id"] is None
    assert captured[0]["scanner_name"] is None


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


@pytest.mark.asyncio
async def test_log_post_persists_scanner_id_from_ingest_jwt(
    setup_db, admin_user, fixture_scan: Scan,
):
    """v0.28.2 — a real bearer ingest JWT carries `scanner_id` as a
    claim (minted at lease time). The log POST must persist that
    attribution on every row so the Live Log panel can render the
    per-scanner pill. Bypasses the test_scan_progress `client`
    fixture (which overrides the deps to skip auth) to exercise the
    real JWT decode path."""
    import uuid as _uuid
    from akashic.auth.jwt import create_ingest_token
    from akashic.auth.dependencies import get_current_user
    from akashic.database import get_db
    from akashic.main import create_app
    from akashic.models.scanner import Scanner
    from sqlalchemy import select
    from httpx import ASGITransport, AsyncClient

    scanner_id = _uuid.uuid4()
    async with setup_db() as session:
        session.add(Scanner(
            id=scanner_id, name="lp-attribution", pool="default",
            public_key_pem="x", key_fingerprint=f"fp-{scanner_id}",
        ))
        await session.commit()

    tok = create_ingest_token(str(admin_user.id), scanner_id=str(scanner_id))

    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_get_current_user():
        return admin_user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    # GET /log uses get_current_user; keep that override so we can
    # also query afterward if we want.
    app.dependency_overrides[get_current_user] = _override_get_current_user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as ac:
        r = await ac.post(
            f"/api/scans/{fixture_scan.id}/log",
            json={
                "lines": [
                    {"ts": datetime.now(timezone.utc).isoformat(),
                     "level": "info", "message": "from-scanner-A"},
                ],
            },
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 204, r.text

    async with setup_db() as session:
        rows = (await session.execute(
            select(ScanLogEntry).where(
                ScanLogEntry.scan_id == fixture_scan.id,
                ScanLogEntry.message == "from-scanner-A",
            )
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].scanner_id == scanner_id


@pytest.mark.asyncio
async def test_log_scanner_name_snapshotted_and_survives_deletion(
    setup_db, admin_user, fixture_scan: Scan,
):
    """v0.30.2 — the scanner's name is snapshotted onto each log row at
    write time. The backfill / reopen path then serves it straight off
    the row, so a log line keeps its scanner attribution even after the
    scanner row is deleted. Pre-fix the read path re-derived the name
    with a JOIN, which dropped it on reopen."""
    import uuid as _uuid

    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select

    from akashic.auth.dependencies import get_current_user
    from akashic.auth.jwt import create_ingest_token
    from akashic.database import get_db
    from akashic.main import create_app
    from akashic.models.scanner import Scanner

    scanner_id = _uuid.uuid4()
    async with setup_db() as session:
        session.add(Scanner(
            id=scanner_id, name="attrib-survivor", pool="default",
            public_key_pem="x", key_fingerprint=f"fp-{scanner_id}",
        ))
        await session.commit()

    tok = create_ingest_token(str(admin_user.id), scanner_id=str(scanner_id))

    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_get_current_user():
        return admin_user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as ac:
        r = await ac.post(
            f"/api/scans/{fixture_scan.id}/log",
            json={"lines": [
                {"ts": datetime.now(timezone.utc).isoformat(),
                 "level": "info", "message": "attributed line"},
            ]},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 204, r.text

        # The name is snapshotted on the row at write time.
        async with setup_db() as session:
            row = (await session.execute(
                select(ScanLogEntry).where(
                    ScanLogEntry.scan_id == fixture_scan.id,
                )
            )).scalar_one()
            assert row.scanner_name == "attrib-survivor"

        # Delete the scanner — the FK SET NULL clears scanner_id on the
        # log row, but the denormalized scanner_name must survive.
        async with setup_db() as session:
            sc = await session.get(Scanner, scanner_id)
            await session.delete(sc)
            await session.commit()

        # The backfill endpoint still serves the name from the column.
        r = await ac.get(f"/api/scans/{fixture_scan.id}/log")
        assert r.status_code == 200, r.text
        lines = r.json()
        assert len(lines) == 1
        assert lines[0]["scanner_name"] == "attrib-survivor"
        assert lines[0]["scanner_id"] is None  # FK SET NULL fired
