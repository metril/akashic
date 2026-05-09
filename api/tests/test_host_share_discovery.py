"""Discover & batch-add shares (v0.5.4).

Endpoints under test:
- POST /api/hosts/{id}/list-shares
- POST /api/hosts/{id}/add-shares

The list-shares path normally spawns the bundled scanner subprocess;
we monkey-patch `share_enumerator.list_shares` so the test doesn't
depend on a binary being on PATH.
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
from akashic.models.audit_event import AuditEvent
from akashic.models.host import Host  # noqa: F401  # imported for relationship registration
from akashic.models.source import Source
from akashic.models.user import User
from akashic.routers import hosts as hosts_router
from akashic.services import share_enumerator
from akashic.services.source_tester import TestResult as ProbeResult


# ── Fixtures (mirrors test_hosts.py) ─────────────────────────────────────


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


async def _create_credential_profile(
    client: AsyncClient, *, name: str, type_: str, credentials: dict
) -> str:
    r = await client.post(
        "/api/credential-profiles",
        json={"name": name, "type": type_, "credentials": credentials},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _create_host(client: AsyncClient, *, type_: str = "smb") -> str:
    body = {
        "name": f"h-{uuid.uuid4().hex[:6]}",
        "type": type_,
    }
    if type_ == "smb":
        body["connection_config"] = {
            "host": "fs.example.com", "username": "scan", "password": "p",
        }
    elif type_ == "nfs":
        body["connection_config"] = {"host": "nfs.example.com"}
    elif type_ == "s3":
        body["connection_config"] = {
            "region": "us-east-1",
            "access_key_id": "AKIA...",
            "secret_access_key": "secret",
        }
    r = await client.post("/api/hosts", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── /list-shares ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_shares_smb_happy_path(
    client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(
        share_enumerator, "list_shares",
        lambda host_type, cfg: share_enumerator.ListSharesResult(
            shares=["Public", "Engineering", "Marketing"],
        ),
    )
    host_id = await _create_host(client, type_="smb")
    r = await client.post(f"/api/hosts/{host_id}/list-shares")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["shares"] == ["Public", "Engineering", "Marketing"]
    assert body["step"] is None
    assert body["error"] is None


@pytest.mark.asyncio
async def test_list_shares_propagates_step_reason_on_failure(
    client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(
        share_enumerator, "list_shares",
        lambda host_type, cfg: share_enumerator.ListSharesResult(
            shares=[], step="auth", error="bad password",
        ),
    )
    host_id = await _create_host(client, type_="smb")
    r = await client.post(f"/api/hosts/{host_id}/list-shares")
    assert r.status_code == 200
    body = r.json()
    assert body["shares"] == []
    assert body["step"] == "auth"
    assert body["error"] == "bad password"


@pytest.mark.asyncio
async def test_list_shares_unknown_host_returns_404(client: AsyncClient):
    r = await client.post(f"/api/hosts/{uuid.uuid4()}/list-shares")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_shares_writes_audit_event(
    client: AsyncClient, monkeypatch, setup_db, admin_user: User
):
    monkeypatch.setattr(
        share_enumerator, "list_shares",
        lambda host_type, cfg: share_enumerator.ListSharesResult(
            shares=["Public"],
        ),
    )
    host_id = await _create_host(client, type_="smb")
    await client.post(f"/api/hosts/{host_id}/list-shares")

    async with setup_db() as session:
        events = (await session.execute(
            select(AuditEvent).where(AuditEvent.event_type == "host_shares_listed")
        )).scalars().all()
    assert len(events) == 1
    assert events[0].user_id == admin_user.id
    assert events[0].payload["share_count"] == 1


# ── credential profile is layered into the probe (v0.26.0 regression) ───


@pytest.mark.asyncio
async def test_list_shares_layers_host_credential_profile(
    client: AsyncClient, monkeypatch
):
    """v0.26.0 regression: list-shares now layers
    host.credential_profile.credentials under host.connection_config.
    Pre-fix the profile was ignored and a profile-only host failed
    with "no credentials"."""
    profile_id = await _create_credential_profile(
        client,
        name="smb-deploy",
        type_="smb",
        credentials={"username": "scan_user", "password": "p@ssw0rd"},
    )
    body = {
        "name": "fs",
        "type": "smb",
        "connection_config": {"host": "fs.example.com"},
        "credential_profile_id": profile_id,
    }
    r = await client.post("/api/hosts", json=body)
    assert r.status_code == 201, r.text
    host_id = r.json()["id"]

    captured: dict = {}

    def _capture(host_type, cfg):
        captured["host_type"] = host_type
        captured["cfg"] = cfg
        return share_enumerator.ListSharesResult(shares=["Public"])

    monkeypatch.setattr(share_enumerator, "list_shares", _capture)

    r = await client.post(f"/api/hosts/{host_id}/list-shares")
    assert r.status_code == 200, r.text
    assert captured["host_type"] == "smb"
    # Inline host.connection_config still wins where it sets a key,
    # but profile credentials fill in the gaps.
    assert captured["cfg"]["host"] == "fs.example.com"
    assert captured["cfg"]["username"] == "scan_user"
    assert captured["cfg"]["password"] == "p@ssw0rd"


@pytest.mark.asyncio
async def test_test_connection_layers_host_credential_profile(
    client: AsyncClient, monkeypatch
):
    """Same regression for POST /api/hosts/{id}/test-connection.
    The probe was previously called with only host.connection_config."""
    profile_id = await _create_credential_profile(
        client,
        name="smb-deploy-2",
        type_="smb",
        credentials={"username": "scan_user", "password": "p@ssw0rd"},
    )
    body = {
        "name": "fs2",
        "type": "smb",
        "connection_config": {"host": "fs2.example.com"},
        "credential_profile_id": profile_id,
    }
    r = await client.post("/api/hosts", json=body)
    assert r.status_code == 201, r.text
    host_id = r.json()["id"]

    captured: dict = {}

    def _capture(host_type, cfg):
        captured["host_type"] = host_type
        captured["cfg"] = cfg
        return ProbeResult(ok=True)

    monkeypatch.setattr(hosts_router, "_probe_host", _capture)

    r = await client.post(f"/api/hosts/{host_id}/test-connection")
    assert r.status_code == 200, r.text
    assert captured["host_type"] == "smb"
    assert captured["cfg"]["host"] == "fs2.example.com"
    assert captured["cfg"]["username"] == "scan_user"
    assert captured["cfg"]["password"] == "p@ssw0rd"


# ── /add-shares ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_shares_creates_one_source_per_entry(
    client: AsyncClient, setup_db
):
    host_id = await _create_host(client, type_="smb")
    r = await client.post(
        f"/api/hosts/{host_id}/add-shares",
        json={
            "shares": [
                {"name": "fs/Public", "share": "Public"},
                {"name": "fs/Eng", "share": "Engineering"},
                {"name": "fs/Mkt", "share": "Marketing"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 3
    assert body["skipped"] == 0
    assert len(body["sources"]) == 3

    async with setup_db() as session:
        rows = (await session.execute(
            select(Source).where(Source.host_id == uuid.UUID(host_id))
        )).scalars().all()
    by_name = {r.name: r for r in rows}
    assert by_name["fs/Public"].connection_config == {"share": "Public"}
    assert by_name["fs/Eng"].connection_config == {"share": "Engineering"}
    # Each new source defaults to max_parallel_scanners=1 (legacy)
    # unless the batch request bumps it.
    assert all(r.max_parallel_scanners == 1 for r in rows)


@pytest.mark.asyncio
async def test_add_shares_skips_dupes_and_creates_the_rest(
    client: AsyncClient, setup_db
):
    host_id = await _create_host(client, type_="smb")
    # First batch
    await client.post(
        f"/api/hosts/{host_id}/add-shares",
        json={"shares": [{"name": "fs/Public", "share": "Public"}]},
    )
    # Second batch — one collision, one new
    r = await client.post(
        f"/api/hosts/{host_id}/add-shares",
        json={
            "shares": [
                {"name": "fs/Public", "share": "Public"},  # dupe
                {"name": "fs/Eng", "share": "Engineering"},  # new
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 1
    assert body["skipped"] == 1


@pytest.mark.asyncio
async def test_add_shares_for_nfs_uses_export_path_key(
    client: AsyncClient, setup_db
):
    host_id = await _create_host(client, type_="nfs")
    r = await client.post(
        f"/api/hosts/{host_id}/add-shares",
        json={
            "shares": [
                {"name": "nfs/data", "share": "/srv/nfs/data"},
                {"name": "nfs/backups", "share": "/srv/nfs/backups"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    async with setup_db() as session:
        rows = (await session.execute(
            select(Source).where(Source.host_id == uuid.UUID(host_id))
        )).scalars().all()
    by_name = {r.name: r for r in rows}
    assert by_name["nfs/data"].connection_config == {"export_path": "/srv/nfs/data"}
    assert by_name["nfs/backups"].connection_config == {"export_path": "/srv/nfs/backups"}


@pytest.mark.asyncio
async def test_add_shares_for_s3_uses_bucket_key(
    client: AsyncClient, setup_db
):
    host_id = await _create_host(client, type_="s3")
    r = await client.post(
        f"/api/hosts/{host_id}/add-shares",
        json={
            "shares": [
                {"name": "s3/logs", "share": "company-logs"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    async with setup_db() as session:
        row = (await session.execute(
            select(Source).where(Source.name == "s3/logs")
        )).scalar_one()
    assert row.connection_config == {"bucket": "company-logs"}


@pytest.mark.asyncio
async def test_add_shares_writes_rollup_audit_event(
    client: AsyncClient, monkeypatch, setup_db, admin_user: User
):
    host_id = await _create_host(client, type_="smb")
    await client.post(
        f"/api/hosts/{host_id}/add-shares",
        json={"shares": [{"name": "fs/Public", "share": "Public"}]},
    )
    async with setup_db() as session:
        events = (await session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == "host_shares_batch_added"
            )
        )).scalars().all()
    assert len(events) == 1
    assert events[0].user_id == admin_user.id
    assert events[0].payload["created"] == 1
    assert events[0].payload["skipped"] == 0


@pytest.mark.asyncio
async def test_add_shares_batch_carries_max_parallel_scanners(
    client: AsyncClient, setup_db
):
    host_id = await _create_host(client, type_="smb")
    await client.post(
        f"/api/hosts/{host_id}/add-shares",
        json={
            "shares": [{"name": "fs/Public", "share": "Public"}],
            "max_parallel_scanners": 4,
        },
    )
    async with setup_db() as session:
        row = (await session.execute(
            select(Source).where(Source.name == "fs/Public")
        )).scalar_one()
    assert row.max_parallel_scanners == 4


# ── share_enumerator unit (no subprocess) ────────────────────────────────


def test_share_enumerator_dispatch_unknown_type():
    res = share_enumerator.list_shares("ftp", {})
    assert res.shares == []
    assert res.step == "config"
    assert "ftp" in (res.error or "")
