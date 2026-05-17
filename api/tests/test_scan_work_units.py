"""POST /api/scans/{id}/work/* — claim, heartbeat, complete, fail,
split semantics for parallel scanning.

These exercise the API contract only. Scanner-side use of the
endpoints (split heuristic in the walker, multi-claimer agent loop)
ships in a follow-up release; the data model + lease primitives are
in place so an updated agent can opt in without further migration.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.scan import Scan
from akashic.models.scan_work_unit import ScanWorkUnit
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services.scanner_keys import sign_jwt


# ── Fixtures ──────────────────────────────────────────────────────────────


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


async def _mint_scanner(setup_db, admin_user, name: str | None = None) -> dict:
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.post("/api/scanners", json={
            "name": name or f"s-{uuid.uuid4().hex[:6]}",
            "pool": "default",
        })
    assert r.status_code == 201, r.text
    return r.json()


async def _seed_scan(setup_db, max_parallel: int = 1) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a Source + a pending Scan; return (scan_id, source_id)."""
    async with setup_db() as db:
        src = Source(
            id=uuid.uuid4(),
            name=f"src-{uuid.uuid4().hex[:6]}",
            type="local",
            connection_config={"path": "/tmp"},
            max_parallel_scanners=max_parallel,
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
        return scan.id, src.id


def _auth(scn: dict) -> dict:
    return {
        "Authorization": f"Bearer {_scanner_token(scn['id'], scn['private_key_pem'])}",
    }


# ── Tests ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lease_with_no_units_returns_204(setup_db, admin_user):
    scan_id, _ = await _seed_scan(setup_db)
    scn = await _mint_scanner(setup_db, admin_user)

    async with _bearer_client(setup_db) as ac:
        r = await ac.post(f"/api/scans/{scan_id}/work/lease", headers=_auth(scn))
    assert r.status_code == 204, r.text


@pytest.mark.asyncio
async def test_split_then_lease_returns_unit(setup_db, admin_user):
    scan_id, _ = await _seed_scan(setup_db)
    scn = await _mint_scanner(setup_db, admin_user)

    async with _bearer_client(setup_db) as ac:
        s = await ac.post(
            f"/api/scans/{scan_id}/work/split",
            json={"child_paths": ["", "a", "b"]},
            headers=_auth(scn),
        )
        assert s.status_code == 200, s.text
        assert s.json()["created"] == 3
        assert s.json()["skipped"] == 0

        r = await ac.post(f"/api/scans/{scan_id}/work/lease", headers=_auth(scn))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "running"
    assert body["lease_expires_at"] is not None


@pytest.mark.asyncio
async def test_split_is_idempotent(setup_db, admin_user):
    scan_id, _ = await _seed_scan(setup_db)
    scn = await _mint_scanner(setup_db, admin_user)

    async with _bearer_client(setup_db) as ac:
        first = await ac.post(
            f"/api/scans/{scan_id}/work/split",
            json={"child_paths": ["", "x"]},
            headers=_auth(scn),
        )
        second = await ac.post(
            f"/api/scans/{scan_id}/work/split",
            json={"child_paths": ["", "x", "y"]},  # "y" is new, "" + "x" repeat
            headers=_auth(scn),
        )
    assert first.json() == {"created": 2, "skipped": 0}
    assert second.json() == {"created": 1, "skipped": 2}


@pytest.mark.asyncio
async def test_lease_serialises_via_skip_locked(setup_db, admin_user):
    scan_id, _ = await _seed_scan(setup_db)
    scn1 = await _mint_scanner(setup_db, admin_user, name="a")
    scn2 = await _mint_scanner(setup_db, admin_user, name="b")

    async with _bearer_client(setup_db) as ac:
        await ac.post(
            f"/api/scans/{scan_id}/work/split",
            json={"child_paths": ["only"]},
            headers=_auth(scn1),
        )
        # Two scanners race for the single available unit.
        results = await asyncio.gather(
            ac.post(f"/api/scans/{scan_id}/work/lease", headers=_auth(scn1)),
            ac.post(f"/api/scans/{scan_id}/work/lease", headers=_auth(scn2)),
        )
    statuses = sorted(r.status_code for r in results)
    # Exactly one wins (200), the other gets 204 (no work) — never both 200.
    assert statuses == [200, 204], [r.status_code for r in results]


@pytest.mark.asyncio
async def test_lease_returns_204_not_409_when_capped_but_no_units_left(
    setup_db, admin_user,
):
    """A scanner that finds no claimable unit gets 204 — even when the
    parallel-scanner cap is already full. 409 is reserved for the case
    where a unit IS free but the cap blocks this scanner. This pins the
    ordering that made test_lease_serialises_via_skip_locked flake
    between [200, 204] and [200, 409]."""
    scan_id, _ = await _seed_scan(setup_db, max_parallel=1)
    scn1 = await _mint_scanner(setup_db, admin_user, name="a")
    scn2 = await _mint_scanner(setup_db, admin_user, name="b")

    async with _bearer_client(setup_db) as ac:
        await ac.post(
            f"/api/scans/{scan_id}/work/split",
            json={"child_paths": ["only"]},
            headers=_auth(scn1),
        )
        # scn1 takes the one unit — the scan is now at its cap of 1.
        r1 = await ac.post(f"/api/scans/{scan_id}/work/lease", headers=_auth(scn1))
        assert r1.status_code == 200, r1.text
        # scn2 polls with no unit free. Must be 204 ("nothing for me"),
        # never 409 ("cap reached") — there is simply no work.
        r2 = await ac.post(f"/api/scans/{scan_id}/work/lease", headers=_auth(scn2))
    assert r2.status_code == 204, r2.text


@pytest.mark.asyncio
async def test_max_parallel_scanners_cap(setup_db, admin_user):
    scan_id, _ = await _seed_scan(setup_db, max_parallel=2)
    scn1 = await _mint_scanner(setup_db, admin_user, name="a")
    scn2 = await _mint_scanner(setup_db, admin_user, name="b")
    scn3 = await _mint_scanner(setup_db, admin_user, name="c")

    async with _bearer_client(setup_db) as ac:
        await ac.post(
            f"/api/scans/{scan_id}/work/split",
            json={"child_paths": ["a", "b", "c"]},
            headers=_auth(scn1),
        )
        # First two scanners each lease one unit — now at the cap.
        r1 = await ac.post(f"/api/scans/{scan_id}/work/lease", headers=_auth(scn1))
        r2 = await ac.post(f"/api/scans/{scan_id}/work/lease", headers=_auth(scn2))
        assert r1.status_code == 200
        assert r2.status_code == 200
        # Third scanner refused: cap reached.
        r3 = await ac.post(f"/api/scans/{scan_id}/work/lease", headers=_auth(scn3))
    assert r3.status_code == 409
    assert "cap" in r3.json()["detail"].lower()


@pytest.mark.asyncio
async def test_complete_unit_finalizes_scan_when_last(
    setup_db, admin_user
):
    scan_id, source_id = await _seed_scan(setup_db)
    scn = await _mint_scanner(setup_db, admin_user)

    async with _bearer_client(setup_db) as ac:
        await ac.post(
            f"/api/scans/{scan_id}/work/split",
            json={"child_paths": ["only"]},
            headers=_auth(scn),
        )
        leased = (await ac.post(
            f"/api/scans/{scan_id}/work/lease", headers=_auth(scn)
        )).json()
        c = await ac.post(
            f"/api/scans/{scan_id}/work/{leased['id']}/complete",
            headers=_auth(scn),
        )
    assert c.status_code == 204

    async with setup_db() as db:
        scan = (await db.execute(
            select(Scan).where(Scan.id == scan_id)
        )).scalar_one()
        src = (await db.execute(
            select(Source).where(Source.id == source_id)
        )).scalar_one()
    assert scan.status == "completed"
    assert scan.completed_at is not None
    # v0.31.4 — the finalize path must record cancellation_reason to
    # match the terminal status. Left NULL, a scanner still heartbeating
    # after finalize gets a 409 with reason=null, which the Go decoder
    # mislabels "scan cancelled by user" in the Live Log.
    assert scan.cancellation_reason == "completed"
    assert src.status == "online"
    assert src.last_scan_at is not None
    # v0.28.0: cached source.is_reachable + last_reachable_at columns
    # are gone. The implicit reachability proof now lives in
    # reachability_results — assert one row landed for the assigned
    # scanner with ok=true.
    from akashic.models.reachability_result import ReachabilityResult
    async with setup_db() as db:
        rr = (await db.execute(
            select(ReachabilityResult).where(
                ReachabilityResult.source_id == source_id,
            )
        )).scalars().all()
    assert any(r.ok and r.scanner_id is not None for r in rr), (
        "expected an implicit reachability_results row from scan completion"
    )


@pytest.mark.asyncio
async def test_complete_unit_with_siblings_pending_keeps_scan_running(
    setup_db, admin_user
):
    scan_id, _ = await _seed_scan(setup_db)
    scn = await _mint_scanner(setup_db, admin_user)

    async with _bearer_client(setup_db) as ac:
        await ac.post(
            f"/api/scans/{scan_id}/work/split",
            json={"child_paths": ["a", "b"]},
            headers=_auth(scn),
        )
        leased = (await ac.post(
            f"/api/scans/{scan_id}/work/lease", headers=_auth(scn)
        )).json()
        await ac.post(
            f"/api/scans/{scan_id}/work/{leased['id']}/complete",
            headers=_auth(scn),
        )

    async with setup_db() as db:
        scan = (await db.execute(
            select(Scan).where(Scan.id == scan_id)
        )).scalar_one()
    # One unit completed but the sibling unit is still pending.
    assert scan.status == "running"
    assert scan.completed_at is None


@pytest.mark.asyncio
async def test_fail_unit_with_no_others_marks_scan_failed(setup_db, admin_user):
    scan_id, source_id = await _seed_scan(setup_db)
    scn = await _mint_scanner(setup_db, admin_user)

    async with _bearer_client(setup_db) as ac:
        await ac.post(
            f"/api/scans/{scan_id}/work/split",
            json={"child_paths": ["only"]},
            headers=_auth(scn),
        )
        leased = (await ac.post(
            f"/api/scans/{scan_id}/work/lease", headers=_auth(scn)
        )).json()
        f = await ac.post(
            f"/api/scans/{scan_id}/work/{leased['id']}/fail",
            json={"error_message": "synthetic"},
            headers=_auth(scn),
        )
    assert f.status_code == 204

    async with setup_db() as db:
        scan = (await db.execute(
            select(Scan).where(Scan.id == scan_id)
        )).scalar_one()
        src = (await db.execute(
            select(Source).where(Source.id == source_id)
        )).scalar_one()
    assert scan.status == "failed"
    # v0.31.4 — finalize records the reason for the failed path too.
    assert scan.cancellation_reason == "failed"
    assert src.status == "failed"


@pytest.mark.asyncio
async def test_heartbeat_extends_lease(setup_db, admin_user):
    scan_id, _ = await _seed_scan(setup_db)
    scn = await _mint_scanner(setup_db, admin_user)

    async with _bearer_client(setup_db) as ac:
        await ac.post(
            f"/api/scans/{scan_id}/work/split",
            json={"child_paths": ["x"]},
            headers=_auth(scn),
        )
        leased = (await ac.post(
            f"/api/scans/{scan_id}/work/lease", headers=_auth(scn)
        )).json()
        first_expiry = leased["lease_expires_at"]
        # Sleep just a bit so the second timestamp is strictly later.
        await asyncio.sleep(0.05)
        h = await ac.post(
            f"/api/scans/{scan_id}/work/{leased['id']}/heartbeat",
            headers=_auth(scn),
        )
    assert h.status_code == 200
    assert h.json()["lease_expires_at"] > first_expiry


@pytest.mark.asyncio
async def test_complete_by_non_holder_returns_403(setup_db, admin_user):
    scan_id, _ = await _seed_scan(setup_db)
    scn1 = await _mint_scanner(setup_db, admin_user, name="a")
    scn2 = await _mint_scanner(setup_db, admin_user, name="b")

    async with _bearer_client(setup_db) as ac:
        await ac.post(
            f"/api/scans/{scan_id}/work/split",
            json={"child_paths": ["x"]},
            headers=_auth(scn1),
        )
        leased = (await ac.post(
            f"/api/scans/{scan_id}/work/lease", headers=_auth(scn1)
        )).json()
        # scn2 tries to complete scn1's unit.
        c = await ac.post(
            f"/api/scans/{scan_id}/work/{leased['id']}/complete",
            headers=_auth(scn2),
        )
    assert c.status_code == 403


@pytest.mark.asyncio
async def test_lease_after_expiry_reclaimable(setup_db, admin_user):
    scan_id, _ = await _seed_scan(setup_db)
    scn1 = await _mint_scanner(setup_db, admin_user, name="a")
    scn2 = await _mint_scanner(setup_db, admin_user, name="b")

    async with _bearer_client(setup_db) as ac:
        await ac.post(
            f"/api/scans/{scan_id}/work/split",
            json={"child_paths": ["x"]},
            headers=_auth(scn1),
        )
        leased = (await ac.post(
            f"/api/scans/{scan_id}/work/lease", headers=_auth(scn1)
        )).json()

    # Force the lease into the past so the next lease attempt sees it
    # as expired and reclaims.
    async with setup_db() as db:
        unit = (await db.execute(
            select(ScanWorkUnit).where(ScanWorkUnit.id == uuid.UUID(leased["id"]))
        )).scalar_one()
        unit.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()

    async with _bearer_client(setup_db) as ac:
        r = await ac.post(f"/api/scans/{scan_id}/work/lease", headers=_auth(scn2))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == leased["id"]  # same unit, new holder
