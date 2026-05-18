"""Watchdog: stale-scan kill-cutoff (`_check_stale_scans`).

The v0.4.9 regression coverage: previously path 2 of the watchdog
checked `source.last_scan_at < cutoff` to decide whether a source
stuck in `scanning` was actually stuck. `last_scan_at` is the
PREVIOUS completed scan's timestamp — never updated by an in-flight
scan — so any source whose last successful scan was >60 min ago had
its NEW in-flight scans killed by the watchdog within ~60 s of the
lease, regardless of whether the scanner was actively heartbeating.
The user-visible symptom: every newly-triggered scan failed shortly
after starting with `error_message="Watchdog: exceeded 60 min"` and
the agent reported `scan cancelled by api`.

The fix: path 2 now triggers only on `source.status='scanning'` with
NO open scan row at all — i.e., a state-drift recovery path, not a
duplicate of path 1.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.scan import Scan
from akashic.models.scan_work_unit import ScanWorkUnit
from akashic.models.source import Source
from akashic.scheduler import (
    _check_stale_scans,
    _requeue_orphan_leases,
    _requeue_orphan_unit_leases,
)


@pytest.fixture(autouse=True)
def _patch_async_session(monkeypatch, setup_db):
    import akashic.scheduler as scheduler
    monkeypatch.setattr(scheduler, "async_session", setup_db)


async def _make_source(
    db: AsyncSession, *, status: str = "online",
    last_scan_at: datetime | None = None,
) -> Source:
    src = Source(
        id=uuid.uuid4(),
        name=f"src-{uuid.uuid4().hex[:6]}",
        type="local",
        connection_config={"path": "/tmp"},
        status=status,
        last_scan_at=last_scan_at,
    )
    db.add(src)
    await db.commit()
    await db.refresh(src)
    return src


async def _make_scan(
    db: AsyncSession, *, source_id: uuid.UUID,
    status: str = "running",
    started_at: datetime | None = None,
    last_heartbeat_at: datetime | None = None,
    lease_expires_at: datetime | None = None,
    assigned_scanner_id: uuid.UUID | None = None,
    pool: str | None = None,
) -> Scan:
    scan = Scan(
        id=uuid.uuid4(),
        source_id=source_id,
        scan_type="incremental",
        status=status,
        started_at=started_at,
        last_heartbeat_at=last_heartbeat_at,
        lease_expires_at=lease_expires_at,
        assigned_scanner_id=assigned_scanner_id,
        pool=pool,
    )
    db.add(scan)
    await db.commit()
    await db.refresh(scan)
    return scan


async def _make_unit(
    db: AsyncSession, *, scan_id: uuid.UUID, path: str = "",
    status: str = "running",
    lease_expires_at: datetime | None = None,
    assigned_scanner_id: uuid.UUID | None = None,
) -> ScanWorkUnit:
    unit = ScanWorkUnit(
        id=uuid.uuid4(),
        scan_id=scan_id,
        path=path,
        status=status,
        lease_expires_at=lease_expires_at,
        assigned_scanner_id=assigned_scanner_id,
    )
    db.add(unit)
    await db.commit()
    await db.refresh(unit)
    return unit


async def _make_online_scanner(db: AsyncSession, *, pool: str = "default"):
    from akashic.models.scanner import Scanner
    from akashic.services.scanner_keys import generate_keypair

    kp = generate_keypair()
    scanner = Scanner(
        id=uuid.uuid4(),
        name=f"sc-{uuid.uuid4().hex[:6]}",
        pool=pool,
        public_key_pem=kp.public_pem,
        key_fingerprint=kp.fingerprint,
        enabled=True,
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(scanner)
    await db.commit()
    await db.refresh(scanner)
    return scanner


@pytest.mark.asyncio
async def test_active_scan_survives_watchdog_when_source_last_scan_is_old(
    setup_db, db_session: AsyncSession,
):
    """v0.4.9 regression: a freshly-leased scan that's actively
    heartbeating must NOT be killed just because the source's
    PREVIOUS completion was >60 min ago. Pre-fix this killed every
    Anime/Movies/TV scan because their last successful scan was
    earlier the same day."""
    long_ago = datetime.now(timezone.utc) - timedelta(hours=15)
    src = await _make_source(
        db_session, status="scanning", last_scan_at=long_ago,
    )
    just_now = datetime.now(timezone.utc) - timedelta(seconds=5)
    scan = await _make_scan(
        db_session, source_id=src.id, status="running",
        started_at=just_now, last_heartbeat_at=just_now,
    )

    await _check_stale_scans()

    await db_session.refresh(scan)
    await db_session.refresh(src)
    assert scan.status == "running", (
        "watchdog killed an actively-heartbeating scan because "
        "source.last_scan_at was old — the v0.4.9 bug"
    )
    assert src.status == "scanning"


@pytest.mark.asyncio
async def test_path1_still_kills_scan_with_stale_heartbeat(
    setup_db, db_session: AsyncSession,
):
    """Path 1 should still fire on scans whose heartbeat is older
    than the threshold — that's the legitimate "lost worker" case."""
    long_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    src = await _make_source(db_session, status="scanning")
    scan = await _make_scan(
        db_session, source_id=src.id, status="running",
        started_at=long_ago, last_heartbeat_at=long_ago,
    )

    await _check_stale_scans()

    await db_session.refresh(scan)
    await db_session.refresh(src)
    assert scan.status == "failed"
    assert "60 min" in (scan.error_message or "")
    assert src.status == "failed"


