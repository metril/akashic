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
from akashic.models.source import Source
from akashic.scheduler import _check_stale_scans


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
) -> Scan:
    scan = Scan(
        id=uuid.uuid4(),
        source_id=source_id,
        scan_type="incremental",
        status=status,
        started_at=started_at,
        last_heartbeat_at=last_heartbeat_at,
    )
    db.add(scan)
    await db.commit()
    await db.refresh(scan)
    return scan


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
