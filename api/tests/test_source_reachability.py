"""External-drive awareness — is_removable inference, /check-reachability,
scan-complete updates last_reachable_at."""
from __future__ import annotations

import time
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from akashic.auth.dependencies import get_current_user, require_admin
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.audit_event import AuditEvent
from akashic.models.scan import Scan
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services import source_tester
from akashic.services.scanner_keys import sign_jwt
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

    async with setup_db() as session:
        src = (await session.execute(
            select(Source).where(Source.name == "usb-drive")
        )).scalar_one()
    assert src.is_removable is True


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


@pytest.mark.asyncio
async def test_check_reachability_happy_path(
    client: AsyncClient, setup_db, tmp_path
):
    """Probe ok=true → is_reachable=true, both timestamps set."""
    # Use a real on-disk path so the local test_connection succeeds.
    r = await client.post(
        "/api/sources",
        json={
            "name": "real-drive",
            "type": "local",
            "connection_config": {"path": str(tmp_path)},
            "is_removable": True,
        },
    )
    sid = r.json()["id"]

    r2 = await client.post(f"/api/sources/{sid}/check-reachability", json={})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["result"]["ok"] is True
    assert body["source"]["is_reachable"] is True
    assert body["source"]["last_reachable_at"] is not None
    assert body["source"]["last_reachability_check_at"] is not None

    async with setup_db() as session:
        src = (await session.execute(
            select(Source).where(Source.id == uuid.UUID(sid))
        )).scalar_one()
    assert src.is_reachable is True
    assert src.last_reachable_at is not None
    assert src.last_reachability_check_at is not None


@pytest.mark.asyncio
async def test_check_reachability_failure_does_not_bump_last_reachable_at(
    client: AsyncClient, setup_db
):
    """Probe ok=false → is_reachable=false; last_reachability_check_at
    updates (we did run a check) but last_reachable_at must NOT change."""
    r = await client.post(
        "/api/sources",
        json={
            "name": "missing-drive",
            "type": "local",
            "connection_config": {"path": "/nonexistent/path/xyzzy"},
            "is_removable": True,
        },
    )
    sid = r.json()["id"]

    r2 = await client.post(f"/api/sources/{sid}/check-reachability", json={})
    assert r2.status_code == 200
    body = r2.json()
    assert body["result"]["ok"] is False
    assert body["result"]["step"] == "list"
    assert body["source"]["is_reachable"] is False
    assert body["source"]["last_reachable_at"] is None
    assert body["source"]["last_reachability_check_at"] is not None


@pytest.mark.asyncio
async def test_check_reachability_failure_after_success_keeps_old_last_reachable(
    client: AsyncClient, setup_db, tmp_path
):
    """Drive was reached once, then unplugged. last_reachable_at must
    survive the failed probe so the UI can show "last seen 2h ago"."""
    r = await client.post(
        "/api/sources",
        json={
            "name": "plug-unplug",
            "type": "local",
            "connection_config": {"path": str(tmp_path)},
            "is_removable": True,
        },
    )
    sid = r.json()["id"]

    # First check — reachable, sets last_reachable_at.
    r1 = await client.post(f"/api/sources/{sid}/check-reachability", json={})
    first_seen = r1.json()["source"]["last_reachable_at"]
    assert first_seen is not None

    # Now break the path (mutate the source's connection_config so the
    # next probe fails). Direct DB mutation since the API masks the path.
    async with setup_db() as session:
        src = (await session.execute(
            select(Source).where(Source.id == uuid.UUID(sid))
        )).scalar_one()
        src.connection_config = {"path": "/nonexistent/now"}
        await session.commit()

    # Second check — unreachable; last_reachable_at must stay = first_seen.
    r2 = await client.post(f"/api/sources/{sid}/check-reachability", json={})
    body = r2.json()
    assert body["result"]["ok"] is False
    assert body["source"]["is_reachable"] is False
    assert body["source"]["last_reachable_at"] == first_seen


