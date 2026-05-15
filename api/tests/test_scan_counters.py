"""Redis-backed per-scan counters (v0.29.2).

Covers add/read/overlay/flush_to_db semantics:

  * Multiple add() calls accumulate (HINCRBY).
  * read() returns the hash; overlay() returns row + hash.
  * flush_to_db() applies the hash as deltas to scan.* and clears
    the hash; subsequent add+flush accumulates correctly.
  * No-op safely when called on an empty hash or on a non-existent
    scan id.
  * Concurrent add() calls from different "scanners" (coroutines)
    both land — exercises the no-row-lock claim.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from akashic.models.scan import Scan
from akashic.models.source import Source
from akashic.services import scan_counters
from akashic.services.scan_pubsub import _client as redis_client


async def _seed_scan(db) -> Scan:
    src = Source(
        id=uuid.uuid4(), name=f"sc-{uuid.uuid4().hex[:6]}", type="local",
        connection_config={"path": "/tmp"},
    )
    db.add(src)
    await db.flush()
    scan = Scan(
        id=uuid.uuid4(), source_id=src.id, scan_type="full",
        status="running",
    )
    db.add(scan)
    await db.commit()
    return scan


async def _cleanup(scan_id):
    redis = redis_client()
    try:
        await redis.delete(f"akashic:scan:{scan_id}:counters")
    except Exception:  # noqa: BLE001
        pass


@pytest.mark.asyncio
async def test_add_accumulates(db_session):
    scan = await _seed_scan(db_session)
    try:
        await scan_counters.add(scan.id, files_found=10, files_new=3)
        await scan_counters.add(scan.id, files_found=5, files_new=2)
        live = await scan_counters.read(scan.id)
        assert live["files_found"] == 15
        assert live["files_new"] == 5
    finally:
        await _cleanup(scan.id)


@pytest.mark.asyncio
async def test_add_skips_zero_deltas(db_session):
    """Zero/None deltas are dropped so the hash doesn't accumulate
    no-op writes."""
    scan = await _seed_scan(db_session)
    try:
        await scan_counters.add(scan.id, files_found=0, files_new=0)
        live = await scan_counters.read(scan.id)
        assert live == {}
    finally:
        await _cleanup(scan.id)


@pytest.mark.asyncio
async def test_add_rejects_unknown_field(db_session):
    """Typos must blow up loudly, not silently grow a phantom counter
    that nothing flushes."""
    scan = await _seed_scan(db_session)
    try:
        with pytest.raises(ValueError):
            await scan_counters.add(scan.id, files_fund=10)
    finally:
        await _cleanup(scan.id)


@pytest.mark.asyncio
async def test_overlay_sums_row_and_hash(db_session):
    """overlay returns row + hash so a flushed scan + new pending
    deltas reads as the in-flight total — same semantics as the
    pre-v0.29.2 ``scan.x`` direct read."""
    scan = await _seed_scan(db_session)
    scan.files_found = 100
    await db_session.commit()
    try:
        await scan_counters.add(scan.id, files_found=25)
        live = await scan_counters.overlay(scan)
        assert live["files_found"] == 125
    finally:
        await _cleanup(scan.id)


@pytest.mark.asyncio
async def test_flush_to_db_applies_as_delta_and_clears_hash(db_session):
    scan = await _seed_scan(db_session)
    scan.files_found = 7
    await db_session.commit()
    try:
        await scan_counters.add(scan.id, files_found=10, files_new=4)
        flushed = await scan_counters.flush_to_db(db_session, scan)
        assert flushed is True
        # Row picked up the deltas.
        assert scan.files_found == 17
        assert scan.files_new == 4
        # Hash is gone.
        assert await scan_counters.read(scan.id) == {}
    finally:
        await _cleanup(scan.id)


@pytest.mark.asyncio
async def test_flush_then_add_then_flush_accumulates(db_session):
    """Multi-flush case (e.g., per-batch is_final on a multi-unit
    parallel-walker scan): each flush moves whatever is in the hash
    onto the row; subsequent adds re-populate the hash; subsequent
    flush moves THOSE deltas. The row sums correctly across the cycle."""
    scan = await _seed_scan(db_session)
    try:
        await scan_counters.add(scan.id, files_found=5)
        await scan_counters.flush_to_db(db_session, scan)
        assert scan.files_found == 5

        await scan_counters.add(scan.id, files_found=10)
        await scan_counters.flush_to_db(db_session, scan)
        assert scan.files_found == 15

        await scan_counters.add(scan.id, files_found=7)
        await scan_counters.flush_to_db(db_session, scan)
        assert scan.files_found == 22
    finally:
        await _cleanup(scan.id)


@pytest.mark.asyncio
async def test_flush_noop_on_empty_hash(db_session):
    scan = await _seed_scan(db_session)
    flushed = await scan_counters.flush_to_db(db_session, scan)
    assert flushed is False
    assert scan.files_found == 0


@pytest.mark.asyncio
async def test_concurrent_adds_from_two_scanners_both_land(db_session):
    """The whole point of the Redis hash: HINCRBY is atomic and
    lock-free. Two simulated scanners both adding to the same scan
    should produce the sum of their adds, not a lost-update."""
    scan = await _seed_scan(db_session)
    try:
        async def scanner_a():
            for _ in range(50):
                await scan_counters.add(scan.id, files_found=1)

        async def scanner_b():
            for _ in range(50):
                await scan_counters.add(scan.id, files_found=2)

        await asyncio.gather(scanner_a(), scanner_b())
        live = await scan_counters.read(scan.id)
        assert live["files_found"] == 50 + 100
    finally:
        await _cleanup(scan.id)
