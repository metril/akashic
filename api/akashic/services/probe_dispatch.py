"""Dispatch a credentialed reachability probe to a scanner agent.

The API never dials shares directly. Reachability — does this scanner
have credentials and can it list this share — is a scanner-side fact;
the API just orchestrates by publishing probe requests to per-scanner
Redis channels, which the agents consume via long-poll.

OAuth-shaped sources (gdrive, onedrive, dropbox) get a freshly minted
access token injected into the published `connection_config` so the
agent doesn't need to call back to /api/scanners/oauth/access-token
just to start a probe.

The host /api/hosts/{id}/online-check endpoint is the only "API can
do this" path — and it's a TCP probe, not reachability.

Why long-poll over websockets: the agent already talks HTTP-only
(JWT-authed POSTs); adding a sustained connection type would mean new
transport, new auth shape, and fleet-side rollout risk. Long-poll
reuses the existing handshake and keeps the agent stateless.

Pubsub channels are namespaced separately from the scan-progress
channels in `scan_pubsub` so probe traffic doesn't have to be filtered
out by scan WS subscribers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from akashic.services.scan_pubsub import _client as _redis_client
from akashic.services.source_config import merge_host_and_source

logger = logging.getLogger(__name__)


# ── Channel scheme ────────────────────────────────────────────────────────

# Where the API publishes a probe request for a specific scanner. The
# agent's long-poll endpoint subscribes (one subscriber per active
# scanner) and yields the next message it receives.
def _probe_channel(scanner_id: uuid.UUID | str) -> str:
    return f"scanner:{scanner_id}:probe"


# Where the report endpoint forwards the result for a specific request,
# so the dispatcher waiting on the originating /test-… call can resume.
def _result_channel(request_id: uuid.UUID | str) -> str:
    return f"probe:{request_id}:result"


# ── Remote dispatch (the only credentialed-probe path) ───────────────────


async def dispatch_remote(
    *,
    db,
    source,
    scanner_ids: list[uuid.UUID],
    timeout_s: float = 5.0,
    triggered_by: Optional[uuid.UUID] = None,
) -> dict[uuid.UUID, dict[str, Any]]:
    """Publish a probe request per scanner and collect any results that
    arrive within ``timeout_s``.

    Returns a dict keyed by scanner_id with the report payload for each
    scanner that responded in time. Slow scanners drop off the dict —
    the report endpoint still persists their late result via
    ``reachability_results.record_result`` and pushes it to source-
    event subscribers, so the frontend will see them whenever they
    land.

    For OAuth-shaped sources, mints a fresh access token and injects it
    into the published connection_config. Failure to refresh the OAuth
    grant is reported as a synthetic per-scanner result with
    `step="auth"` so the user gets a clear "sign in again" signal
    without needing the agent round-trip.
    """
    from akashic.services.source_oauth import (
        OAuthExchangeFailed,
        mint_access_token_for_source,
    )

    request_id = uuid.uuid4()
    merged = merge_host_and_source(source.host, source)

    oauth_failure: Optional[str] = None
    try:
        oauth_pair = await mint_access_token_for_source(db, source.id)
    except OAuthExchangeFailed as exc:
        oauth_failure = f"oauth refresh failed: {exc.detail[:200]}"
    else:
        if oauth_pair is not None:
            merged["access_token"] = oauth_pair[0]

    if oauth_failure is not None:
        # Short-circuit: fabricate a per-scanner failure for each
        # requested scanner, persist it, and return without touching
        # the agent. The OAuth grant problem is the source's, not
        # the scanner's network.
        from akashic.services import reachability_results
        now = datetime.now(timezone.utc)
        out: dict[uuid.UUID, dict[str, Any]] = {}
        for sid in scanner_ids:
            await reachability_results.record_result(
                db=db, source_id=source.id, scanner_id=sid,
                ok=False, step="auth", error=oauth_failure,
                started_at=now, triggered_by=triggered_by,
            )
            out[sid] = {
                "scanner_id": str(sid),
                "ok": False,
                "step": "auth",
                "error": oauth_failure,
                "completed_at": now.isoformat(),
            }
        return out

    payload = {
        "request_id": str(request_id),
        "source_id": str(source.id),
        "source_type": source.type,
        "connection_config": merged,
    }

    redis = _redis_client()
    pubsub = redis.pubsub()

    received: dict[uuid.UUID, dict[str, Any]] = {}

    try:
        # Subscribe FIRST so we don't miss a fast reporter that beats us
        # to the result channel. With Redis pub/sub there's no replay —
        # publishing before subscribe means the message is lost.
        await pubsub.subscribe(_result_channel(request_id))

        # v0.28.2 — push the probe onto each scanner's per-id queue.
        # The previous PUBLISH implementation lost messages whenever
        # the scanner's long-poll was momentarily unsubscribed
        # (between report-post and next long-poll subscribe). Redis
        # LISTs are durable until consumed, so the agent picks the
        # probe up on its next BRPOP regardless of timing. LTRIM caps
        # each queue at 100 items so a disconnected scanner can't
        # accumulate unbounded backlog.
        body = json.dumps(payload, default=str)
        for sid in scanner_ids:
            chan = _probe_channel(sid)
            await redis.lpush(chan, body)
            await redis.ltrim(chan, 0, 99)

        deadline = asyncio.get_running_loop().time() + timeout_s
        wanted = {str(sid) for sid in scanner_ids}
        while wanted and asyncio.get_running_loop().time() < deadline:
            remaining = deadline - asyncio.get_running_loop().time()
            try:
                msg = await asyncio.wait_for(
                    pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=remaining,
                    ),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                break
            if msg is None or msg.get("type") != "message":
                continue
            data = msg.get("data")
            if not isinstance(data, str):
                continue
            try:
                report = json.loads(data)
            except json.JSONDecodeError:
                logger.warning("probe_dispatch: malformed result payload")
                continue
            sid = report.get("scanner_id")
            if sid in wanted:
                received[uuid.UUID(sid)] = report
                wanted.discard(sid)
    finally:
        try:
            await pubsub.unsubscribe(_result_channel(request_id))
        except Exception as exc:  # noqa: BLE001
            logger.debug("probe_dispatch unsubscribe noise: %s", exc)
        try:
            await pubsub.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.debug("probe_dispatch aclose noise: %s", exc)

    return received


# ── Result fan-out (called from the report endpoint) ──────────────────────


async def publish_report(
    *,
    request_id: uuid.UUID,
    scanner_id: uuid.UUID,
    source_id: uuid.UUID,
    ok: bool,
    step: Optional[str],
    error: Optional[str],
    completed_at: datetime,
) -> None:
    """Broadcast a scanner-reported result on two channels:

      * `probe:{request_id}:result` — for the synchronous dispatcher
        waiting on this request.
      * `source:{source_id}:reachability` — for any subscribed frontend
        watching the source's eligibility panel.
    """
    redis = _redis_client()
    payload = {
        "request_id": str(request_id),
        "scanner_id": str(scanner_id),
        "source_id": str(source_id),
        "ok": ok,
        "step": step,
        "error": error,
        "completed_at": completed_at.isoformat(),
    }
    body = json.dumps(payload, default=str)
    try:
        await redis.publish(_result_channel(request_id), body)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "probe_dispatch: result publish failed for request=%s: %s",
            request_id, exc,
        )
    try:
        await redis.publish(f"source:{source_id}:reachability", body)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "probe_dispatch: source-event publish failed for source=%s: %s",
            source_id, exc,
        )


# ── Long-poll subscription (called from /probes/long-poll) ────────────────


async def wait_for_probe(
    scanner_id: uuid.UUID, timeout_s: float = 30.0,
) -> Optional[dict[str, Any]]:
    """Block on ``scanner:{id}:probe`` until a probe arrives, or
    return None after ``timeout_s``. Used by the long-poll endpoint.

    v0.28.2 — switched from pub/sub subscribe to ``BRPOP`` against a
    Redis list. Pub/sub had no replay, so any probe published while
    the scanner was between long-poll calls (subscribe → wait →
    unsubscribe → reconnect) was silently dropped — especially
    visible under bulk fan-out with multiple scanners. The list-based
    queue absorbs probes whenever they're produced; the agent picks
    them up on its next BRPOP regardless of subscribe timing.
    """
    redis = _redis_client()
    chan = _probe_channel(scanner_id)
    # BRPOP returns (key, value) tuple or None on timeout. Redis
    # requires an integer timeout (seconds); 0 means "wait forever",
    # which we deliberately avoid here.
    timeout_int = max(1, int(timeout_s))
    try:
        result = await redis.brpop(chan, timeout=timeout_int)
    except Exception as exc:  # noqa: BLE001
        logger.warning("probe_dispatch BRPOP failed for %s: %s", chan, exc)
        return None
    if result is None:
        return None
    # `decode_responses=True` is set on the shared client, so values
    # come back as strings.
    _key, data = result
    if not isinstance(data, str):
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        logger.warning("probe_dispatch: malformed probe payload on %s", chan)
        return None
