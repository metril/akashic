"""Watchdog: orphan-lease re-queue logic.

Drives `_requeue_orphan_leases` directly (it's a small async function
that opens its own session — no api fixtures needed beyond the test
DB)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.scan import Scan
from akashic.models.scanner import Scanner
from akashic.models.source import Source
from akashic.scheduler import _requeue_orphan_leases


@pytest.fixture(autouse=True)
def _patch_async_session(monkeypatch, setup_db):
    """The watchdog opens its own `async_session()` — point it at the
    test DB's session-maker via `setup_db`."""
    import akashic.scheduler as scheduler
    monkeypatch.setattr(scheduler, "async_session", setup_db)


async def _make_source(db: AsyncSession, **kw) -> Source:
    src = Source(
        id=uuid.uuid4(),
        name=f"src-{uuid.uuid4().hex[:6]}",
        type="local",
        connection_config={"path": "/tmp"},
        **kw,
    )
    db.add(src)
    await db.commit()
    await db.refresh(src)
    return src


async def _make_scanner(db: AsyncSession, *, pool: str, online: bool) -> Scanner:
    last_seen = (
        datetime.now(timezone.utc) - timedelta(seconds=5)
        if online
        else datetime.now(timezone.utc) - timedelta(hours=1)
    )
    s = Scanner(
        id=uuid.uuid4(),
        name=f"scn-{uuid.uuid4().hex[:6]}",
        pool=pool,
        public_key_pem="-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----\n",
        key_fingerprint=uuid.uuid4().hex,
        last_seen_at=last_seen,
        enabled=True,
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_scan(
    db: AsyncSession, *, source_id: uuid.UUID, pool: str | None,
    assigned_to: uuid.UUID | None, lease_expired: bool,
) -> Scan:
    scan = Scan(
        id=uuid.uuid4(),
        source_id=source_id,
        scan_type="incremental",
        status="running",
        pool=pool,
        assigned_scanner_id=assigned_to,
        lease_expires_at=(
            datetime.now(timezone.utc) - timedelta(minutes=2)
            if lease_expired
            else datetime.now(timezone.utc) + timedelta(minutes=10)
        ),
    )
    db.add(scan)
    await db.commit()
    await db.refresh(scan)
    return scan


@pytest.mark.asyncio
async def test_expired_lease_requeues_when_pool_has_online_scanner(
    setup_db, db_session: AsyncSession,
):
    src = await _make_source(db_session)
    crashed = await _make_scanner(db_session, pool="default", online=False)
    _healthy = await _make_scanner(db_session, pool="default", online=True)
    scan = await _make_scan(
        db_session, source_id=src.id, pool="default",
        assigned_to=crashed.id, lease_expired=True,
    )

    await _requeue_orphan_leases()
    # The watchdog ran in a separate session; refresh forces a fresh
    # SELECT instead of returning the identity-map's cached row.
    await db_session.refresh(scan)
    refreshed = scan
    assert refreshed.status == "pending"
    assert refreshed.assigned_scanner_id is None
    assert refreshed.lease_expires_at is None


@pytest.mark.asyncio
async def test_expired_lease_left_alone_when_no_online_scanner(
    setup_db, db_session: AsyncSession,
):
    """The kill-cutoff path will fail it later. Don't requeue without
    a candidate, otherwise it'd just churn between pending and
    running every watchdog tick."""
    src = await _make_source(db_session)
    crashed = await _make_scanner(db_session, pool="default", online=False)
    scan = await _make_scan(
        db_session, source_id=src.id, pool="default",
        assigned_to=crashed.id, lease_expired=True,
    )

    await _requeue_orphan_leases()
    # The watchdog ran in a separate session; refresh forces a fresh
    # SELECT instead of returning the identity-map's cached row.
    await db_session.refresh(scan)
    refreshed = scan
    assert refreshed.status == "running"  # untouched
    assert refreshed.assigned_scanner_id == crashed.id


@pytest.mark.asyncio
async def test_unexpired_lease_is_not_requeued(
    setup_db, db_session: AsyncSession,
):
    """Lease still in date — even if scanner went offline, we wait for
    the lease to expire before stealing the work."""
    src = await _make_source(db_session)
    crashed = await _make_scanner(db_session, pool="default", online=False)
    _healthy = await _make_scanner(db_session, pool="default", online=True)
    scan = await _make_scan(
        db_session, source_id=src.id, pool="default",
        assigned_to=crashed.id, lease_expired=False,
    )

    await _requeue_orphan_leases()
    # The watchdog ran in a separate session; refresh forces a fresh
    # SELECT instead of returning the identity-map's cached row.
    await db_session.refresh(scan)
    refreshed = scan
    assert refreshed.status == "running"
    assert refreshed.assigned_scanner_id == crashed.id


@pytest.mark.asyncio
async def test_null_pool_requeues_when_any_pool_has_online_scanner(
    setup_db, db_session: AsyncSession,
):
    """Permissive null-pool scan can be picked up by any online
    scanner regardless of pool — so re-queue as soon as ANY scanner
    is healthy."""
    src = await _make_source(db_session)
    crashed = await _make_scanner(db_session, pool="hq", online=False)
    _healthy = await _make_scanner(db_session, pool="dr-site", online=True)
    scan = await _make_scan(
        db_session, source_id=src.id, pool=None,
        assigned_to=crashed.id, lease_expired=True,
    )

    await _requeue_orphan_leases()
    # The watchdog ran in a separate session; refresh forces a fresh
    # SELECT instead of returning the identity-map's cached row.
    await db_session.refresh(scan)
    refreshed = scan
    assert refreshed.status == "pending"


@pytest.mark.asyncio
async def test_pool_specific_only_requeues_when_that_pool_has_online_scanner(
    setup_db, db_session: AsyncSession,
):
    """Pool=`hq` scan must be re-queued only if `hq` has an online
    scanner — having an online scanner in a *different* pool isn't
    enough."""
    src = await _make_source(db_session)
    crashed = await _make_scanner(db_session, pool="hq", online=False)
    _wrong_pool = await _make_scanner(db_session, pool="dr-site", online=True)
    scan = await _make_scan(
        db_session, source_id=src.id, pool="hq",
        assigned_to=crashed.id, lease_expired=True,
    )

    await _requeue_orphan_leases()
    # The watchdog ran in a separate session; refresh forces a fresh
    # SELECT instead of returning the identity-map's cached row.
    await db_session.refresh(scan)
    refreshed = scan
    assert refreshed.status == "running"  # not re-queued
