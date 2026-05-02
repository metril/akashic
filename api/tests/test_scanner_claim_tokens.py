"""POST /api/scanner-claim-tokens + POST /api/scanners/claim coverage.

The claim flow is the v0.3.0 self-registration path: an admin mints
a single-use token, the scanner host generates its own keypair and
posts the public key + token, and the api creates the Scanner row
without ever seeing the private key.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.scan import Scan
from akashic.models.scanner_claim_token import ScannerClaimToken
from akashic.models.source import Source
from akashic.models.user import User
from akashic.protocol import PROTOCOL_VERSION
from akashic.services.scanner_keys import generate_keypair, sign_jwt


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


def _scanner_token(sid: str, priv: str) -> str:
    now = int(time.time())
    return sign_jwt(
        priv,
        {"iss": "scanner", "sub": sid, "iat": now, "exp": now + 300},
        headers={"kid": sid},
    )


@pytest.mark.asyncio
async def test_admin_mint_token_returns_plaintext_once(setup_db, admin_user):
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.post(
            "/api/scanner-claim-tokens",
            json={"label": "homelab-nas", "ttl_minutes": 60},
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["token"].startswith("akcl_")
    assert body["label"] == "homelab-nas"
    assert body["pool"] == "default"
    assert "shell" in body["snippets"]
    assert "compose" in body["snippets"]
    assert body["snippets"]["shell"].startswith("akashic-scanner claim")

    # The list endpoint never re-exposes the plaintext.
    async with _admin_client(setup_db, admin_user) as ac:
        listed = (await ac.get("/api/scanner-claim-tokens")).json()
    assert any(t["id"] == body["id"] for t in listed)
    for t in listed:
        assert "token" not in t
        assert t["status"] == "active"


@pytest.mark.asyncio
async def test_non_admin_cannot_mint(setup_db):
    async with setup_db() as session:
        viewer = User(
            id=uuid.uuid4(), username="v", email="v@b.c",
            password_hash="x", role="viewer",
        )
        session.add(viewer)
        await session.commit()
    async with _admin_client(setup_db, viewer) as ac:
        r = await ac.post(
            "/api/scanner-claim-tokens", json={"label": "no"},
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_claim_creates_scanner_and_marks_token_used(setup_db, admin_user):
    async with _admin_client(setup_db, admin_user) as ac:
        token_resp = (await ac.post(
            "/api/scanner-claim-tokens",
            json={"label": "homelab-nas", "pool": "site-1"},
        )).json()
    plain = token_resp["token"]

    kp = generate_keypair()
    async with _unauth_client(setup_db) as ac:
        r = await ac.post(
            "/api/scanners/claim",
            json={
                "token": plain,
                "public_key_pem": kp.public_pem,
                "hostname": "nas-01",
                "agent_version": "0.3.0",
            },
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["pool"] == "site-1"
    assert body["server_protocol_version"] == PROTOCOL_VERSION

    # Second attempt with same token → 410 Gone.
    async with _unauth_client(setup_db) as ac:
        r2 = await ac.post(
            "/api/scanners/claim",
            json={"token": plain, "public_key_pem": generate_keypair().public_pem},
        )
    assert r2.status_code == 410

    # Token list now shows the row as 'used' with the scanner id.
    async with _admin_client(setup_db, admin_user) as ac:
        listed = (await ac.get("/api/scanner-claim-tokens")).json()
    [row] = [t for t in listed if t["id"] == token_resp["id"]]
    assert row["status"] == "used"
    assert row["used_by_scanner_id"] == body["scanner_id"]


@pytest.mark.asyncio
async def test_claim_with_garbage_token_returns_401(setup_db):
    async with _unauth_client(setup_db) as ac:
        r = await ac.post(
            "/api/scanners/claim",
            json={
                "token": "akcl_definitelyNotARealToken",
                "public_key_pem": generate_keypair().public_pem,
            },
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_claim_with_expired_token_returns_410(setup_db, admin_user):
    """Expire the row directly in the DB so we don't have to wait."""
    async with _admin_client(setup_db, admin_user) as ac:
        token_resp = (await ac.post(
            "/api/scanner-claim-tokens", json={"label": "soon-stale"},
        )).json()
    plain = token_resp["token"]
    async with setup_db() as session:
        row = (await session.execute(
            ScannerClaimToken.__table__.select().where(
                ScannerClaimToken.id == uuid.UUID(token_resp["id"]),
            )
        )).first()
        assert row is not None
        await session.execute(
            ScannerClaimToken.__table__.update()
            .where(ScannerClaimToken.id == uuid.UUID(token_resp["id"]))
            .values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        )
        await session.commit()

    async with _unauth_client(setup_db) as ac:
        r = await ac.post(
            "/api/scanners/claim",
            json={"token": plain, "public_key_pem": generate_keypair().public_pem},
        )
    assert r.status_code == 410


@pytest.mark.asyncio
async def test_revoke_unused_token_blocks_subsequent_claim(setup_db, admin_user):
    async with _admin_client(setup_db, admin_user) as ac:
        token_resp = (await ac.post(
            "/api/scanner-claim-tokens", json={"label": "to-revoke"},
        )).json()
        delete = await ac.delete(
            f"/api/scanner-claim-tokens/{token_resp['id']}",
        )
        assert delete.status_code == 204
    plain = token_resp["token"]

    async with _unauth_client(setup_db) as ac:
        r = await ac.post(
            "/api/scanners/claim",
            json={"token": plain, "public_key_pem": generate_keypair().public_pem},
        )
    assert r.status_code == 410


