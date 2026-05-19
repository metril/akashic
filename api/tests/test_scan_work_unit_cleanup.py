"""scan_work_units retention — purge_scan_work_units (v0.34.0).

Budget-bounded dynamic units mean a scan accumulates many work-unit
rows; the hourly cleanup drops them once their parent scan has been
terminal for longer than the retention window. A running scan's units,
and a recently-finished scan's units, are spared.
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
from akashic.scheduler import purge_scan_work_units


async def _scan_with_unit(
    db: AsyncSession, *, status: str, completed_at: datetime | None,
) -> uuid.UUID:
    src = Source(
        id=uuid.uuid4(),
        name=f"src-{uuid.uuid4().hex[:6]}",
        type="local",
        connection_config={"path": "/tmp"},
    )
    db.add(src)
    await db.flush()
    scan = Scan(
        id=uuid.uuid4(),
        source_id=src.id,
        scan_type="incremental",
        status=status,
        completed_at=completed_at,
    )
    db.add(scan)
    await db.flush()
    db.add(ScanWorkUnit(
        id=uuid.uuid4(), scan_id=scan.id, path="u", status="completed",
    ))
    await db.commit()
    return scan.id


async def _unit_count(db: AsyncSession, scan_id: uuid.UUID) -> int:
    rows = (await db.execute(
        select(ScanWorkUnit).where(ScanWorkUnit.scan_id == scan_id)
    )).scalars().all()
    return len(rows)


@pytest.mark.asyncio
async def test_purge_drops_units_of_old_terminal_scan(setup_db):
    now = datetime.now(timezone.utc)
    async with setup_db() as db:
        old = await _scan_with_unit(
            db, status="completed", completed_at=now - timedelta(days=8),
        )

    async with setup_db() as db:
        deleted = await purge_scan_work_units(db, older_than_days=7)
    assert deleted >= 1

    async with setup_db() as db:
        assert await _unit_count(db, old) == 0


@pytest.mark.asyncio
async def test_purge_spares_recent_terminal_scan(setup_db):
    now = datetime.now(timezone.utc)
    async with setup_db() as db:
        recent = await _scan_with_unit(
            db, status="completed", completed_at=now - timedelta(days=2),
        )

    async with setup_db() as db:
        await purge_scan_work_units(db, older_than_days=7)

    async with setup_db() as db:
        assert await _unit_count(db, recent) == 1


@pytest.mark.asyncio
async def test_purge_spares_running_scan(setup_db):
    async with setup_db() as db:
        running = await _scan_with_unit(
            db, status="running", completed_at=None,
        )

    async with setup_db() as db:
        await purge_scan_work_units(db, older_than_days=7)

    async with setup_db() as db:
        assert await _unit_count(db, running) == 1
