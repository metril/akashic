"""POST /api/scanners/discover + admin approve/deny.

Exercises the full pending-claim flow:

  scanner POSTs /discover     →  pending row + pairing code
  admin POSTs /approve         →  Scanner row created
  scanner GETs /discover/{id}  →  resolves with scanner_id
  admin POSTs /deny            →  resolves with denied
  no-op when discovery toggle is off  →  404
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.user import User
from akashic.routers import scanner_discovery as sd_module
from akashic.services.scanner_keys import generate_keypair
from akashic.services.server_settings import (
    KEY_DISCOVERY_ENABLED, invalidate_all, set_setting,
)


@pytest_asyncio.fixture(autouse=True)
def _reset_state():
    invalidate_all()
    sd_module._rate_buckets.clear()
    yield
    invalidate_all()
    sd_module._rate_buckets.clear()


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


def _unauth_client(setup_db) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _enable_discovery(setup_db) -> None:
    async with setup_db() as session:
        await set_setting(session, KEY_DISCOVERY_ENABLED, True)
        await session.commit()


@pytest.mark.asyncio
async def test_discovery_disabled_returns_404(setup_db):
    """Default state has no row, so is_discovery_enabled returns False."""
    async with _unauth_client(setup_db) as ac:
        r = await ac.post(
            "/api/scanners/discover",
            json={"public_key_pem": generate_keypair().public_pem},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_post_discover_creates_pending_request(setup_db):
    await _enable_discovery(setup_db)
    kp = generate_keypair()
    async with _unauth_client(setup_db) as ac:
        r = await ac.post(
            "/api/scanners/discover",
            json={
                "public_key_pem": kp.public_pem,
                "hostname": "homeserver",
                "agent_version": "0.3.0",
                "requested_pool": "media",
            },
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "discovery_id" in body
    # Pairing code is `XXXX-XXXX` Crockford base32.
    code = body["pairing_code"]
    assert len(code) == 9 and code[4] == "-"


@pytest.mark.asyncio
async def test_post_discover_is_idempotent_per_pubkey(setup_db):
    await _enable_discovery(setup_db)
    kp = generate_keypair()
    async with _unauth_client(setup_db) as ac:
        r1 = (await ac.post(
            "/api/scanners/discover",
            json={"public_key_pem": kp.public_pem},
        )).json()
        r2 = (await ac.post(
            "/api/scanners/discover",
            json={"public_key_pem": kp.public_pem},
        )).json()
    assert r1["discovery_id"] == r2["discovery_id"]
    assert r1["pairing_code"] == r2["pairing_code"]


@pytest.mark.asyncio
async def test_admin_approve_creates_scanner(setup_db, admin_user):
    await _enable_discovery(setup_db)
    kp = generate_keypair()
    async with _unauth_client(setup_db) as ac:
        discover = (await ac.post(
            "/api/scanners/discover",
            json={"public_key_pem": kp.public_pem, "hostname": "h"},
        )).json()
    did = discover["discovery_id"]

    async with _admin_client(setup_db, admin_user) as ac:
        listed = (await ac.get("/api/scanner-discovery-requests")).json()
        assert any(r["id"] == did for r in listed)
        approve = await ac.post(
            f"/api/scanner-discovery-requests/{did}/approve",
            json={"name": "approved-scanner", "pool": "media"},
        )
    assert approve.status_code == 200, approve.text
    assert approve.json()["pool"] == "media"

    # Scanner side resolves with status=approved + scanner_id.
    async with _unauth_client(setup_db) as ac:
        status = (await ac.get(f"/api/scanners/discover/{did}")).json()
    assert status["status"] == "approved"
    assert status["name"] == "approved-scanner"
    assert status["pool"] == "media"


@pytest.mark.asyncio
async def test_admin_deny_resolves_to_denied(setup_db, admin_user):
    await _enable_discovery(setup_db)
    kp = generate_keypair()
    async with _unauth_client(setup_db) as ac:
        discover = (await ac.post(
            "/api/scanners/discover",
            json={"public_key_pem": kp.public_pem},
        )).json()
    did = discover["discovery_id"]

    async with _admin_client(setup_db, admin_user) as ac:
        deny = await ac.post(
            f"/api/scanner-discovery-requests/{did}/deny",
            json={"reason": "wrong host"},
        )
    assert deny.status_code == 204

    async with _unauth_client(setup_db) as ac:
        status = (await ac.get(f"/api/scanners/discover/{did}")).json()
    assert status["status"] == "denied"
    assert status["deny_reason"] == "wrong host"


@pytest.mark.asyncio
async def test_approve_already_decided_returns_410(setup_db, admin_user):
    await _enable_discovery(setup_db)
    kp = generate_keypair()
    async with _unauth_client(setup_db) as ac:
        discover = (await ac.post(
            "/api/scanners/discover",
            json={"public_key_pem": kp.public_pem},
        )).json()
    did = discover["discovery_id"]

    async with _admin_client(setup_db, admin_user) as ac:
        await ac.post(
            f"/api/scanner-discovery-requests/{did}/deny",
            json={"reason": "no"},
        )
        r2 = await ac.post(
            f"/api/scanner-discovery-requests/{did}/approve",
            json={"name": "x"},
        )
    assert r2.status_code == 410


@pytest.mark.asyncio
async def test_long_poll_short_circuits_on_decision(setup_db, admin_user):
    """When an admin approves while the scanner is mid-poll, the GET
    must return within ~100ms instead of waiting the full 25s."""
    await _enable_discovery(setup_db)
    kp = generate_keypair()
    async with _unauth_client(setup_db) as ac:
        discover = (await ac.post(
            "/api/scanners/discover",
            json={"public_key_pem": kp.public_pem},
        )).json()
    did = discover["discovery_id"]

    async def _approve_after_delay():
        await asyncio.sleep(0.2)
        async with _admin_client(setup_db, admin_user) as ac:
            await ac.post(
                f"/api/scanner-discovery-requests/{did}/approve",
                json={"name": "fast"},
            )

    async def _poll() -> dict:
        async with _unauth_client(setup_db) as ac:
            r = await ac.get(
                f"/api/scanners/discover/{did}",
                timeout=10.0,
            )
        return r.json()

    poll_task = asyncio.create_task(_poll())
    approve_task = asyncio.create_task(_approve_after_delay())
    result = await asyncio.wait_for(poll_task, timeout=10)
    await approve_task
    assert result["status"] == "approved"


@pytest.mark.asyncio
async def test_rate_limit_kicks_in(setup_db):
    await _enable_discovery(setup_db)
    async with _unauth_client(setup_db) as ac:
        # Six attempts with distinct keys to avoid the upsert path —
        # we want to exercise the rate-limit, not the idempotent reuse.
        responses = []
        for _ in range(6):
            kp = generate_keypair()
            r = await ac.post(
                "/api/scanners/discover",
                json={"public_key_pem": kp.public_pem},
            )
            responses.append(r.status_code)
    assert responses[-1] == 429
    assert responses[:5].count(201) == 5
