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

from akashic.config import settings
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
    """Insert `(key, env_value)` only if no row exists. First-boot
    bootstrap hook for IaC users who'd rather configure via env vars.
    Subsequent runtime PATCHes from the UI take precedence."""
    if env_value is None:
        return
    existing = (await db.execute(
        select(ServerSetting.key).where(ServerSetting.key == key)
    )).scalar_one_or_none()
    if existing is not None:
        return
    db.add(ServerSetting(key=key, value=env_value))
    await db.commit()


# Convenience wrapper for the most-read setting.
async def is_discovery_enabled(db: AsyncSession) -> bool:
    return bool(await get_setting(db, KEY_DISCOVERY_ENABLED, default=False))


async def listen_for_invalidations() -> None:
    """Long-running task: subscribe to scanners pubsub and bust the
    cache when a `setting.changed` event arrives. Started from the
    api lifespan so cache stays consistent across worker processes.
    """
    from akashic.services import scan_pubsub
    try:
        async for event in scan_pubsub.subscribe_scanners():
            if not isinstance(event, dict):
                continue
            if event.get("kind") != "setting.changed":
                continue
            key = event.get("key")
            if isinstance(key, str):
                invalidate(key)
            else:
                invalidate_all()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("server_settings cache invalidator stopped: %s", exc)