@pytest.mark.asyncio
async def test_check_reachability_records_audit_event(
    client: AsyncClient, setup_db, tmp_path
):
    r = await client.post(
        "/api/sources",
        json={
            "name": "audit-probe",
            "type": "local",
            "connection_config": {"path": str(tmp_path)},
        },
    )
    sid = r.json()["id"]

    await client.post(f"/api/sources/{sid}/check-reachability", json={})

    async with setup_db() as session:
        events = (await session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == "source_reachability_checked"
            )
        )).scalars().all()
    assert len(events) == 1
    assert events[0].payload["ok"] is True


@pytest.mark.asyncio
async def test_check_reachability_unknown_source_404(client: AsyncClient):
    r = await client.post(
        f"/api/sources/{uuid.uuid4()}/check-reachability", json={},
    )
    assert r.status_code == 404


def _scanner_token(sid: str, priv: str) -> str:
    now = int(time.time())
    return sign_jwt(
        priv,
        {"iss": "scanner", "sub": sid, "iat": now, "exp": now + 300},
        headers={"kid": sid},
    )


@pytest.mark.asyncio
async def test_scan_complete_bumps_last_reachable_at(
    client: AsyncClient, setup_db
):
    """A successful scan implies the source was reachable. Don't make
    the user click Check now to update the badge."""
    # Mint a scanner via the admin API (this is the only way to get a
    # matching keypair we can use for the bearer token).
    scn = (await client.post(
        "/api/scanners", json={"name": f"sc-{uuid.uuid4().hex[:6]}", "pool": "default"},
    )).json()

    # Seed a removable source with stale "unreachable" state, plus a
    # leased scan assigned to our scanner.
    async with setup_db() as session:
        src = Source(
            id=uuid.uuid4(),
            name="scanner-test-source",
            type="local",
            connection_config={"path": "/tmp/whatever"},
            is_removable=True,
            is_reachable=False,
            last_reachable_at=None,
        )
        session.add(src)
        await session.flush()
        src_id = src.id

        scan = Scan(
            id=uuid.uuid4(),
            source_id=src.id,
            scan_type="incremental",
            status="pending",
        )
        session.add(scan)
        await session.commit()
        scan_id = scan.id

    # Lease the scan to assign it to our scanner. The lease endpoint
    # uses the bearer-token auth path, so we bypass the test's
    # admin-user override by using a fresh client.
    token = _scanner_token(scn["id"], scn["private_key_pem"])

    async def _override_get_db():
        async with setup_db() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as bearer_ac:
        lease_r = await bearer_ac.post(
            "/api/scans/lease", json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert lease_r.status_code == 200, lease_r.text
        complete_r = await bearer_ac.post(
            f"/api/scans/{scan_id}/complete",
            json={"status": "completed"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert complete_r.status_code == 204, complete_r.text

    async with setup_db() as session:
        src = (await session.execute(
            select(Source).where(Source.id == src_id)
        )).scalar_one()
    assert src.is_reachable is True, "scan-complete should flip is_reachable=true"
    assert src.last_reachable_at is not None
    assert src.status == "online"


@pytest.mark.asyncio
async def test_check_reachability_uses_persisted_credentials(
    client: AsyncClient, setup_db, monkeypatch
):
    """Caller doesn't re-supply credentials — the endpoint pulls them
    from the persisted connection_config."""
    captured = {}

    def _fake_test_connection(source_type, cfg):
        captured["type"] = source_type
        captured["cfg"] = cfg
        return _TestResult(ok=True)

    monkeypatch.setattr(
        "akashic.routers.sources.test_connection", _fake_test_connection,
    )

    r = await client.post(
        "/api/sources",
        json={
            "name": "ssh-creds",
            "type": "smb",
            "connection_config": {
                "host": "h",
                "username": "u",
                "password": "secret-001",
                "known_hosts_path": "/k",
            },
        },
    )
    sid = r.json()["id"]

    r2 = await client.post(f"/api/sources/{sid}/check-reachability", json={})
    assert r2.status_code == 200
    assert r2.json()["result"]["ok"] is True
    assert captured["type"] == "smb"
    # Persisted creds — including the password — flow into the probe
    # untouched. The /test endpoint never sees them.
    assert captured["cfg"]["password"] == "secret-001"
