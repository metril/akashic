"""Write-side dedup in reachability_results.record_result (v0.29.0).

Pre-fix every probe inserted a new row, so the AllowedScannersPanel
history disclosure stacked identical green/red dots for repeated
no-change clicks. Post-fix: identical (ok, step, error) on top of the
most recent row UPDATEs that row's completed_at; state changes still
INSERT a fresh row.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from akashic.models.reachability_result import ReachabilityResult
from akashic.models.scanner import Scanner
from akashic.models.source import Source
from akashic.services import reachability_results
from akashic.services.scanner_keys import generate_keypair


async def _seed_source(db) -> uuid.UUID:
    src = Source(
        id=uuid.uuid4(),
        name=f"dedup-src-{uuid.uuid4().hex[:6]}",
        type="smb",
        connection_config={"host": "h", "share": "s"},
    )
    db.add(src)
    await db.commit()
    return src.id


async def _seed_scanner(db) -> uuid.UUID:
    kp = generate_keypair()
    s = Scanner(
        id=uuid.uuid4(),
        name=f"dedup-scn-{uuid.uuid4().hex[:6]}",
        public_key_pem=kp.public_pem,
        key_fingerprint=kp.fingerprint,
    )
    db.add(s)
    await db.commit()
    return s.id


@pytest.mark.asyncio
async def test_consecutive_identical_results_merge(db_session):
    source_id = await _seed_source(db_session)
    scanner_id = await _seed_scanner(db_session)

    base_started = datetime.now(timezone.utc) - timedelta(seconds=10)
    first = await reachability_results.record_result(
        db=db_session, source_id=source_id, scanner_id=scanner_id,
        ok=True, step=None, error=None, started_at=base_started,
    )
    await db_session.commit()

    later = base_started + timedelta(seconds=5)
    second = await reachability_results.record_result(
        db=db_session, source_id=source_id, scanner_id=scanner_id,
        ok=True, step=None, error=None, started_at=later,
    )
    await db_session.commit()

    assert second.id == first.id
    # The completed_at must not regress; on fast hardware two calls in
    # the same microsecond produce equal timestamps. The contract is
    # "freshness moves forward or stays put for this row".
    assert second.completed_at >= first.completed_at
    assert second.started_at == later

    # And only one row exists for the pair.
    rows = (await db_session.execute(
        select(ReachabilityResult)
        .where(ReachabilityResult.source_id == source_id)
        .where(ReachabilityResult.scanner_id == scanner_id)
    )).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_state_change_inserts_new_row(db_session):
    """ok→fail (or any change in (ok, step, error)) inserts a new
    row; the prior row is preserved so the history still shows the
    transition."""
    source_id = await _seed_source(db_session)
    scanner_id = await _seed_scanner(db_session)
    now = datetime.now(timezone.utc)

    a = await reachability_results.record_result(
        db=db_session, source_id=source_id, scanner_id=scanner_id,
        ok=True, step=None, error=None, started_at=now,
    )
    await db_session.commit()

    b = await reachability_results.record_result(
        db=db_session, source_id=source_id, scanner_id=scanner_id,
        ok=False, step="auth", error="bad creds", started_at=now,
    )
    await db_session.commit()

    assert b.id != a.id

    c = await reachability_results.record_result(
        db=db_session, source_id=source_id, scanner_id=scanner_id,
        ok=True, step=None, error=None, started_at=now,
    )
    await db_session.commit()
    assert c.id != b.id

    rows = (await db_session.execute(
        select(ReachabilityResult)
        .where(ReachabilityResult.source_id == source_id)
        .where(ReachabilityResult.scanner_id == scanner_id)
        .order_by(ReachabilityResult.completed_at.asc())
    )).scalars().all()
    assert len(rows) == 3
    assert [r.ok for r in rows] == [True, False, True]


@pytest.mark.asyncio
async def test_dedup_is_pair_scoped(db_session):
    """Two different scanners on the same source must not collapse
    onto each other's rows — the dedup is per (source, scanner) pair."""
    source_id = await _seed_source(db_session)
    scanner_a = await _seed_scanner(db_session)
    scanner_b = await _seed_scanner(db_session)
    now = datetime.now(timezone.utc)

    a = await reachability_results.record_result(
        db=db_session, source_id=source_id, scanner_id=scanner_a,
        ok=True, step=None, error=None, started_at=now,
    )
    b = await reachability_results.record_result(
        db=db_session, source_id=source_id, scanner_id=scanner_b,
        ok=True, step=None, error=None, started_at=now,
    )
    await db_session.commit()

    assert a.id != b.id


@pytest.mark.asyncio
async def test_dedup_includes_inline_probes(db_session):
    """scanner_id=None probes (inline API-side checks) must also
    dedup against the previous None-scanner row for the same source."""
    source_id = await _seed_source(db_session)
    now = datetime.now(timezone.utc)

    a = await reachability_results.record_result(
        db=db_session, source_id=source_id, scanner_id=None,
        ok=True, step=None, error=None, started_at=now,
    )
    b = await reachability_results.record_result(
        db=db_session, source_id=source_id, scanner_id=None,
        ok=True, step=None, error=None, started_at=now,
    )
    await db_session.commit()
    assert a.id == b.id
