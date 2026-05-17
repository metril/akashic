"""Admin Maintenance router + job runner — /api/admin/maintenance/*."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.maintenance_job import MaintenanceJob
from akashic.models.scan import Scan
from akashic.models.scan_log_entry import ScanLogEntry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services import maintenance_jobs as mj


# — fixtures —

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
            id=uuid.uuid4(), username="viewer", email="v@b.c",
            password_hash="x", role="viewer",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def _client(setup_db, user: User) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_get_current_user():
        return user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_source(setup_db) -> uuid.UUID:
    async with setup_db() as db:
        src = Source(
            id=uuid.uuid4(), name=f"src-{uuid.uuid4().hex[:6]}",
            type="local", connection_config={"path": "/tmp"},
        )
        db.add(src)
        await db.commit()
        return src.id


async def _make_scan(
    setup_db, source_id, *, status: str,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> uuid.UUID:
    async with setup_db() as db:
        scan = Scan(
            id=uuid.uuid4(), source_id=source_id, scan_type="incremental",
            status=status, started_at=started_at, completed_at=completed_at,
        )
        db.add(scan)
        await db.commit()
        return scan.id


async def _add_logs(setup_db, scan_id, n: int) -> None:
    async with setup_db() as db:
        for i in range(n):
            db.add(ScanLogEntry(
                scan_id=scan_id, ts=datetime.now(timezone.utc),
                level="info", message=f"line {i}",
            ))
        await db.commit()


# — overview —

@pytest.mark.asyncio
async def test_overview_counts(setup_db, admin_user, monkeypatch):
    monkeypatch.setattr(
        "akashic.routers.health_services._meili_activity",
        lambda: _async_return({"documents_in_index": 123}),
    )
    src = await _make_source(setup_db)
    await _make_scan(setup_db, src, status="pending")
    await _make_scan(setup_db, src, status="running")
    old = await _make_scan(
        setup_db, src, status="completed",
        completed_at=datetime.now(timezone.utc) - timedelta(days=10),
    )
    running = await _make_scan(setup_db, src, status="running")
    await _add_logs(setup_db, old, 2)       # purgeable — terminal, >7d old
    await _add_logs(setup_db, running, 1)   # not purgeable — still running

    async with _client(setup_db, admin_user) as ac:
        r = await ac.get("/api/admin/maintenance/overview")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scans_active"] == 3  # 2 running + 1 pending
    assert body["scans_by_status"]["completed"] == 1
    assert body["scan_log_rows"] == 3
    assert body["scan_log_purgeable"] == 2
    assert body["meili_documents"] == 123


def _async_return(value):
    async def _coro():
        return value
    return _coro()


# — admin gate —

@pytest.mark.asyncio
async def test_non_admin_gets_403(setup_db, viewer_user):
    async with _client(setup_db, viewer_user) as ac:
        r = await ac.get("/api/admin/maintenance/overview")
    assert r.status_code == 403


# — stuck scans + cancel —

@pytest.mark.asyncio
async def test_stuck_scans_lists_only_non_terminal(setup_db, admin_user):
    src = await _make_source(setup_db)
    pending = await _make_scan(setup_db, src, status="pending")
    running = await _make_scan(setup_db, src, status="running")
    await _make_scan(setup_db, src, status="completed")
    await _make_scan(setup_db, src, status="failed")

    async with _client(setup_db, admin_user) as ac:
        r = await ac.get("/api/admin/maintenance/scans/stuck")
    assert r.status_code == 200, r.text
    ids = {row["scan_id"] for row in r.json()}
    assert ids == {str(pending), str(running)}


@pytest.mark.asyncio
async def test_admin_cancel_transitions_running_to_cancelled(setup_db, admin_user):
    src = await _make_source(setup_db)
    scan_id = await _make_scan(setup_db, src, status="running")

    async with _client(setup_db, admin_user) as ac:
        r = await ac.post(f"/api/admin/maintenance/scans/{scan_id}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"

    async with setup_db() as db:
        scan = await db.get(Scan, scan_id)
        assert scan.status == "cancelled"
        assert scan.cancellation_reason == "admin"


@pytest.mark.asyncio
async def test_cancel_unknown_scan_404(setup_db, admin_user):
    async with _client(setup_db, admin_user) as ac:
        r = await ac.post(f"/api/admin/maintenance/scans/{uuid.uuid4()}/cancel")
    assert r.status_code == 404


# — watchdog —

@pytest.mark.asyncio
async def test_watchdog_run_fails_a_stale_scan(setup_db, admin_user, monkeypatch):
    # The watchdog opens its own session via scheduler.async_session —
    # point that at the test DB so the pass operates on seeded rows.
    monkeypatch.setattr("akashic.scheduler.async_session", setup_db)
    src = await _make_source(setup_db)
    stale = await _make_scan(
        setup_db, src, status="running",
        started_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )

    async with _client(setup_db, admin_user) as ac:
        r = await ac.post("/api/admin/maintenance/watchdog/run")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active_after"] < body["active_before"]

    async with setup_db() as db:
        scan = await db.get(Scan, stale)
        assert scan.status == "failed"


# — log purge —

@pytest.mark.asyncio
async def test_purge_logs_by_age(setup_db, admin_user):
    src = await _make_source(setup_db)
    done = await _make_scan(
        setup_db, src, status="completed",
        completed_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    running = await _make_scan(setup_db, src, status="running")
    await _add_logs(setup_db, done, 3)
    await _add_logs(setup_db, running, 2)

    async with _client(setup_db, admin_user) as ac:
        r = await ac.post(
            "/api/admin/maintenance/logs/purge", json={"older_than_days": 0},
        )
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 3  # only the terminal scan's logs

    async with setup_db() as db:
        from sqlalchemy import func, select
        remaining = (await db.execute(
            select(func.count(ScanLogEntry.id))
        )).scalar()
        assert remaining == 2  # the running scan keeps its logs


@pytest.mark.asyncio
async def test_purge_logs_by_scan_id(setup_db, admin_user):
    src = await _make_source(setup_db)
    scan_id = await _make_scan(setup_db, src, status="running")
    await _add_logs(setup_db, scan_id, 4)

    async with _client(setup_db, admin_user) as ac:
        r = await ac.post(
            "/api/admin/maintenance/logs/purge", json={"scan_id": str(scan_id)},
        )
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 4


@pytest.mark.asyncio
async def test_purge_logs_requires_exactly_one_arg(setup_db, admin_user):
    async with _client(setup_db, admin_user) as ac:
        both = await ac.post(
            "/api/admin/maintenance/logs/purge",
            json={"older_than_days": 1, "scan_id": str(uuid.uuid4())},
        )
        neither = await ac.post("/api/admin/maintenance/logs/purge", json={})
    assert both.status_code == 400
    assert neither.status_code == 400


# — jobs —

@pytest.mark.asyncio
async def test_start_job_runs_and_succeeds(setup_db, admin_user, monkeypatch):
    # _run opens its own session via the runner module's async_session —
    # point it at the test DB, and stub the kind to a fast no-DB fn.
    monkeypatch.setattr(mj, "async_session", setup_db)

    async def _fake(_params):
        return 7

    monkeypatch.setitem(mj.JOB_KINDS, "warm_groups", _fake)

    async with _client(setup_db, admin_user) as ac:
        r = await ac.post(
            "/api/admin/maintenance/jobs", json={"kind": "warm_groups"},
        )
        assert r.status_code == 202, r.text
        job = r.json()
        assert job["kind"] == "warm_groups"
        assert job["status"] == "running"

        # The runner task finishes shortly after the response.
        for _ in range(50):
            await asyncio.sleep(0.02)
            jobs = (await ac.get("/api/admin/maintenance/jobs")).json()
            done = next((j for j in jobs if j["id"] == job["id"]), None)
            if done and done["status"] != "running":
                break
        assert done is not None
        assert done["status"] == "succeeded"
        assert done["result"] == {"rows_affected": 7}


@pytest.mark.asyncio
async def test_duplicate_job_kind_409(setup_db, admin_user):
    # A running job of the same kind already exists.
    async with setup_db() as db:
        db.add(MaintenanceJob(
            id=uuid.uuid4(), kind="reindex_search", status="running", params={},
        ))
        await db.commit()

    async with _client(setup_db, admin_user) as ac:
        r = await ac.post(
            "/api/admin/maintenance/jobs", json={"kind": "reindex_search"},
        )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_unknown_job_kind_400(setup_db, admin_user):
    async with _client(setup_db, admin_user) as ac:
        r = await ac.post(
            "/api/admin/maintenance/jobs", json={"kind": "not_a_real_job"},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_reconcile_orphans_fails_stuck_running_rows(setup_db, monkeypatch):
    monkeypatch.setattr(mj, "async_session", setup_db)
    async with setup_db() as db:
        job_id = uuid.uuid4()
        db.add(MaintenanceJob(
            id=job_id, kind="reindex_search", status="running", params={},
        ))
        await db.commit()

    count = await mj.reconcile_orphans()
    assert count == 1

    async with setup_db() as db:
        row = await db.get(MaintenanceJob, job_id)
        assert row.status == "failed"
        assert "restart" in (row.error or "")
        assert row.finished_at is not None
