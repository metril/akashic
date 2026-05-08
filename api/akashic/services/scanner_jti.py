"""Replay protection for scanner JWTs (review I7).

Each token carries a `jti` claim (random per-mint UUID). The verifier
records the (scanner_id, jti) pair in Redis with TTL ≈ token's
remaining lifetime; if the same jti shows up again before its key
expires, the second request is rejected as a replay.

Atomic insert via SET NX so two near-simultaneous requests with the
same jti can't both pass the check. If Redis is unreachable we
fail-open (log + allow) — the JWT signature + exp + iss/sub guards
still hold; this is defence-in-depth, not the primary auth.
"""
from __future__ import annotations

import logging
import time

from redis.asyncio import Redis

from akashic.config import settings

logger = logging.getLogger(__name__)


_redis: Redis | None = None


def _client() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _key(scanner_id: str, jti: str) -> str:
    return f"scanner:jti:{scanner_id}:{jti}"


async def claim_jti(scanner_id: str, jti: str, exp_unix: int) -> bool:
    """Record (scanner_id, jti). Returns True if this is the first time
    we've seen it (request continues), False if it's a replay (caller
    should 401).

    TTL = max(60s, exp - now). The 60s floor covers near-expiry tokens
    so a replay arriving in the last second still gets blocked.
    Redis errors → log + return True (fail-open)."""
    if not jti or not isinstance(jti, str):
        # Tokens minted before the jti rollout don't carry one. Allow
        # them through so a mid-deploy scanner agent doesn't get
        # locked out; remove this branch once the floor scanner
        # version is past the jti-claim release.
        return True
    ttl = max(60, int(exp_unix) - int(time.time()))
    try:
        # SET key value NX EX ttl — atomic insert-if-absent with TTL.
        ok = await _client().set(_key(scanner_id, jti), "1", nx=True, ex=ttl)
    except Exception as exc:
        logger.warning(
            "scanner_jti.claim_jti redis failure for %s — failing open: %s",
            scanner_id, exc,
        )
        return True
    return bool(ok)
