"""v0.31.5 — the ingest `is_final` batch must not finalize a
unit-coordinated (multi-scanner) scan.

A multi-scanner scan splits into work units; each non-root unit's walk
posts its own `is_final` batch. Treating any one of them as *scan*-final
completes the whole scan after its first unit — truncating it, sweeping
not-yet-scanned entries as stale, and aborting the sibling scanners.

The ingest handler now defers to the work-unit finalize path
(`_maybe_finalize_scan`) whenever the scan has `ScanWorkUnit` rows. A
scan with no units is single-scanner — ingest still owns its completion,
and now also records `cancellation_reason` so the scanner's heartbeat
decoder never logs a false "scan cancelled by user".
"""
import uuid

import pytest
from sqlalchemy import select

from akashic.auth.jwt import create_ingest_token
from akashic.models.scan import Scan
from akashic.models.scan_work_unit import ScanWorkUnit
from akashic.models.source import Source
from akashic.models.user import User
from tests.conftest import seed_scan


async def _ingest_final(client, token, source_id, scan_id):
    return await client.post(
        "/api/ingest/batch",
        json={
            "source_id": str(source_id),
            "scan_id": str(scan_id),
            "is_final": True,
            "entries": [],
        },
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_ingest_is_final_defers_when_scan_has_work_units(client, db_session):
    """A scan with a pending ScanWorkUnit is unit-coordinated — an
    `is_final` ingest batch must leave it running, not complete it."""
    user = User(
        id=uuid.uuid4(), username="u", email="u@e",
        password_hash="x", role="admin",
    )
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add_all([user, source])
    await db_session.commit()

    scan_id = await seed_scan(db_session, source.id, status="running")
    # One work unit still pending — this scan is unit-coordinated.
    db_session.add(ScanWorkUnit(
        id=uuid.uuid4(), scan_id=scan_id, path="a", status="pending",
    ))
    await db_session.commit()

    token = create_ingest_token(str(user.id))
    resp = await _ingest_final(client, token, source.id, scan_id)
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    scan = (await db_session.execute(
        select(Scan).where(Scan.id == scan_id)
    )).scalar_one()
    assert scan.status == "running", (
        "ingest is_final wrongly completed a unit-coordinated scan"
    )
    assert scan.completed_at is None
    assert scan.cancellation_reason is None


@pytest.mark.asyncio
async def test_ingest_is_final_completes_single_scanner_scan(client, db_session):
    """A scan with no work units is single-scanner — the `is_final`
    batch still completes it, and now sets cancellation_reason so the
    scanner logs an accurate terminal message (not a false cancel)."""
    user = User(
        id=uuid.uuid4(), username="u2", email="u2@e",
        password_hash="x", role="admin",
    )
    source = Source(id=uuid.uuid4(), name="s2", type="local", connection_config={})
    db_session.add_all([user, source])
    await db_session.commit()

    scan_id = await seed_scan(db_session, source.id, status="running")
    token = create_ingest_token(str(user.id))
    resp = await _ingest_final(client, token, source.id, scan_id)
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    scan = (await db_session.execute(
        select(Scan).where(Scan.id == scan_id)
    )).scalar_one()
    assert scan.status == "completed"
    assert scan.cancellation_reason == "completed"
    assert scan.completed_at is not None