@pytest.mark.asyncio
async def test_revoke_used_token_returns_410_not_404(setup_db, admin_user):
    async with _admin_client(setup_db, admin_user) as ac:
        token_resp = (await ac.post(
            "/api/scanner-claim-tokens", json={"label": "use-then-revoke"},
        )).json()
    async with _unauth_client(setup_db) as ac:
        await ac.post(
            "/api/scanners/claim",
            json={
                "token": token_resp["token"],
                "public_key_pem": generate_keypair().public_pem,
            },
        )
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.delete(f"/api/scanner-claim-tokens/{token_resp['id']}")
    assert r.status_code == 410


@pytest.mark.asyncio
async def test_mint_rejects_unknown_scan_type(setup_db, admin_user):
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.post(
            "/api/scanner-claim-tokens",
            json={"label": "x", "allowed_scan_types": ["incremental", "deep"]},
        )
    assert r.status_code == 400
    assert "deep" in r.text


@pytest.mark.asyncio
async def test_mint_rejects_unknown_source_id(setup_db, admin_user):
    bogus = str(uuid.uuid4())
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.post(
            "/api/scanner-claim-tokens",
            json={"label": "x", "allowed_source_ids": [bogus]},
        )
    assert r.status_code == 400
    assert bogus in r.text


@pytest.mark.asyncio
async def test_scope_filter_blocks_disallowed_source(setup_db, admin_user):
    """Scanner scoped to source A cannot lease a pending scan for
    source B even though the pool matches."""
    # Two sources, one pending scan on the disallowed one.
    async with setup_db() as db:
        src_allowed = Source(
            id=uuid.uuid4(), name="a",
            type="local", connection_config={"path": "/a"},
        )
        src_blocked = Source(
            id=uuid.uuid4(), name="b",
            type="local", connection_config={"path": "/b"},
        )
        db.add_all([src_allowed, src_blocked])
        await db.flush()
        scan = Scan(
            id=uuid.uuid4(), source_id=src_blocked.id,
            scan_type="incremental", status="pending",
        )
        db.add(scan)
        await db.commit()
        allowed_id = src_allowed.id

    async with _admin_client(setup_db, admin_user) as ac:
        token_resp = (await ac.post(
            "/api/scanner-claim-tokens",
            json={
                "label": "scoped",
                "allowed_source_ids": [str(allowed_id)],
            },
        )).json()

    kp = generate_keypair()
    async with _unauth_client(setup_db) as ac:
        claim = (await ac.post(
            "/api/scanners/claim",
            json={"token": token_resp["token"], "public_key_pem": kp.public_pem},
        )).json()
    sid = claim["scanner_id"]

    jwt = _scanner_token(sid, kp.private_pem)
    async with _unauth_client(setup_db) as ac:
        r = await ac.post(
            "/api/scans/lease",
            headers={"Authorization": f"Bearer {jwt}"},
        )
    # Pending scan exists but is for src_blocked; scope filter blocks it.
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_scope_filter_blocks_disallowed_scan_type(setup_db, admin_user):
    async with setup_db() as db:
        src = Source(
            id=uuid.uuid4(), name="only-source",
            type="local", connection_config={"path": "/x"},
        )
        db.add(src)
        await db.flush()
        scan = Scan(
            id=uuid.uuid4(), source_id=src.id,
            scan_type="full", status="pending",
        )
        db.add(scan)
        await db.commit()

    async with _admin_client(setup_db, admin_user) as ac:
        token_resp = (await ac.post(
            "/api/scanner-claim-tokens",
            json={"label": "incr-only", "allowed_scan_types": ["incremental"]},
        )).json()

    kp = generate_keypair()
    async with _unauth_client(setup_db) as ac:
        claim = (await ac.post(
            "/api/scanners/claim",
            json={"token": token_resp["token"], "public_key_pem": kp.public_pem},
        )).json()
    sid = claim["scanner_id"]

    jwt = _scanner_token(sid, kp.private_pem)
    async with _unauth_client(setup_db) as ac:
        r = await ac.post(
            "/api/scans/lease",
            headers={"Authorization": f"Bearer {jwt}"},
        )
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_patch_scanner_can_widen_scope(setup_db, admin_user):
    """Admin tightens then widens a scope post-hoc; scope edits don't
    require key rotation."""
    async with _admin_client(setup_db, admin_user) as ac:
        token_resp = (await ac.post(
            "/api/scanner-claim-tokens", json={"label": "starts-loose"},
        )).json()
    kp = generate_keypair()
    async with _unauth_client(setup_db) as ac:
        claim = (await ac.post(
            "/api/scanners/claim",
            json={"token": token_resp["token"], "public_key_pem": kp.public_pem},
        )).json()
    sid = claim["scanner_id"]

    # Tighten to incremental only.
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.patch(
            f"/api/scanners/{sid}",
            json={"allowed_scan_types": ["incremental"]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["allowed_scan_types"] == ["incremental"]

        # Clear back to unrestricted.
        r2 = await ac.patch(
            f"/api/scanners/{sid}",
            json={"clear_allowed_scan_types": True},
        )
        assert r2.json()["allowed_scan_types"] is None