@pytest.mark.asyncio
async def test_path2_resets_source_when_no_open_scan(
    setup_db, db_session: AsyncSession,
):
    """Path 2's legitimate purpose: source.status='scanning' but no
    pending/running scan row exists (state drift). Reset the source
    so the user can re-trigger."""
    src = await _make_source(db_session, status="scanning")
    # No scan row at all.

    await _check_stale_scans()

    await db_session.refresh(src)
    assert src.status == "failed"


@pytest.mark.asyncio
async def test_path2_doesnt_touch_source_with_recent_scan(
    setup_db, db_session: AsyncSession,
):
    """Belt-and-braces: even if the source's last_scan_at is brand
    new, path 2 still skips it because there's an open scan."""
    just_now = datetime.now(timezone.utc) - timedelta(seconds=5)
    src = await _make_source(
        db_session, status="scanning", last_scan_at=just_now,
    )
    scan = await _make_scan(
        db_session, source_id=src.id, status="running",
        started_at=just_now, last_heartbeat_at=just_now,
    )

    await _check_stale_scans()

    await db_session.refresh(src)
    await db_session.refresh(scan)
    assert src.status == "scanning"
    assert scan.status == "running"


@pytest.mark.asyncio
async def test_requeue_skips_scan_with_a_fresh_lease(
    setup_db, db_session: AsyncSession,
):
    """v0.29.10 regression guard: a scan whose lease is still in the
    future (kept fresh by heartbeats, post-fix) must NOT be re-queued,
    even with an online scanner available to steal it. Pre-fix the
    lease expired 60 s after claim regardless of heartbeats, so
    `_requeue_orphan_leases` reset the healthy scan to `pending` and a
    second scanner double-leased it."""
    src = await _make_source(db_session, status="scanning")
    scanner = await _make_online_scanner(db_session, pool="default")
    just_now = datetime.now(timezone.utc) - timedelta(seconds=5)
    holder = scanner.id
    scan = await _make_scan(
        db_session, source_id=src.id, status="running",
        started_at=just_now, last_heartbeat_at=just_now,
        # Lease renewed by a recent heartbeat — still 55 s in the future.
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=55),
        assigned_scanner_id=holder, pool=None,
    )

    await _requeue_orphan_leases()

    await db_session.refresh(scan)
    assert scan.status == "running", (
        "_requeue_orphan_leases stole a scan with a fresh (renewed) lease"
    )
    assert scan.assigned_scanner_id == holder
    assert scan.lease_expires_at is not None


@pytest.mark.asyncio
async def test_requeue_reclaims_scan_with_an_expired_lease(
    setup_db, db_session: AsyncSession,
):
    """The genuine-failure path stays intact: a scan whose lease has
    actually expired (scanner died — no heartbeats renewing it) IS
    re-queued to `pending` so another scanner can pick it up, as long
    as an online scanner exists."""
    src = await _make_source(db_session, status="scanning")
    scanner = await _make_online_scanner(db_session, pool="default")
    long_ago = datetime.now(timezone.utc) - timedelta(minutes=5)
    scan = await _make_scan(
        db_session, source_id=src.id, status="running",
        started_at=long_ago, last_heartbeat_at=long_ago,
        lease_expires_at=long_ago,  # expired — no renewing heartbeats
        assigned_scanner_id=scanner.id, pool=None,
    )

    await _requeue_orphan_leases()

    await db_session.refresh(scan)
    assert scan.status == "pending"
    assert scan.assigned_scanner_id is None
    assert scan.lease_expires_at is None


# ── Work-unit-level orphan-lease reaper (v0.32.1) ────────────────────


@pytest.mark.asyncio
async def test_orphan_unit_lease_requeued_when_online_scanner(
    setup_db, db_session: AsyncSession,
):
    """A work unit stuck `running` with an expired lease — its scanner
    crashed or exited mid-unit — is reset to `pending` so a scanner can
    re-lease it, as long as an online scanner is available. Without this
    the orphan stalls the whole scan: `_maybe_finalize_scan` counts it
    as pending and `_requeue_orphan_leases` is blind to it."""
    src = await _make_source(db_session, status="scanning")
    scanner = await _make_online_scanner(db_session, pool="default")
    just_now = datetime.now(timezone.utc) - timedelta(seconds=5)
    scan = await _make_scan(
        db_session, source_id=src.id, status="running",
        started_at=just_now, pool=None,
    )
    long_ago = datetime.now(timezone.utc) - timedelta(minutes=5)
    unit = await _make_unit(
        db_session, scan_id=scan.id, status="running",
        lease_expires_at=long_ago, assigned_scanner_id=scanner.id,
    )

    await _requeue_orphan_unit_leases()

    await db_session.refresh(unit)
    assert unit.status == "pending"
    assert unit.assigned_scanner_id is None
    assert unit.lease_expires_at is None


