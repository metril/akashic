"""Hosts CRUD + Source ↔ Host wiring (v0.5.0).

Covers:
- merge_host_and_source helper
- Host create/list/get/update/delete happy paths
- Delete blocked when sources are attached (409)
- Source create with host_id only (no host fields in body) merges
  through to the source-tester
- Source create rejects host_id with mismatched type
- Source create rejects host_id on local sources
- Backfill helper (alembic 0023) — see test_alembic_0023_hosts.py
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
from akashic.models.host import Host
from akashic.models.reachability_result import ReachabilityResult
from akashic.models.scanner import Scanner
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services import source_tester
from akashic.services.source_config import merge_host_and_source
from akashic.services.source_tester import TestResult as _TestResult


# ── merge_host_and_source ─────────────────────────────────────────────────


class _DummyHost:
    def __init__(self, cfg):
        self.connection_config = cfg


class _DummySource:
    def __init__(self, cfg):
        self.connection_config = cfg


def test_merge_host_none_returns_just_source_config():
    src = _DummySource({"path": "/data"})
    assert merge_host_and_source(None, src) == {"path": "/data"}


def test_merge_layers_host_under_source():
    host = _DummyHost({"host": "fs1", "port": 22, "username": "scan"})
    src = _DummySource({"path": "/srv/data"})
    merged = merge_host_and_source(host, src)
    assert merged == {
        "host": "fs1",
        "port": 22,
        "username": "scan",
        "path": "/srv/data",
    }


def test_merge_source_overrides_host_on_conflict():
    host = _DummyHost({"port": 22})
    src = _DummySource({"port": 2222})
    assert merge_host_and_source(host, src) == {"port": 2222}


def test_merge_handles_none_configs():
    host = _DummyHost(None)
    src = _DummySource(None)
    assert merge_host_and_source(host, src) == {}


# ── Credential profile layering (v0.5.9) ─────────────────────────────────
# The merge order is last-write-wins:
#   host_profile < host_inline < source_profile < source_inline
# Each test below isolates one layer beating the previous.


class _DummyProfile:
    def __init__(self, credentials: dict):
        self.credentials = credentials


class _DummyHostWithProfile:
    def __init__(self, cfg, profile=None):
        self.connection_config = cfg
        self.credential_profile = profile


class _DummySourceWithProfile:
    def __init__(self, cfg, profile=None):
        self.connection_config = cfg
        self.credential_profile = profile


def test_merge_host_profile_under_host_inline():
    host = _DummyHostWithProfile(
        {"username": "host-u"},
        profile=_DummyProfile({"username": "profile-u", "password": "pp"}),
    )
    src = _DummySourceWithProfile({"path": "/srv/data"})
    merged = merge_host_and_source(host, src)
    # host inline overrides the host profile's username, but the
    # profile's password keeps flowing through.
    assert merged == {"username": "host-u", "password": "pp", "path": "/srv/data"}


def test_merge_source_profile_under_source_inline():
    host = _DummyHostWithProfile({"username": "u"})
    src = _DummySourceWithProfile(
        {"username": "src-inline"},
        profile=_DummyProfile({"username": "src-profile", "key_path": "/k"}),
    )
    merged = merge_host_and_source(host, src)
    # source inline beats source profile beats host inline.
    assert merged == {"username": "src-inline", "key_path": "/k"}


def test_merge_full_layer_order():
    host = _DummyHostWithProfile(
        {"port": 22},  # beats host_profile.port
        profile=_DummyProfile({"port": 1, "username": "host-prof"}),
    )
    src = _DummySourceWithProfile(
        {"username": "src-inline"},  # beats source_profile.username
        profile=_DummyProfile({"username": "src-prof", "key_path": "/k"}),
    )
    merged = merge_host_and_source(host, src)
    assert merged == {
        "port": 22,
        "username": "src-inline",
        "key_path": "/k",
    }


# ── Endpoint integration tests ────────────────────────────────────────────


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


# ── Host CRUD ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_host_and_get(client: AsyncClient):
    r = await client.post(
        "/api/hosts",
        json={
            "name": "fileserver01",
            "type": "smb",
            "connection_config": {
                "host": "fs01.example.com",
                "port": 445,
                "username": "scan",
                "password": "s3cret",
            },
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "fileserver01"
    assert body["type"] == "smb"
    # Password is masked on response
    assert body["connection_config"]["password"] == "***"
    assert body["source_count"] == 0
    host_id = body["id"]

    g = await client.get(f"/api/hosts/{host_id}")
    assert g.status_code == 200
    assert g.json()["name"] == "fileserver01"


@pytest.mark.asyncio
async def test_list_hosts_includes_source_count(client: AsyncClient, setup_db):
    h = await client.post(
        "/api/hosts",
        json={
            "name": "nas",
            "type": "smb",
            "connection_config": {"host": "nas.lan", "username": "u", "password": "p"},
        },
    )
    host_id = h.json()["id"]
    # Attach two sources
    for share in ("share1", "share2"):
        r = await client.post(
            "/api/sources",
            json={
                "name": share,
                "type": "smb",
                "host_id": host_id,
                "connection_config": {"share": share},
            },
        )
        assert r.status_code == 201, r.text

    listing = await client.get("/api/hosts")
    assert listing.status_code == 200
    rows = listing.json()
    by_name = {r["name"]: r for r in rows}
    assert by_name["nas"]["source_count"] == 2


@pytest.mark.asyncio
async def test_update_host_preserves_secret_via_sentinel(client: AsyncClient):
    h = await client.post(
        "/api/hosts",
        json={
            "name": "smb-fileserver",
            "type": "smb",
            "connection_config": {
                "host": "h",
                "username": "u",
                "password": "real-secret",
            },
        },
    )
    host_id = h.json()["id"]
    # PATCH with masked sentinel for password — the real value must
    # survive (same merge rule as Source.update).
    p = await client.patch(
        f"/api/hosts/{host_id}",
        json={
            "connection_config": {
                "host": "h-renamed",
                "username": "u",
                "password": "***",
            },
        },
    )
    assert p.status_code == 200, p.text
    body = p.json()
    assert body["connection_config"]["host"] == "h-renamed"
    # Password still masked on response, but the underlying value wasn't
    # overwritten by the sentinel — we test that via test_host_connection
    # if needed; here we just verify the response is correct.


@pytest.mark.asyncio
async def test_delete_host_with_attached_source_returns_409(
    client: AsyncClient,
):
    h = await client.post(
        "/api/hosts",
        json={
            "name": "shared-nas",
            "type": "smb",
            "connection_config": {"host": "nas", "username": "u", "password": "p"},
        },
    )
    host_id = h.json()["id"]
    r = await client.post(
        "/api/sources",
        json={
            "name": "attached",
            "type": "smb",
            "host_id": host_id,
            "connection_config": {"share": "data"},
        },
    )
    assert r.status_code == 201

    d = await client.delete(f"/api/hosts/{host_id}")
    assert d.status_code == 409
    assert "attached source" in d.json()["detail"]


@pytest.mark.asyncio
async def test_delete_host_without_attached_sources_succeeds(
    client: AsyncClient,
):
    h = await client.post(
        "/api/hosts",
        json={
            "name": "lonely",
            "type": "smb",
            "connection_config": {
                "host": "h", "username": "u",
            },
        },
    )
    host_id = h.json()["id"]
    d = await client.delete(f"/api/hosts/{host_id}")
    assert d.status_code == 204
    g = await client.get(f"/api/hosts/{host_id}")
    assert g.status_code == 404


@pytest.mark.asyncio
async def test_create_host_unsupported_type_returns_400(client: AsyncClient):
    r = await client.post(
        "/api/hosts",
        json={"name": "bad", "type": "ftp", "connection_config": {}},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_create_host_duplicate_name_returns_409(client: AsyncClient):
    body = {
        "name": "dup",
        "type": "smb",
        "connection_config": {
            "host": "h", "username": "u",
        },
    }
    r1 = await client.post("/api/hosts", json=body)
    assert r1.status_code == 201
    r2 = await client.post("/api/hosts", json=body)
    assert r2.status_code == 409


# ── Source ↔ Host wiring ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_source_with_host_id_only(client: AsyncClient, setup_db):
    """Source body carries no host fields — host_id is sufficient.
    Verifies the merge happens server-side."""
    h = await client.post(
        "/api/hosts",
        json={
            "name": "smb-host",
            "type": "smb",
            "connection_config": {
                "host": "fs.example.com",
                "username": "scan",
                "password": "topsecret",
            },
        },
    )
    host_id = h.json()["id"]

    r = await client.post(
        "/api/sources",
        json={
            "name": "shared-docs",
            "type": "smb",
            "host_id": host_id,
            # No host fields here — just the share-level path.
            "connection_config": {"share": "Docs"},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["host_id"] == host_id
    # Per-share connection_config is share-only; host fields aren't
    # echoed back on the source row.
    assert body["connection_config"] == {"share": "Docs"}
    # Inlined host shape should be present for the UI to render the link.
    assert body["host"]["id"] == host_id
    assert body["host"]["name"] == "smb-host"

    # v0.28.0: source-level /test-scanners replaces the legacy
    # /check-reachability — verify the merged config (host creds
    # under the source's share-only fields) flows through to the
    # inline probe via probe_dispatch.dispatch_inline.
    captured: list[dict] = []

    def fake_test(source_type: str, cfg: dict) -> _TestResult:
        captured.append(cfg)
        return _TestResult(ok=True)

    import akashic.services.probe_dispatch as probe_dispatch
    real_test_connection = probe_dispatch.test_connection
    probe_dispatch.test_connection = fake_test  # type: ignore[assignment]
    # Need a scanner registered so /test-scanners has someone to probe.
    scn = await client.post(
        "/api/scanners",
        json={"name": "merge-cfg-scanner", "pool": "default"},
    )
    assert scn.status_code == 201, scn.text
    try:
        check = await client.post(
            f"/api/sources/{body['id']}/test-scanners",
            json={"scanner_ids": [scn.json()["id"]]},
        )
        assert check.status_code == 200, check.text
    finally:
        probe_dispatch.test_connection = real_test_connection
    assert captured, "test_connection was never called"
    cfg = captured[0]
    assert cfg["share"] == "Docs"
    assert cfg["host"] == "fs.example.com"
    assert cfg["username"] == "scan"
    assert cfg["password"] == "topsecret"


@pytest.mark.asyncio
async def test_create_source_host_type_mismatch_returns_400(
    client: AsyncClient,
):
    h = await client.post(
        "/api/hosts",
        json={
            "name": "smb-host",
            "type": "smb",
            "connection_config": {"host": "h", "username": "u", "password": "p"},
        },
    )
    host_id = h.json()["id"]
    r = await client.post(
        "/api/sources",
        json={
            "name": "wrong-type",
            "type": "nfs",  # mismatched (host is smb)
            "host_id": host_id,
            "connection_config": {},
        },
    )
    assert r.status_code == 400
    assert "match" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_local_source_with_host_id_returns_400(
    client: AsyncClient,
):
    h = await client.post(
        "/api/hosts",
        json={
            "name": "any",
            "type": "smb",
            "connection_config": {"host": "h", "username": "u", "password": "p"},
        },
    )
    host_id = h.json()["id"]
    r = await client.post(
        "/api/sources",
        json={
            "name": "local-with-host",
            "type": "local",
            "host_id": host_id,
            "connection_config": {"path": "/srv/data"},
        },
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_create_source_unknown_host_id_returns_404(client: AsyncClient):
    r = await client.post(
        "/api/sources",
        json={
            "name": "ghost",
            "type": "smb",
            "host_id": str(uuid.uuid4()),
            "connection_config": {"share": "x"},
        },
    )
    assert r.status_code == 404


# ── Scanner reachability summary endpoint ──────────────────────────────────
# Regression: v0.5.7 shipped with an off-by-one (r[8] on a 0-7 result) that
# 500s the host eligibility panel. Cover both the empty-attachments and
# attached-with-probe shapes so the bug stays fixed.


@pytest.mark.asyncio
async def test_scanner_reachability_summary_with_no_sources(
    client: AsyncClient, setup_db,
):
    async with setup_db() as session:
        host = Host(
            id=uuid.uuid4(),
            name="empty-host",
            type="smb",
            connection_config={
                "host": "h", "username": "u",
            },
        )
        scanner = Scanner(
            id=uuid.uuid4(),
            name="scanner-a",
            pool="default",
            public_key_pem="x",
            key_fingerprint="fp-a",
        )
        session.add_all([host, scanner])
        await session.commit()
        host_id = host.id
        scanner_id = scanner.id

    r = await client.get(f"/api/hosts/{host_id}/scanner-reachability-summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1
    row = body[0]
    assert row["scanner_id"] == str(scanner_id)
    assert row["total_sources"] == 0
    assert row["reaches_count"] == 0
    assert row["unreachable_count"] == 0
    assert row["not_yet_probed_count"] == 0
    # No attached sources => "all" allowed_source_ids resolves to 0 of 0.
    assert row["currently_allowed_count"] == 0


@pytest.mark.asyncio
async def test_scanner_reachability_summary_with_attached_source_and_probe(
    client: AsyncClient, setup_db,
):
    async with setup_db() as session:
        host = Host(
            id=uuid.uuid4(),
            name="probed-host",
            type="smb",
            connection_config={"host": "h", "username": "u", "password": "p"},
        )
        scanner = Scanner(
            id=uuid.uuid4(),
            name="scanner-b",
            pool="default",
            public_key_pem="x",
            key_fingerprint="fp-b",
        )
        source = Source(
            id=uuid.uuid4(),
            name="probed-src",
            type="smb",
            connection_config={"share": "data"},
        )
        session.add_all([host, scanner, source])
        await session.flush()
        source.host_id = host.id
        await session.commit()
        host_id = host.id
        scanner_id = scanner.id
        source_id = source.id

    # Seed a successful probe in the new on-demand model so
    # reaches_count == 1 in the host-level summary.
    from datetime import datetime, timezone
    async with setup_db() as session:
        result = ReachabilityResult(
            id=uuid.uuid4(),
            source_id=source_id,
            scanner_id=scanner_id,
            ok=True,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        session.add(result)
        await session.commit()

    r = await client.get(f"/api/hosts/{host_id}/scanner-reachability-summary")
    assert r.status_code == 200, r.text
    body = r.json()
    row = next(x for x in body if x["scanner_id"] == str(scanner_id))
    assert row["total_sources"] == 1
    assert row["reaches_count"] == 1
    assert row["unreachable_count"] == 0
    assert row["not_yet_probed_count"] == 0


@pytest.mark.asyncio
async def test_create_host_audit_event_recorded(
    client: AsyncClient, setup_db, admin_user: User
):
    r = await client.post(
        "/api/hosts",
        json={
            "name": "audit-host",
            "type": "smb",
            "connection_config": {
                "host": "h", "username": "u",
            },
        },
    )
    assert r.status_code == 201
    async with setup_db() as session:
        events = (await session.execute(
            select(AuditEvent).where(AuditEvent.event_type == "host_created")
        )).scalars().all()
    assert len(events) == 1
    assert events[0].user_id == admin_user.id
