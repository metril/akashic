"""External-drive awareness + on-demand reachability (v0.28.0).

Tests the `infer_is_removable` heuristic and the new on-demand
reachability surface that replaces the v0.5.6 continuous-poll model:

  * POST /api/sources/{id}/test-scanners — runs probes inline (non-
    local) or dispatches to scanners over long-poll (local).
  * GET /api/sources/{id}/reachability-summary — derives source-level
    reachability from the latest probe + latest successful scan.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from akashic.auth.dependencies import get_current_user, require_admin
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.reachability_result import ReachabilityResult
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services.source_defaults import infer_is_removable
# Aliased so pytest doesn't try to collect it as a test class.
from akashic.services.source_tester import TestResult as _TestResult


# ── infer_is_removable ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "source_type,config,expected",
    [
        # USB / external mount conventions → true
        ("local", {"path": "/media/usb1"}, True),
        ("local", {"path": "/media/jagannath/SeagateExt"}, True),
        ("local", {"path": "/run/media/user/disk"}, True),
        ("local", {"path": "/mnt/external"}, True),
        ("local", {"path": "/Volumes/MyDrive"}, True),
        # Fixed local roots → false
        ("local", {"path": "/srv/data"}, False),
        ("local", {"path": "/home/user/files"}, False),
        ("local", {"path": "/var/backups"}, False),
        ("local", {"path": "/"}, False),
        ("local", {"path": ""}, False),
        ("local", {}, False),
        # Network sources opt in via UI; default to false even though
        # they could be intermittent.
        ("smb", {"host": "h", "share": "s"}, False),
        ("nfs", {"host": "h", "export_path": "/e"}, False),
        ("s3", {"bucket": "b", "region": "us-east-1"}, False),
        # Unknown type → false (no inference)
        ("ftp", {"path": "/media/x"}, False),
    ],
)
def test_infer_is_removable(source_type, config, expected):
    assert infer_is_removable(source_type, config) is expected


def test_infer_is_removable_handles_none_config():
    assert infer_is_removable("local", None) is False


# ── Endpoint integration tests ──────────────────────────────────────────────


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
async def client(setup_db, admin_user: User) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_get_current_user():
        return admin_user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    app.dependency_overrides[require_admin] = _override_get_current_user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_create_source_infers_is_removable_for_usb_path(
    client: AsyncClient, setup_db
):
    r = await client.post(
        "/api/sources",
        json={
            "name": "usb-drive",
            "type": "local",
            "connection_config": {"path": "/media/usb1"},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_removable"] is True


@pytest.mark.asyncio
async def test_create_source_infers_false_for_fixed_local_path(
    client: AsyncClient,
):
    r = await client.post(
        "/api/sources",
        json={
            "name": "fixed",
            "type": "local",
            "connection_config": {"path": "/srv/data"},
        },
    )
    assert r.status_code == 201
    assert r.json()["is_removable"] is False


@pytest.mark.asyncio
async def test_create_source_explicit_is_removable_overrides_inference(
    client: AsyncClient,
):
    """User explicitly set is_removable=true on a fixed-looking path —
    the explicit value wins over the inference default."""
    r = await client.post(
        "/api/sources",
        json={
            "name": "fixed-but-flagged",
            "type": "local",
            "connection_config": {"path": "/srv/data"},
            "is_removable": True,
        },
    )
    assert r.status_code == 201
    assert r.json()["is_removable"] is True


# ── /test-scanners (on-demand reachability) ─────────────────────────────


@pytest.mark.asyncio
async def test_test_scanners_inline_for_non_local_source_writes_results(
    client: AsyncClient, setup_db, monkeypatch,
):
    """Non-local sources are probed inline by the API. Each requested
    scanner gets one reachability_results row attributed to it."""
    # Stub out the actual network probe so the test doesn't need a
    # real SMB server.
    captured: list[dict] = []

    def fake_test(source_type: str, cfg: dict) -> _TestResult:
        captured.append({"type": source_type, "cfg": cfg})
        return _TestResult(ok=True)

    import akashic.services.probe_dispatch as probe_dispatch
    monkeypatch.setattr(probe_dispatch, "test_connection", fake_test, raising=False)

    # Create a source + two scanners.
    src_r = await client.post(
        "/api/sources",
        json={
            "name": "smb-src",
            "type": "smb",
            "connection_config": {
                "host": "h", "share": "s", "username": "u", "password": "p",
            },
        },
    )
    sid = src_r.json()["id"]

    scn_a = (await client.post(
        "/api/scanners", json={"name": "sc-a", "pool": "default"},
    )).json()
    scn_b = (await client.post(
        "/api/scanners", json={"name": "sc-b", "pool": "default"},
    )).json()

    r = await client.post(
        f"/api/sources/{sid}/test-scanners",
        json={"scanner_ids": [scn_a["id"], scn_b["id"]]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["results"]) == 2
    for row in body["results"]:
        assert row["ok"] is True
        assert row["pending"] is False

    async with setup_db() as session:
        rows = (await session.execute(
            select(ReachabilityResult).where(
                ReachabilityResult.source_id == uuid.UUID(sid),
            )
        )).scalars().all()
    assert {r.scanner_id for r in rows} == {
        uuid.UUID(scn_a["id"]), uuid.UUID(scn_b["id"]),
    }
    assert all(r.ok for r in rows)
    # The probe was actually called twice (once per scanner).
    assert len(captured) == 2


@pytest.mark.asyncio
async def test_test_scanners_local_source_returns_pending_when_no_agent(
    client: AsyncClient, setup_db,
):
    """Local sources need a scanner agent to probe — without one, the
    dispatcher times out (5 s) and the row comes back pending=True."""
    src_r = await client.post(
        "/api/sources",
        json={
            "name": "local-src",
            "type": "local",
            "connection_config": {"path": "/tmp/nonexistent"},
        },
    )
    sid = src_r.json()["id"]
    scn = (await client.post(
        "/api/scanners", json={"name": "no-agent", "pool": "default"},
    )).json()

    # Patch the dispatcher's timeout to ~250 ms so the test isn't slow
    # — without a live scanner the long-poll just won't deliver.
    import akashic.services.probe_dispatch as probe_dispatch
    real_dispatch_remote = probe_dispatch.dispatch_remote

    async def fast_dispatch_remote(*, source, scanner_ids, timeout_s=5.0):
        return await real_dispatch_remote(
            source=source, scanner_ids=scanner_ids, timeout_s=0.25,
        )

    probe_dispatch.dispatch_remote = fast_dispatch_remote
    try:
        r = await client.post(
            f"/api/sources/{sid}/test-scanners",
            json={"scanner_ids": [scn["id"]]},
        )
    finally:
        probe_dispatch.dispatch_remote = real_dispatch_remote

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["results"]) == 1
    assert body["results"][0]["pending"] is True


@pytest.mark.asyncio
async def test_test_scanners_unknown_source_404(client: AsyncClient):
    r = await client.post(
        f"/api/sources/{uuid.uuid4()}/test-scanners",
        json={"scanner_ids": []},
    )
    assert r.status_code == 404


# ── /reachability-summary ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reachability_summary_unchecked_when_no_data(
    client: AsyncClient,
):
    src_r = await client.post(
        "/api/sources",
        json={
            "name": "fresh",
            "type": "local",
            "connection_config": {"path": "/tmp"},
        },
    )
    sid = src_r.json()["id"]

    r = await client.get(f"/api/sources/{sid}/reachability-summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is None
    assert body["last_at"] is None


@pytest.mark.asyncio
async def test_reachability_summary_reflects_latest_probe(
    client: AsyncClient, setup_db,
):
    """Latest reachability_results row (across all scanners) drives the
    badge state."""
    src_r = await client.post(
        "/api/sources",
        json={
            "name": "probed",
            "type": "smb",
            "connection_config": {"host": "h", "share": "s", "username": "u"},
        },
    )
    sid = src_r.json()["id"]

    from datetime import datetime, timezone
    async with setup_db() as session:
        session.add(ReachabilityResult(
            id=uuid.uuid4(), source_id=uuid.UUID(sid), scanner_id=None,
            ok=False, step="auth", error="bad creds",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        ))
        await session.commit()

    r = await client.get(f"/api/sources/{sid}/reachability-summary")
    body = r.json()
    assert body["ok"] is False
    assert body["last_step"] == "auth"
    assert body["last_error"] == "bad creds"
