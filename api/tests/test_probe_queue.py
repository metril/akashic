"""Durable probe queue (v0.28.2).

Replaces the pub/sub-based dispatch the v0.28.1 version used. Covers:

  * LPUSH then BRPOP delivers the probe even when the consumer
    subscribes AFTER the publish (impossible with pub/sub — message
    would have been dropped).
  * LTRIM caps the queue at 100 items per scanner so an offline
    scanner can't accumulate unbounded backlog.
  * Multiple back-to-back LPUSHes queue cleanly and a single consumer
    drains them in LIFO-from-tail order via BRPOP (matches the agent's
    "process one at a time" model).
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from akashic.services.probe_dispatch import _probe_channel, wait_for_probe
from akashic.services.scan_pubsub import _client as redis_client


@pytest.mark.asyncio
async def test_brpop_picks_up_probe_pushed_before_subscribe(setup_db):
    """The pub/sub regime lost any probe published while the long-poll
    was momentarily unsubscribed. With a list-backed queue the BRPOP
    consumer picks up the probe regardless of timing — even when it
    arrives well before the consumer starts waiting."""
    scanner_id = uuid.uuid4()
    redis = redis_client()
    chan = _probe_channel(scanner_id)
    payload = {"request_id": str(uuid.uuid4()), "source_id": "src-1"}
    await redis.lpush(chan, json.dumps(payload))

    got = await wait_for_probe(scanner_id, timeout_s=2.0)
    assert got is not None
    assert got["request_id"] == payload["request_id"]


@pytest.mark.asyncio
async def test_brpop_returns_none_on_timeout(setup_db):
    """No probe queued → wait_for_probe returns None after the timeout."""
    scanner_id = uuid.uuid4()
    got = await wait_for_probe(scanner_id, timeout_s=1.0)
    assert got is None


@pytest.mark.asyncio
async def test_dispatch_remote_lpushes_per_scanner_with_ltrim_cap(setup_db):
    """dispatch_remote LPUSHes the probe onto each scanner's queue and
    caps it at 100 items via LTRIM. A pre-filled queue of 200 stale
    probes is trimmed back to 100 after one dispatch."""
    from akashic.models.source import Source
    from akashic.services.probe_dispatch import dispatch_remote

    redis = redis_client()
    scanner_id = uuid.uuid4()
    chan = _probe_channel(scanner_id)

    # Pre-fill with 200 stale probes so LTRIM has work to do.
    for i in range(200):
        await redis.lpush(chan, json.dumps({"stale": i}))

    # Source row needs to exist for dispatch_remote's payload build.
    src_id = uuid.uuid4()
    async with setup_db() as session:
        session.add(Source(
            id=src_id, name=f"probe-q-{uuid.uuid4().hex[:6]}", type="smb",
            connection_config={"host": "h", "share": "s"},
        ))
        await session.commit()
        src = (await session.execute(
            __import__("sqlalchemy").select(Source).where(Source.id == src_id)
        )).scalar_one()

        # Fast-fail timeout — we're only testing the LPUSH+LTRIM side,
        # not waiting for any agent reply.
        await dispatch_remote(
            db=session, source=src,
            scanner_ids=[scanner_id], timeout_s=0.25,
        )

    llen = await redis.llen(chan)
    assert llen == 100, f"expected queue trimmed to 100, got {llen}"
    # Cleanup so other tests don't see leftovers.
    await redis.delete(chan)


@pytest.mark.asyncio
async def test_brpop_drains_in_lifo_order_from_tail(setup_db):
    """LPUSH inserts at the head, BRPOP pops from the tail — so the
    oldest item is delivered first. With back-to-back LPUSHes that
    gives FIFO consumption, which is what the agent expects."""
    scanner_id = uuid.uuid4()
    redis = redis_client()
    chan = _probe_channel(scanner_id)
    try:
        for i in range(3):
            await redis.lpush(chan, json.dumps({"order": i}))
        # Drain via repeated BRPOP — order should be 0, 1, 2 (oldest first).
        seen: list[int] = []
        for _ in range(3):
            got = await wait_for_probe(scanner_id, timeout_s=1.0)
            assert got is not None
            seen.append(got["order"])
        assert seen == [0, 1, 2]
    finally:
        await redis.delete(chan)
