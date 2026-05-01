"""POST /api/scans/lease — atomic claim semantics."""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.scan import Scan
from akashic.models.source import Source
from akashic.models.user import User
from akashic.protocol import PROTOCOL_VERSION
from akashic.services.scanner_keys import sign_jwt


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


def _admin_client(setup_db, user: User) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_get_current_user():
        return user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _bearer_client(setup_db) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _scanner_token(sid: str, priv: str) -> str:
    now = int(time.time())
    return sign_jwt(
        priv,
        {"iss": "scanner", "sub": sid, "iat": now, "exp": now + 300},
        headers={"kid": sid},
    )


async def _seed(setup_db, admin_user, *, source_pool: str | None,
                scanner_pool: str = "default") -> tuple[uuid.UUID, dict, dict]:
    """Returns (scan_id, source_dict, scanner_create_response)."""
    async with _admin_client(setup_db, admin_user) as ac:
        scanner_resp = await ac.post(
            "/api/scanners",
            json={"name": f"s-{uuid.uuid4().hex[:6]}", "pool": scanner_pool},
        )
    scanner_resp = scanner_resp.json()

    async with setup_db() as db:
        src = Source(
            id=uuid.uuid4(),
            name=f"src-{uuid.uuid4().hex[:6]}",
            type="local",
            connection_config={"path": "/tmp"},
            preferred_pool=source_pool,
        )
        db.add(src)
        await db.flush()
        scan = Scan(
            id=uuid.uuid4(),
            source_id=src.id,
            scan_type="incremental",
            status="pending",
            pool=source_pool,
        )
        db.add(scan)
        await db.commit()
        return scan.id, {
            "id": str(src.id),
            "type": src.type,
            "connection_config": src.connection_config,
        }, scanner_resp


@pytest.mark.asyncio
async def test_lease_returns_pending_scan(setup_db, admin_user):
    scan_id, src, scn = await _seed(setup_db, admin_user, source_pool=None)
    token = _scanner_token(scn["id"], scn["private_key_pem"])

    async with _bearer_client(setup_db) as ac:
        r = await ac.post(
            "/api/scans/lease", json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scan_id"] == str(scan_id)
    assert body["source"]["id"] == src["id"]
    assert body["api_jwt"]  # admin user exists, so the jwt is minted


@pytest.mark.asyncio
async def test_lease_returns_204_when_no_pending(setup_db, admin_user):
    # No scan seeded — only a scanner.
    async with _admin_client(setup_db, admin_user) as ac:
        scn = (await ac.post(
            "/api/scanners", json={"name": "s1", "pool": "default"},
        )).json()
    token = _scanner_token(scn["id"], scn["private_key_pem"])
    async with _bearer_client(setup_db) as ac:
        r = await ac.post(
            "/api/scans/lease", json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_pool_mismatch_returns_204(setup_db, admin_user):
    """Source preferred_pool=hq, scanner pool=default → no claim."""
    _scan, _src, scn = await _seed(
        setup_db, admin_user, source_pool="hq", scanner_pool="default",
    )
    token = _scanner_token(scn["id"], scn["private_key_pem"])
    async with _bearer_client(setup_db) as ac:
        r = await ac.post(
            "/api/scans/lease", json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_permissive_null_pool_can_be_claimed_by_any(setup_db, admin_user):
    _scan, _src, scn = await _seed(
        setup_db, admin_user, source_pool=None, scanner_pool="far-away",
    )
    token = _scanner_token(scn["id"], scn["private_key_pem"])
    async with _bearer_client(setup_db) as ac:
        r = await ac.post(
            "/api/scans/lease", json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_concurrent_leases_serialise_via_skip_locked(setup_db, admin_user):
    """Two scanners in the same pool race for one pending scan. The
    SKIP LOCKED + UPDATE … RETURNING pattern must hand the scan to
    exactly one of them; the other gets 204."""
    scan_id, _src, _ = await _seed(setup_db, admin_user, source_pool=None)

    async with _admin_client(setup_db, admin_user) as ac:
        s1 = (await ac.post(
            "/api/scanners", json={"name": "s1", "pool": "default"},
        )).json()
        s2 = (await ac.post(
            "/api/scanners", json={"name": "s2", "pool": "default"},
        )).json()
    t1 = _scanner_token(s1["id"], s1["private_key_pem"])
    t2 = _scanner_token(s2["id"], s2["private_key_pem"])

    async def lease(token: str):
        async with _bearer_client(setup_db) as ac:
            return await ac.post(
                "/api/scans/lease", json={},
                headers={"Authorization": f"Bearer {token}"},
            )

    r1, r2 = await asyncio.gather(lease(t1), lease(t2))
    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 204], f"got {statuses}"


@pytest.mark.asyncio
async def test_complete_releases_lease(setup_db, admin_user):
    scan_id, _src, scn = await _seed(setup_db, admin_user, source_pool=None)
    token = _scanner_token(scn["id"], scn["private_key_pem"])
    async with _bearer_client(setup_db) as ac:
        await ac.post(
            "/api/scans/lease", json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        r = await ac.post(
            f"/api/scans/{scan_id}/complete",
            json={"status": "completed"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 204

    # Re-lease should now return 204 because the scan is terminal.
    async with _bearer_client(setup_db) as ac:
        r2 = await ac.post(
            "/api/scans/lease", json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r2.status_code == 204
