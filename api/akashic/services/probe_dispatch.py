"""Dispatch a reachability probe to where it actually runs.

Two paths share this module:

  * ``dispatch_inline`` — the API itself dials the source via
    ``test_connection``. Used for non-local sources (SMB / NFS / S3 /
    SharePoint / WebDAV / OAuth-shaped clouds — everything that's
    network-addressable from the API container). Synchronous, returns
    the result directly.

  * ``dispatch_remote`` — the probe lives in the agent's filesystem
    namespace (``type=local`` sources, where only the agent has the
    bind-mount). The API publishes a request to the scanner's per-id
    Redis channel; the scanner consumes via the long-poll endpoint and
    POSTs the result back, which the API forwards onto a per-request
    response channel. The dispatcher subscribes to that channel and
    returns whatever results land within ``timeout_s``.

Why long-poll instead of websockets to the agent: the agent already
talks HTTP-only (JWT-authed POSTs); adding a sustained connection
type means new transport, new auth shape, and fleet-side rollout
risk. A long-poll loop reuses the existing handshake and keeps the
agent stateless.

The pubsub channels are namespaced separately from the scan-progress
channels in ``scan_pubsub`` so the probe traffic doesn't have to be
filtered out by scan WS subscribers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.source import Source
from akashic.services import reachability_results
from akashic.services.scan_pubsub import _client as _redis_client
from akashic.services.source_config import merge_host_and_source
# Re-exported here so tests can monkeypatch `probe_dispatch.test_connection`
# to stub the inline probe without faking the source_tester module-level
# function (which other code paths still call).
from akashic.services.source_tester import test_connection

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


# ── Inline dispatch (non-local) ───────────────────────────────────────────


async def dispatch_inline(
    *,
    db: AsyncSession,
    source: Source,
    scanner_id: Optional[uuid.UUID] = None,
    triggered_by: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    """Probe a non-local source from the API process and persist a
    `reachability_results` row.

    `scanner_id` is informational here — the API does the actual dialing
    so the row is recorded against ``scanner_id=None`` (an inline probe
    has no agent attribution). The caller may still pass a scanner id
    when running this on behalf of the per-row "Test" button so the
    eligibility panel sees the result attributed to the row the user
    clicked. We honour that and record it against the requested
    scanner_id; the test_connection result reflects the API's view of
    reachability, which is what matters for non-local sources where
    every agent dials the same network anyway.
    """
    from akashic.services.source_oauth import (
        OAuthExchangeFailed,
        mint_access_token_for_source,
    )

    started = datetime.now(timezone.utc)
    merged = merge_host_and_source(source.host, source)

    # OAuth-shaped sources need a fresh access token before the probe;
    # match what the legacy /check-reachability did so cloud-drive tests
    # stay supported.
    try:
        oauth_pair = await mint_access_token_for_source(db, source.id)
    except OAuthExchangeFailed as exc:
        result_dict = {
            "ok": False,
            "step": "auth",
            "error": f"oauth refresh failed: {exc.detail[:200]}",
        }
    else:
        if oauth_pair is not None:
            merged["access_token"] = oauth_pair[0]
        result = await asyncio.to_thread(test_connection, source.type, merged)
        result_dict = {
            "ok": result.ok,
            "step": result.step,
            "error": result.error,
        }

    await reachability_results.record_result(
        db=db,
        source_id=source.id,
        scanner_id=scanner_id,
        ok=result_dict["ok"],
        step=result_dict.get("step"),
        error=result_dict.get("error"),
        started_at=started,
        triggered_by=triggered_by,
    )
    return {
        "scanner_id": str(scanner_id) if scanner_id else None,
        "ok": result_dict["ok"],
        "step": result_dict.get("step"),
        "error": result_dict.get("error"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Remote dispatch (local sources, agent-side probe) ─────────────────────


async def dispatch_remote(
    *,
    source: Source,
    scanner_ids: list[uuid.UUID],
    timeout_s: float = 5.0,
) -> dict[uuid.UUID, dict[str, Any]]:
    """Publish a probe request to each scanner channel and collect any
    results that arrive within ``timeout_s``.

    Returns a dict keyed by scanner_id with the report payload for each
    scanner that responded in time. Slow scanners drop off the dict —
    the report endpoint still persists their late result via
    ``reachability_results.record_result`` and pushes it to source-
    event subscribers, so the frontend will see them whenever they
    land.
    """
    request_id = uuid.uuid4()
    merged = merge_host_and_source(source.host, source)
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

        # Publish one probe request per scanner. Each scanner's long-poll
        # subscriber consumes from its own scanner channel; the result
        # channel is shared per request_id and tagged by scanner_id.
        for sid in scanner_ids:
            await redis.publish(
                _probe_channel(sid), json.dumps(payload, default=str),
            )

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
    """Subscribe to ``scanner:{id}:probe`` and yield the first message,
    or return None on timeout. Used by the long-poll endpoint.

    Uses pubsub.listen() (the same async iterator scan_pubsub.subscribe
    relies on) rather than get_message — listen() handles the SUBSCRIBE
    confirmation cleanly so a fast publisher doesn't race the
    subscriber and silently drop the message.
    """
    redis = _redis_client()
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(_probe_channel(scanner_id))

        async def _next_message():
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    # Skip the SUBSCRIBE confirmation and any
                    # housekeeping frames.
                    continue
                return message
            return None

        try:
            msg = await asyncio.wait_for(_next_message(), timeout=timeout_s)
        except asyncio.TimeoutError:
            return None
        if msg is None:
            return None
        data = msg.get("data")
        if not isinstance(data, str):
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None
    finally:
        try:
            await pubsub.unsubscribe(_probe_channel(scanner_id))
        except Exception as exc:  # noqa: BLE001
            logger.debug("probe_dispatch long-poll unsubscribe noise: %s", exc)
        try:
            await pubsub.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.debug("probe_dispatch long-poll aclose noise: %s", exc)
