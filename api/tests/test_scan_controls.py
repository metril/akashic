"""Scan-distribution controls — host → source inheritance (v0.35.0).

`max_parallel_scanners` and `scan_chunk_size` resolve through
``source ?? host ?? built-in default``. The resolvers collapse that
two-level lookup; the lease payload must carry the *resolved* ints so
the scanner never has to know about host inheritance.
"""
from __future__ import annotations

import time
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.host import Host
from akashic.models.scan import Scan
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services.scanner_keys import sign_jwt
from akashic.services.source_config import (
    DEFAULT_MAX_PARALLEL_SCANNERS,
    DEFAULT_SCAN_CHUNK_SIZE,
    effective_chunk_size,
    effective_max_parallel_scanners,
    validate_scan_controls,
)


# ── Resolver unit tests ──────────────────────────────────────────────────


def _src(value, host_value):
    """A source-shaped object: its own field, plus an optional host."""
    host = None if host_value is _NO_HOST else SimpleNamespace(
        max_parallel_scanners=host_value, scan_chunk_size=host_value,
    )
    return SimpleNamespace(
        max_parallel_scanners=value, scan_chunk_size=value, host=host,
    )


_NO_HOST = object()


def test_resolver_prefers_source_value():
    src = _src(value=4, host_value=9)
    assert effective_max_parallel_scanners(src) == 4
    assert effective_chunk_size(src) == 4


def test_resolver_falls_back_to_host_when_source_null():
    src = _src(value=None, host_value=7)
    assert effective_max_parallel_scanners(src) == 7
    assert effective_chunk_size(src) == 7


def test_resolver_uses_default_when_source_and_host_null():
    src = _src(value=None, host_value=None)
    assert effective_max_parallel_scanners(src) == DEFAULT_MAX_PARALLEL_SCANNERS
    assert effective_chunk_size(src) == DEFAULT_SCAN_CHUNK_SIZE


def test_resolver_uses_default_when_no_host_attached():
    src = _src(value=None, host_value=_NO_HOST)
    assert effective_max_parallel_scanners(src) == DEFAULT_MAX_PARALLEL_SCANNERS
    assert effective_chunk_size(src) == DEFAULT_SCAN_CHUNK_SIZE


def test_validate_scan_controls_accepts_in_range_and_null():
    assert validate_scan_controls() is None
    assert validate_scan_controls(
        max_parallel_scanners=None, scan_chunk_size=None,
    ) is None
    assert validate_scan_controls(
        max_parallel_scanners=16, scan_chunk_size=1_000_000,
    ) is None
    assert validate_scan_controls(
        max_parallel_scanners=1, scan_chunk_size=100,
    ) is None


def test_validate_scan_controls_rejects_out_of_range():
    assert "max_parallel_scanners" in (
        validate_scan_controls(max_parallel_scanners=0) or ""
    )
    assert "max_parallel_scanners" in (
        validate_scan_controls(max_parallel_scanners=17) or ""
    )
    assert "scan_chunk_size" in (
        validate_scan_controls(scan_chunk_size=99) or ""
    )
    assert "scan_chunk_size" in (
        validate_scan_controls(scan_chunk_size=1_000_001) or ""
    )


# ── Lease-payload integration tests ──────────────────────────────────────


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


def _client(setup_db, user: User | None = None) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _scanner_token(sid: str, priv: str) -> str:
    now = int(time.time())
    return sign_jwt(
        priv,
        {"iss": "scanner", "sub": sid, "iat": now, "exp": now + 300},
        headers={"kid": sid},
    )


async def _seed_host_backed_scan(
    setup_db, admin_user, *,
    host_cap: int | None, host_chunk: int | None,
    source_cap: int | None, source_chunk: int | None,
) -> tuple[uuid.UUID, dict]:
    """Seed an SMB host + attached source + pending scan. Returns
    (scan_id, scanner_create_response)."""
    async with _client(setup_db, admin_user) as ac:
        scanner = (await ac.post(
            "/api/scanners",
            json={"name": f"s-{uuid.uuid4().hex[:6]}", "pool": "default"},
        )).json()

    async with setup_db() as db:
        host = Host(
            id=uuid.uuid4(),
            name=f"host-{uuid.uuid4().hex[:6]}",
            type="smb",
            connection_config={"host": "10.0.0.1", "username": "u",
                               "password": "p"},
            max_parallel_scanners=host_cap,
            scan_chunk_size=host_chunk,
        )
        db.add(host)
        await db.flush()
        src = Source(
            id=uuid.uuid4(),
            name=f"src-{uuid.uuid4().hex[:6]}",
            type="smb",
            host_id=host.id,
            connection_config={"share": "data"},
            max_parallel_scanners=source_cap,
            scan_chunk_size=source_chunk,
        )
        db.add(src)
        await db.flush()
        scan = Scan(
            id=uuid.uuid4(),
            source_id=src.id,
            scan_type="incremental",
            status="pending",
        )
        db.add(scan)
        await db.commit()
        return scan.id, scanner


@pytest.mark.asyncio
async def test_lease_payload_inherits_host_controls(setup_db, admin_user):
    # Source leaves both controls NULL → the lease payload must carry
    # the host's values, resolved API-side.
    scan_id, scn = await _seed_host_backed_scan(
        setup_db, admin_user,
        host_cap=3, host_chunk=777, source_cap=None, source_chunk=None,
    )
    token = _scanner_token(scn["id"], scn["private_key_pem"])
    async with _client(setup_db) as ac:
        r = await ac.post(
            "/api/scans/lease", json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200, r.text
    src = r.json()["source"]
    assert src["max_parallel_scanners"] == 3
    assert src["scan_chunk_size"] == 777


@pytest.mark.asyncio
async def test_lease_payload_source_overrides_host(setup_db, admin_user):
    # An explicit source value wins over the host's.
    scan_id, scn = await _seed_host_backed_scan(
        setup_db, admin_user,
        host_cap=3, host_chunk=777, source_cap=2, source_chunk=500,
    )
    token = _scanner_token(scn["id"], scn["private_key_pem"])
    async with _client(setup_db) as ac:
        r = await ac.post(
            "/api/scans/lease", json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200, r.text
    src = r.json()["source"]
    assert src["max_parallel_scanners"] == 2
    assert src["scan_chunk_size"] == 500


@pytest.mark.asyncio
async def test_lease_payload_uses_defaults_when_unset(setup_db, admin_user):
    # Neither host nor source pins a value → built-in defaults.
    scan_id, scn = await _seed_host_backed_scan(
        setup_db, admin_user,
        host_cap=None, host_chunk=None, source_cap=None, source_chunk=None,
    )
    token = _scanner_token(scn["id"], scn["private_key_pem"])
    async with _client(setup_db) as ac:
        r = await ac.post(
            "/api/scans/lease", json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200, r.text
    src = r.json()["source"]
    assert src["max_parallel_scanners"] == DEFAULT_MAX_PARALLEL_SCANNERS
    assert src["scan_chunk_size"] == DEFAULT_SCAN_CHUNK_SIZE
