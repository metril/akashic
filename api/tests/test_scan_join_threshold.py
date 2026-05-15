"""v0.29.5 — scan_join uses a wider online threshold than bulk-probe.

The bulk-probe path stays at 120 s (don't waste 5 s per dead scanner).
The notify path uses 600 s — the join queue is durable, missing the
notification is the bug, notifying a marginally-stale scanner is free.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from akashic.models.scan import Scan
from akashic.models.scanner import Scanner
from akashic.models.source import Source
from akashic.services import scan_join
from akashic.services.scan_pubsub import _client as redis_client
from akashic.services.scanner_keys import generate_keypair


async def _seed_scanner(db, *, last_seen_delta: timedelta) -> Scanner:
    kp = generate_keypair()
    s = Scanner(
        id=uuid.uuid4(),
        name=f"scn-{uuid.uuid4().hex[:6]}",
        public_key_pem=kp.public_pem,
        key_fingerprint=kp.fingerprint,
        last_seen_at=datetime.now(timezone.utc) - last_seen_delta,
    )
    db.add(s)
    await db.commit()
    return s


@pytest.mark.asyncio
async def test_notify_path_includes_scanner_stale_at_200s(setup_db):
    """A scanner whose last_seen_at is 200 s old (between 120 s and
    600 s) is excluded by the bulk-probe path's 120 s window but
    included by the notify path's 600 s window. Notify should LPUSH
    the join payload."""
    from akashic.models.user import User
    admin = User(
        id=uuid.uuid4(), username=f"u-{uuid.uuid4().hex[:6]}",
        email="x@e", password_hash="x", role="admin",
    )
    async with setup_db() as session:
        session.add(admin)
        await session.commit()

        src = Source(
            id=uuid.uuid4(),
            name=f"src-{uuid.uuid4().hex[:6]}",
            type="smb",
            connection_config={"host": "h", "share": "s"},
            max_parallel_scanners=2,
        )
        session.add(src)
        await session.commit()

        scan = Scan(
            id=uuid.uuid4(), source_id=src.id, scan_type="full",
            status="running",
        )
        session.add(scan)
        await session.commit()

        holder = await _seed_scanner(session, last_seen_delta=timedelta(seconds=10))
        stale = await _seed_scanner(session, last_seen_delta=timedelta(seconds=200))

        notified = await scan_join.notify_eligible_joiners(
            db=session, scan=scan, source=src,
            exclude_scanner_id=holder.id,
        )
        assert notified == 1, \
            f"expected the 200s-stale scanner to be notified; got count={notified}"

    redis = redis_client()
    try:
        # Stale scanner got the payload; holder did not.
        body = await redis.rpop(scan_join._join_channel(stale.id))
        assert body is not None
        payload = json.loads(body)
        assert payload["scan_id"] == str(scan.id)
        assert await redis.llen(scan_join._join_channel(holder.id)) == 0
    finally:
        await redis.delete(
            scan_join._join_channel(stale.id),
            scan_join._join_channel(holder.id),
        )


@pytest.mark.asyncio
async def test_bulk_probe_path_still_excludes_scanner_stale_at_200s(setup_db):
    """The default `_eligible_scanners_for` (online_only=True, 120 s
    threshold) still excludes 200s-stale scanners. Confirms the
    softer notify-path threshold doesn't leak through to test-scanners
    paths."""
    from akashic.routers.sources import _eligible_scanners_for

    async with setup_db() as session:
        src = Source(
            id=uuid.uuid4(), name=f"s-{uuid.uuid4().hex[:6]}",
            type="smb", connection_config={"host": "h", "share": "s"},
        )
        session.add(src)
        await session.commit()
        stale = await _seed_scanner(session, last_seen_delta=timedelta(seconds=200))
        result = _eligible_scanners_for(src, [stale])
        assert result == [], \
            "default 120s threshold should still exclude the 200s-stale scanner"


@pytest.mark.asyncio
async def test_notify_eligible_joiners_zero_logs_diagnostic(setup_db, caplog):
    """When 0 scanners are eligible the helper emits an INFO log so
    the user can spot the gap from `docker compose logs api`."""
    import logging
    async with setup_db() as session:
        src = Source(
            id=uuid.uuid4(), name=f"s-{uuid.uuid4().hex[:6]}",
            type="smb", connection_config={"host": "h", "share": "s"},
            max_parallel_scanners=2,
        )
        session.add(src)
        await session.commit()
        scan = Scan(
            id=uuid.uuid4(), source_id=src.id, scan_type="full",
            status="running",
        )
        session.add(scan)
        await session.commit()

        with caplog.at_level(logging.INFO, logger="akashic.services.scan_join"):
            count = await scan_join.notify_eligible_joiners(
                db=session, scan=scan, source=src,
                exclude_scanner_id=uuid.uuid4(),
            )
        assert count == 0
        # The "0 eligible scanner(s)" diagnostic should appear.
        msgs = " ".join(r.message for r in caplog.records)
        assert "0 eligible" in msgs or "0 of" in msgs, \
            f"expected eligibility diagnostic; got: {msgs}"
