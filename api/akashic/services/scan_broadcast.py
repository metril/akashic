"""Per-scan last-broadcast snapshot for change-detection broadcasts.

The v0.4.7 heartbeat-driven broadcast was effectively a 1 Hz polling
channel: every heartbeat fired a scan.state event regardless of
whether the user-visible state had changed. v0.4.11 replaces that
with strict event-driven fan-out — heartbeats persist silently,
broadcasts fire only when a meaningful field actually moved since
the last broadcast.

The "last broadcast" snapshot lives in Redis (TTL'd) rather than the
scans table because:
- It's transient — only meaningful while a scan is in flight.
- Auto-expires; no cleanup task needed.
- Doesn't add a hot-write column to the scans table on every
  heartbeat (which fires 1 Hz per active scan).

Thresholds are ADAPTIVE: ~1% of the estimated total, clamped to a
[50, 5000] range. A fixed delta is too sparse for a 10k-file scan
and too chatty for a 100M-file scan; the adaptive formula gives
~100 broadcasts per scan regardless of size, which translates to
a smooth-feeling counter at any scale.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.config import settings

logger = logging.getLogger(__name__)


def _key(scan_id: str) -> str:
    return f"scanbcast:{scan_id}"


_TTL = timedelta(hours=24)
DELTA_FLOOR = 50
DELTA_CEILING = 5000

_redis: Redis | None = None


def _client() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url, decode_responses=True, retry_on_timeout=True, health_check_interval=30)
    return _redis


def adaptive_threshold(total_estimated: int | None) -> int:
    """Adaptive delta: ~1% of estimated total, clamped to [floor, ceiling].

    Why 1%: gives ~100 broadcasts per scan regardless of size. At 60-fps
    perception, every 1% of a multi-minute scan looks "live" without
    being noisy.

    Why the floor (50): even on a never-counted scan (total_estimated=0
    at scan start), broadcast every 50 files so the user sees the
    counter start moving immediately.

    Why the ceiling (5000): on enormous scans (100M+ files), 1% would
    mean a million files between broadcasts — too sparse to feel live.
    Cap at 5000 so even a 10k-files-per-second scan still broadcasts
    every ~0.5s.
    """
    base = (total_estimated or 0) // 100
    return max(DELTA_FLOOR, min(DELTA_CEILING, base))


async def should_broadcast(
    scan_id: str,
    *,
    phase: str | None,
    status: str,
    files_found: int,
    total_estimated: int | None,
) -> bool:
    """Return True if this scan's state has changed enough since the
    last broadcast to warrant another one.

    Returns True on:
      - first call for this scan (no snapshot yet)
      - phase or status flip (always meaningful, regardless of delta)
      - files_found delta >= adaptive threshold
      - total_estimated delta >= adaptive threshold (covers prewalk
        progress where files_found stays at 0)

    Best-effort against Redis: on any error, assume True so the user
    still sees updates (better to over-broadcast than to silently lose
    state).
    """
    try:
        raw = await _client().get(_key(scan_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("scan_broadcast.should_broadcast redis read failed: %s", exc)
        return True

    if not raw:
        return True

    try:
        last = json.loads(raw)
    except json.JSONDecodeError:
        return True

    if last.get("phase") != phase:
        return True
    if last.get("status") != status:
        return True

    last_found = int(last.get("files_found") or 0)
    threshold = adaptive_threshold(total_estimated)
    if files_found - last_found >= threshold:
        return True

    last_est = int(last.get("total_estimated") or 0)
    cur_est = int(total_estimated or 0)
    # Use last_est for the threshold during prewalk so a still-ramping
    # estimate doesn't lower the bar for itself.
    est_threshold = max(DELTA_FLOOR, min(DELTA_CEILING, last_est // 100))
    if cur_est - last_est >= est_threshold:
        return True

    return False


async def record_broadcast(
    scan_id: str,
    *,
    phase: str | None,
    status: str,
    files_found: int,
    total_estimated: int | None,
) -> None:
    """Commit the current state as the new "last broadcast" snapshot.

    Caller must invoke this AFTER a successful publish_source_event so
    subsequent should_broadcast() calls compare against the just-emitted
    state. Best-effort against Redis: on error, log and continue —
    worst case we over-broadcast on the next heartbeat.
    """
    payload: dict[str, Any] = {
        "phase": phase,
        "status": status,
        "files_found": files_found,
        "total_estimated": total_estimated,
    }
    try:
        await _client().set(
            _key(scan_id),
            json.dumps(payload),
            ex=int(_TTL.total_seconds()),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("scan_broadcast.record_broadcast redis write failed: %s", exc)


async def resolve_scanner_name(
    db: AsyncSession, scanner_id: uuid.UUID | None,
) -> str | None:
    """Look up a scanner's human-readable name for a scan.state
    broadcast payload. Returns None when no scanner is assigned
    (pending scans, or a scan no scanner has leased).

    v0.30.1 — the live heartbeat / batch-ingest / cancel broadcasts
    previously hardcoded ``scanner_name: None`` here, which blanked the
    scanner name in the UI the moment the first live event arrived
    after the (correctly-populated) WS snapshot frame.
    """
    if scanner_id is None:
        return None
    from akashic.models.scanner import Scanner
    res = await db.execute(
        select(Scanner.name).where(Scanner.id == scanner_id)
    )
    return res.scalar_one_or_none()


async def clear_broadcast(scan_id: str) -> None:
    """Remove the snapshot. Called when a scan terminates so a future
    re-trigger of the same scan_id (rare; mostly for tests) starts
    fresh. In production, snapshots TTL-expire after 24h."""
    try:
        await _client().delete(_key(scan_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug("scan_broadcast.clear_broadcast redis delete noise: %s", exc)
