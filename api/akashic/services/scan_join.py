"""Push-based multi-scanner join discovery (v0.29.0).

Pre-fix multi-scanner cooperation existed in the schema (work units,
``max_parallel_scanners``, ``_distinct_active_scanners`` cap check) but
never functioned end-to-end. The first scanner claimed the scan via
``POST /api/scans/lease`` and flipped ``assigned_scanner_id`` non-NULL;
any second scanner polling lease was filtered out by the
``assigned_scanner_id IS NULL`` clause and idled forever. It had no
way to discover that an in-flight scan needed help.

This module is the discovery channel: once the first scanner finishes
enumerating units in ``POST /api/scans/{id}/work/split``, the API
LPUSHes a per-eligible-scanner join notification onto a Redis list
(``scanner:{id}:scan_join``). Each other eligible scanner long-polls
``GET /api/scanners/{id}/scans/long-poll`` and BRPOPs the next
notification, then dispatches to the same ``runUnitCoordinated`` path
the original holder runs — leasing work units from the existing pool.

Pattern mirrors ``probe_dispatch`` (v0.28.2): durable list-backed
queue so a momentary subscribe gap doesn't drop the notification,
``LTRIM`` caps backlog at 50 per scanner so an offline scanner can't
accumulate unbounded notifications.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.services.scan_pubsub import _client as _redis_client

logger = logging.getLogger(__name__)

# Cap per scanner — same reasoning as probe_dispatch's 100. A scanner
# offline for a while accumulating join notifications would just see
# its queue trimmed; on reconnect it pulls whatever's freshest.
_JOIN_QUEUE_CAP = 50


def _join_channel(scanner_id: uuid.UUID | str) -> str:
    return f"scanner:{scanner_id}:scan_join"


async def notify_eligible_joiners(
    *,
    db: AsyncSession,
    scan,
    source,
    exclude_scanner_id: uuid.UUID,
) -> int:
    """LPUSH a join-payload onto each eligible scanner's queue.

    ``exclude_scanner_id`` is the scanner that already holds the scan
    lease — they don't need to be told about their own scan. Returns
    the count of scanners notified (for logging).

    Eligibility mirrors ``_eligible_scanners_for`` in routers/sources.py:
    enabled + pool match + allowed_source_ids match + currently online.

    Per-scanner JWT is minted fresh at notify time so the joiner can
    immediately call /api/scans/{id}/work/lease + /api/ingest/batch
    without a separate handshake round trip. Scanner_id claim embeds
    in the JWT (v0.28.2 pattern) so attribution propagates.
    """
    if source is None:
        return 0
    if source.max_parallel_scanners <= 1:
        # Nothing to share — don't even enumerate candidates.
        return 0

    from akashic.models.scanner import Scanner
    from akashic.routers.scanners import _mint_ingest_jwt
    from akashic.routers.sources import _eligible_scanners_for
    from akashic.services.source_config import merge_host_and_source
    from akashic.services.source_oauth import (
        OAuthExchangeFailed,
        mint_access_token_for_source,
    )

    all_scanners = list(
        (await db.execute(select(Scanner))).scalars().all()
    )
    candidates = _eligible_scanners_for(source, all_scanners, online_only=True)
    candidates = [s for s in candidates if s.id != exclude_scanner_id]
    if not candidates:
        return 0

    merged = merge_host_and_source(getattr(source, "host", None), source)
    # OAuth-shaped sources need a fresh access token in the payload so
    # the joiner doesn't have to round-trip back for one. Failure here
    # isn't fatal — joiners can still long-poll, they'll just hit the
    # same OAuth wall the lease holder did and surface it the same way.
    try:
        oauth_pair = await mint_access_token_for_source(db, source.id)
    except OAuthExchangeFailed as exc:
        logger.warning(
            "scan_join: oauth refresh failed for source=%s scan=%s: %s",
            source.id, scan.id, exc.detail[:200],
        )
        oauth_pair = None
    if oauth_pair is not None:
        access_token, expires_at = oauth_pair
        merged["access_token"] = access_token
        if expires_at is not None:
            merged["access_token_expires_at"] = expires_at.isoformat()

    redis = _redis_client()
    notified = 0
    for s in candidates:
        api_jwt = await _mint_ingest_jwt(db, s.id)
        if api_jwt is None:
            # No admin user exists — same edge case lease handles. Skip
            # this scanner; the lease holder will keep going alone.
            continue
        payload = {
            "scan_id": str(scan.id),
            "scan_type": scan.scan_type or "incremental",
            "source": {
                "id": str(source.id),
                "type": source.type,
                "connection_config": merged,
                "exclude_patterns": source.exclude_patterns or [],
                "max_parallel_scanners": source.max_parallel_scanners,
            },
            "api_jwt": api_jwt,
        }
        chan = _join_channel(s.id)
        body = json.dumps(payload, default=str)
        try:
            await redis.lpush(chan, body)
            await redis.ltrim(chan, 0, _JOIN_QUEUE_CAP - 1)
            notified += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "scan_join: LPUSH failed scanner=%s scan=%s: %s",
                s.id, scan.id, exc,
            )
    if notified:
        logger.info(
            "scan_join: scan=%s source=%s notified %d eligible scanner(s) "
            "(holder=%s excluded)",
            scan.id, source.id, notified, exclude_scanner_id,
        )
    return notified


async def wait_for_scan_join(
    scanner_id: uuid.UUID, timeout_s: float = 30.0,
) -> Optional[dict[str, Any]]:
    """BRPOP one join notification or return None after ``timeout_s``.

    Mirrors ``probe_dispatch.wait_for_probe`` — same durability rationale
    (list-backed so a notification published while the scanner was
    between long-polls isn't lost).
    """
    redis = _redis_client()
    chan = _join_channel(scanner_id)
    timeout_int = max(1, int(timeout_s))
    try:
        result = await redis.brpop(chan, timeout=timeout_int)
    except Exception as exc:  # noqa: BLE001
        logger.warning("scan_join BRPOP failed for %s: %s", chan, exc)
        return None
    if result is None:
        return None
    _key, data = result
    if not isinstance(data, str):
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        logger.warning("scan_join: malformed join payload on %s", chan)
        return None