@pytest.mark.asyncio
async def test_orphan_unit_lease_left_when_no_online_scanner(
    setup_db, db_session: AsyncSession,
):
    """No online scanner could pick the unit up → leave it untouched;
    the scan-level kill path fails the scan after the threshold."""
    src = await _make_source(db_session, status="scanning")
    # No scanner created → none online.
    just_now = datetime.now(timezone.utc) - timedelta(seconds=5)
    scan = await _make_scan(
        db_session, source_id=src.id, status="running",
        started_at=just_now, pool=None,
    )
    long_ago = datetime.now(timezone.utc) - timedelta(minutes=5)
    unit = await _make_unit(
        db_session, scan_id=scan.id, status="running",
        lease_expires_at=long_ago,
    )

    await _requeue_orphan_unit_leases()

    await db_session.refresh(unit)
    assert unit.status == "running"


@pytest.mark.asyncio
async def test_orphan_unit_reaper_ignores_live_lease(
    setup_db, db_session: AsyncSession,
):
    """A unit whose lease is still in the future is being actively
    heartbeated by its scanner — it must not be reaped."""
    src = await _make_source(db_session, status="scanning")
    await _make_online_scanner(db_session, pool="default")
    just_now = datetime.now(timezone.utc) - timedelta(seconds=5)
    scan = await _make_scan(
        db_session, source_id=src.id, status="running",
        started_at=just_now, pool=None,
    )
    fresh = datetime.now(timezone.utc) + timedelta(seconds=45)
    unit = await _make_unit(
        db_session, scan_id=scan.id, status="running",
        lease_expires_at=fresh,
    )

    await _requeue_orphan_unit_leases()

    await db_session.refresh(unit)
    assert unit.status == "running"
    assert unit.lease_expires_at is not None


@pytest.mark.asyncio
async def test_orphan_unit_reaper_ignores_terminal_scan(
    setup_db, db_session: AsyncSession,
):
    """A `running` unit with an expired lease whose parent scan is
    already terminal must not be resurrected."""
    src = await _make_source(db_session, status="online")
    await _make_online_scanner(db_session, pool="default")
    scan = await _make_scan(
        db_session, source_id=src.id, status="completed",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        pool=None,
    )
    long_ago = datetime.now(timezone.utc) - timedelta(minutes=5)
    unit = await _make_unit(
        db_session, scan_id=scan.id, status="running",
        lease_expires_at=long_ago,
    )

    await _requeue_orphan_unit_leases()

    await db_session.refresh(unit)
    assert unit.status == "running"  # untouched — scan is terminal


@pytest.mark.asyncio
async def test_check_stale_scans_spares_scan_with_live_unit_lease(
    setup_db, db_session: AsyncSession,
):
    """A unit-coordinated scan never sets Scan.last_heartbeat_at. A
    `running` work unit with an unexpired lease means a scanner is alive
    and heartbeating it, so the scan must NOT be killed even though its
    started_at is older than the threshold."""
    long_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    src = await _make_source(db_session, status="scanning")
    await _make_online_scanner(db_session, pool="default")
    scan = await _make_scan(
        db_session, source_id=src.id, status="running",
        started_at=long_ago, last_heartbeat_at=None, pool=None,
    )
    fresh = datetime.now(timezone.utc) + timedelta(seconds=45)
    await _make_unit(
        db_session, scan_id=scan.id, status="running",
        lease_expires_at=fresh,
    )

    await _check_stale_scans()

    await db_session.refresh(scan)
    assert scan.status == "running", (
        "watchdog killed a healthy multi-scanner scan that has a live "
        "work-unit lease"
    )


@pytest.mark.asyncio
async def test_check_stale_scans_kills_unit_scan_with_no_live_lease(
    setup_db, db_session: AsyncSession,
):
    """The kill path still fires for a genuinely-stuck unit-coordinated
    scan: old started_at, no heartbeat, its only work unit's lease
    expired, and no online scanner for the reaper to recover it."""
    long_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    src = await _make_source(db_session, status="scanning")
    scan = await _make_scan(
        db_session, source_id=src.id, status="running",
        started_at=long_ago, last_heartbeat_at=None, pool=None,
    )
    await _make_unit(
        db_session, scan_id=scan.id, status="running",
        lease_expires_at=long_ago,  # expired — no renewing heartbeats
    )

    await _check_stale_scans()

    await db_session.refresh(scan)
    assert scan.status == "failed"
    assert "60 min" in (scan.error_message or "")
