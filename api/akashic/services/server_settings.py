"""server_settings DB-backed runtime toggles with a tiny TTL cache.

Read path is on the hot loop for /api/scanners/discover and its
poll endpoint, so we cache values in-process for 5 seconds. PATCH
publishes a `setting.changed` event on the existing scanners
pubsub channel; api workers listen to that and bust their cache
entry, so toggling a setting on one node propagates within a few
hundred ms across the fleet (rather than waiting the full TTL).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.server_setting import ServerSetting

logger = logging.getLogger(__name__)


# The setting key strings are gathered here so callers don't sprinkle
# string literals around the codebase.
KEY_DISCOVERY_ENABLED = "discovery_enabled"


_CACHE_TTL_SECONDS = 5.0
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = asyncio.Lock()


async def get_setting(
    db: AsyncSession, key: str, default: Any = None,
) -> Any:
    """Return the value for `key` from cache or DB, falling back to
    `default`. Refreshes a stale cache entry transparently."""
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]
    row = (await db.execute(
        select(ServerSetting).where(ServerSetting.key == key)
    )).scalar_one_or_none()
    value = row.value if row is not None else default
    _cache[key] = (now + _CACHE_TTL_SECONDS, value)
    return value


def invalidate(key: str) -> None:
    """Drop a cached entry so the next read hits the DB."""
    _cache.pop(key, None)


def invalidate_all() -> None:
    _cache.clear()


async def set_setting(
    db: AsyncSession,
    key: str,
    value: Any,
    *,
    user_id: Any = None,
) -> ServerSetting:
    """Upsert `key` to `value`. Caller commits."""
    stmt = (
        pg_insert(ServerSetting)
        .values(key=key, value=value, updated_by_user_id=user_id)
        .on_conflict_do_update(
            index_elements=[ServerSetting.key],
            set_={"value": value, "updated_by_user_id": user_id},
        )
        .returning(ServerSetting)
    )
    row = (await db.execute(stmt)).scalar_one()
    invalidate(key)
    return row


async def seed_from_env_if_missing(
    db: AsyncSession, key: str, env_value: Any,
) -> None:
    """Insert `(key, env_value)` only if no row exists (stored as-is)."""
    if env_value is None:
        return
    existing = (await db.execute(
        select(ServerSetting.key).where(ServerSetting.key == key)
    )).scalar_one_or_none()
    if existing is not None:
        return
    db.add(ServerSetting(key=key, value=env_value))
    await db.commit()


async def import_legacy_env(db: AsyncSession) -> list[str]:
    """One-time import of legacy env vars into the registry.

    For every registry key whose legacy env var is set and whose DB row
    is missing, insert it (secrets encrypted). The env var is ignored
    from then on; a deprecation warning is logged whenever one is seen.
    Returns the imported keys.
    """
    import json
    from akashic.services.runtime_settings import (
        REGISTRY, coerce, encode_stored, legacy_env_value,
    )

    imported: list[str] = []
    for key, spec in REGISTRY.items():
        raw = legacy_env_value(spec)
        if raw is None:
            continue
        logger.warning(
            "env var %s is deprecated and ignored; configure %r in the web UI "
            "(Settings -> Server). Imported once if no value was stored.",
            spec.env_name, key,
        )
        try:
            val = raw
            if spec.type_name in ("list[str]", "dict[str,int]"):
                val = json.loads(raw)
            val = coerce(spec, val)
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("legacy env %s invalid (%s); skipped", spec.env_name, exc)
            continue
        # ON CONFLICT DO NOTHING: concurrent workers race on first boot.
        res = await db.execute(
            pg_insert(ServerSetting)
            .values(key=key, value=encode_stored(spec, val))
            .on_conflict_do_nothing(index_elements=["key"])
        )
        if res.rowcount:
            imported.append(key)
    await db.commit()
    return imported


# Convenience wrapper for the most-read setting.
async def is_discovery_enabled(db: AsyncSession) -> bool:
    return bool(await get_setting(db, KEY_DISCOVERY_ENABLED, default=False))


SNAPSHOT_RELOAD_INTERVAL = 30.0
_RECONNECT_BACKOFF_MAX = 30.0


async def _periodic_snapshot_reload(interval: float) -> None:
    """Safety net: pubsub is fire-and-forget, so re-read the DB regularly."""
    from akashic.services.runtime_settings import reload_snapshot
    while True:
        await asyncio.sleep(interval)
        await reload_snapshot()


async def listen_for_invalidations(
    reload_interval: float = SNAPSHOT_RELOAD_INTERVAL,
) -> None:
    """Long-running per-worker task: subscribe to scanners pubsub and bust
    the cache when a `setting.changed` event arrives. Reconnects with
    exponential backoff on any Redis error, and reloads the snapshot every
    `reload_interval` seconds so a missed event self-heals.
    """
    from akashic.services import scan_pubsub
    from akashic.services.runtime_settings import reload_snapshot

    reloader = asyncio.create_task(_periodic_snapshot_reload(reload_interval))
    backoff = 1.0
    try:
        while True:
            try:
                async for event in scan_pubsub.subscribe_scanners():
                    backoff = 1.0
                    if not isinstance(event, dict):
                        continue
                    if event.get("kind") != "setting.changed":
                        continue
                    key = event.get("key")
                    if isinstance(key, str):
                        invalidate(key)
                    else:
                        invalidate_all()
                    await reload_snapshot()
                logger.warning("server_settings subscription ended; reconnecting")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "server_settings invalidator error (%s); retry in %.0fs",
                    exc, backoff,
                )
            # Events may have been missed while disconnected.
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_BACKOFF_MAX)
            await reload_snapshot()
    finally:
        reloader.cancel()
