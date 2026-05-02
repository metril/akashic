"""Redis pub/sub fan-out for live scan events.

Producer: POST /api/scans/{id}/{heartbeat,log,stderr} handlers call
`publish()` after persisting. Consumer: WS /ws/scans/{id} subscribes via
`subscribe()` to forward events to connected browsers.

Why Redis: Phase 1 only ever runs a single API instance, but the WS handler
holds an open subscription per client. If we used a per-process in-memory
broadcaster, every horizontal scale-up would silently drop subscribers on
nodes that didn't receive the publish. Redis pub/sub gives us "any node
publishes; every node's subscribers receive it" for free, and the broker
is already in compose.

Channel scheme: `scan:{id}` per scan. WS handlers subscribe to the single
channel for the scan they're watching. The list-level WS (Phase 1.x —
`/ws/scans`) subscribes via psubscribe `scan:*` and filters server-side.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from redis.asyncio import Redis

from akashic.config import settings

logger = logging.getLogger(__name__)


def _channel(scan_id: uuid.UUID | str) -> str:
    return f"scan:{scan_id}"


# Single-channel fan-out for the list-level WebSocket. Carries scan
# state-transition events + source CRUD events; deliberately separate
# from `scan:{id}` per-scan channels so the list doesn't have to
# psubscribe and filter out per-batch heartbeats meant for the
# per-scan stream.
SOURCES_CHANNEL = "sources"

# Scanner-lifecycle events: claim redemptions, discovery requests +
# decisions, registrations, deletions. Powers the admin-only
# /ws/scanners stream that drives the SettingsScanners pending pane.
SCANNERS_CHANNEL = "scanners"


_redis: Redis | None = None


def _client() -> Redis:
    """Lazily-constructed shared Redis client. Reused across publishes —
    the asyncio Redis client is connection-pooled internally."""
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def publish(scan_id: uuid.UUID, event: dict[str, Any]) -> None:
    """Fire-and-forget publish; logs and swallows on Redis failure so we
    never break the producing endpoint over a transient broker hiccup.

    The persisted DB row is the source of truth; pub/sub is only the live
    fan-out. Reconnecting WS clients backfill via `GET /api/scans/{id}/log`.
    """
    try:
        payload = json.dumps(event, default=str)
        await _client().publish(_channel(scan_id), payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("scan_pubsub.publish failed for scan=%s: %s", scan_id, exc)


async def subscribe(scan_id: uuid.UUID) -> AsyncIterator[dict[str, Any]]:
    """Yield events for a single scan until the consumer cancels.

    Each yielded value is the dict that was passed to `publish()`. JSON
    decode errors are logged and skipped — never raise out of the
    iterator (the WS endpoint depends on it staying alive).

    Connection failures (Redis unreachable, etc.) raise to the consumer.
    The WS handler's `_forward` task wraps this iterator and sends a
    diagnostic frame to the browser before returning, so the client
    learns the live stream is unavailable rather than seeing an
    unexplained close.
    """
    pubsub = _client().pubsub()
    try:
        # Subscribe inside try so a connection failure here still triggers
        # the finally — otherwise the pubsub object leaks one connection
        # from the pool.
        await pubsub.subscribe(_channel(scan_id))
        async for message in pubsub.listen():
            if message.get("type") != "message":
                # Includes the initial "subscribe" confirmation; skip.
                continue
            data = message.get("data")
            if not isinstance(data, str):
                continue
            try:
                yield json.loads(data)
            except json.JSONDecodeError as exc:
                logger.warning("scan_pubsub bad JSON on channel=%s: %s", _channel(scan_id), exc)
                continue
    finally:
        # `unsubscribe` + `close` is required — leaving the pubsub object
        # to GC leaks a connection from the pool.
        try:
            await pubsub.unsubscribe(_channel(scan_id))
        except Exception as exc:  # noqa: BLE001
            logger.debug("scan_pubsub unsubscribe noise: %s", exc)
        try:
            await pubsub.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.debug("scan_pubsub aclose noise: %s", exc)


async def publish_source_event(event: dict[str, Any]) -> None:
    """Source CRUD + scan transition events for the list-level
    WebSocket (/ws/scans). Same fire-and-forget semantics as
    publish() — broker hiccups don't break the producing endpoint.
    """
    try:
        payload = json.dumps(event, default=str)
        await _client().publish(SOURCES_CHANNEL, payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("scan_pubsub.publish_source_event failed: %s", exc)


async def subscribe_sources() -> AsyncIterator[dict[str, Any]]:
    """Yield events from the SOURCES_CHANNEL until the consumer
    cancels. Mirrors `subscribe()`'s shape.
    """
    pubsub = _client().pubsub()
    try:
        await pubsub.subscribe(SOURCES_CHANNEL)
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            data = message.get("data")
            if not isinstance(data, str):
                continue
            try:
                yield json.loads(data)
            except json.JSONDecodeError as exc:
                logger.warning("scan_pubsub bad JSON on sources channel: %s", exc)
                continue
    finally:
        try:
            await pubsub.unsubscribe(SOURCES_CHANNEL)
        except Exception as exc:  # noqa: BLE001
            logger.debug("scan_pubsub sources unsubscribe noise: %s", exc)
        try:
            await pubsub.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.debug("scan_pubsub sources aclose noise: %s", exc)


async def publish_scanner_event(event: dict[str, Any]) -> None:
    """Scanner-lifecycle events for the admin-only /ws/scanners stream.

    Same fire-and-forget contract as `publish()` — broker hiccups log
    a warning but never break the producing endpoint. The persisted
    DB row remains the source of truth; pubsub is only the live
    fan-out.
    """
    try:
        payload = json.dumps(event, default=str)
        await _client().publish(SCANNERS_CHANNEL, payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("scan_pubsub.publish_scanner_event failed: %s", exc)


async def subscribe_scanners() -> AsyncIterator[dict[str, Any]]:
    """Yield events from SCANNERS_CHANNEL until the consumer cancels.
    Mirrors `subscribe_sources()`'s shape."""
    pubsub = _client().pubsub()
    try:
        await pubsub.subscribe(SCANNERS_CHANNEL)
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            data = message.get("data")
            if not isinstance(data, str):
                continue
            try:
                yield json.loads(data)
            except json.JSONDecodeError as exc:
                logger.warning("scan_pubsub bad JSON on scanners channel: %s", exc)
                continue
    finally:
        try:
            await pubsub.unsubscribe(SCANNERS_CHANNEL)
        except Exception as exc:  # noqa: BLE001
            logger.debug("scan_pubsub scanners unsubscribe noise: %s", exc)
        try:
            await pubsub.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.debug("scan_pubsub scanners aclose noise: %s", exc)


async def aclose() -> None:
    """Close the shared client on app shutdown."""
    global _redis
    if _redis is not None:
        try:
            await _redis.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.debug("scan_pubsub aclose: %s", exc)
        _redis = None
