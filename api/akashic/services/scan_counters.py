"""Redis-backed per-scan counters (v0.29.2).

Pre-v0.29.2 every ingest batch held a brief row-level lock on the
``scans`` row while it did ``scan.files_found += N``,
``scan.files_new += M``, etc., then committed. Two batches from
different scanners on the same scan serialized at that lock — a short
window per batch but real, and it grew linearly with batch count × scanner
count. With AIMD batch sizes climbing to thousands of entries and
multi-scanner cooperation finally working in v0.29.0, the lock became
the dominant per-batch serialization point.

Switch the hot path to Redis ``HINCRBY``:

  * **Ingest** calls ``add(scan_id, files_found=N, files_new=M, ...)``;
    one Redis round-trip per batch with no row lock and no transaction
    contention.
  * **Live readers** (heartbeat broadcast, GET /api/scans/{id}) read
    via ``read(scan_id)`` which falls back to the scan row's columns
    when Redis is unreachable so the system degrades to v0.29.1
    semantics instead of going blank.
  * **Terminal transition** (complete_scan, _maybe_finalize_scan,
    stale-scan watchdog) calls ``flush_to_db(db, scan)`` which UPSERTs
    the row's columns from the Redis hash, then deletes the hash.

Hash schema: ``akashic:scan:{id}:counters`` with integer fields
``files_found``, ``files_new``, ``files_changed``, ``files_deleted``,
``files_skipped``, ``inaccessible_dirs``, ``inaccessible_files``,
``bytes_scanned``. TTL is set on first write (7 days) so a crash mid-
scan doesn't leak the hash forever — if the stale-scan watchdog
forgets, the hash expires on its own.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.scan import Scan
from akashic.services.scan_pubsub import _client as _redis_client

logger = logging.getLogger(__name__)


# Fields permitted on the hash. Anything outside this set is rejected
# to keep typos from silently growing a phantom counter that nothing
# flushes to the DB.
_PERMITTED_FIELDS = frozenset({
    "files_found", "files_new", "files_changed", "files_deleted",
    "files_skipped", "inaccessible_dirs", "inaccessible_files",
    "bytes_scanned",
})

# 7 days: long enough that a hard-crash watchdog recovery
# (stale_scan_threshold_minutes default 60) has plenty of slack, short
# enough that an abandoned hash doesn't accumulate forever.
_HASH_TTL_SECONDS = 7 * 24 * 3600


def _key(scan_id: uuid.UUID | str) -> str:
    return f"akashic:scan:{scan_id}:counters"


async def add(scan_id: uuid.UUID | str, /, **deltas: int) -> None:
    """HINCRBY non-zero fields on the per-scan counter hash.

    Hot path — one Redis round-trip per call regardless of how many
    fields move. Zero-valued fields are skipped so we don't pollute
    the hash with no-op writes. Silently no-ops on Redis failure
    (logs at DEBUG) — the per-batch path must keep flowing even when
    Redis blips; the final flush_to_db on terminal will catch up.
    """
    payload = {k: int(v) for k, v in deltas.items() if v}
    if not payload:
        return
    bad = set(payload) - _PERMITTED_FIELDS
    if bad:
        raise ValueError(f"scan_counters.add: unknown fields {bad}")
    try:
        redis = _redis_client()
        key = _key(scan_id)
        pipe = redis.pipeline()
        for field, delta in payload.items():
            pipe.hincrby(key, field, delta)
        # Always re-arm the TTL — a long-running scan should keep its
        # hash alive even past the default 7-day window if batches
        # keep arriving. EXPIRE is cheap on an existing key.
        pipe.expire(key, _HASH_TTL_SECONDS)
        await pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "scan_counters.add HINCRBY failed for scan=%s: %s",
            scan_id, exc,
        )


async def read(scan_id: uuid.UUID | str) -> dict[str, int]:
    """Read the current counter hash. Returns ``{}`` when the hash
    doesn't exist (no batches yet, or already flushed) or Redis is
    unreachable — callers should fall through to the scan row's
    columns.
    """
    try:
        redis = _redis_client()
        raw = await redis.hgetall(_key(scan_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "scan_counters.read HGETALL failed for scan=%s: %s",
            scan_id, exc,
        )
        return {}
    out: dict[str, int] = {}
    for field, value in raw.items():
        try:
            out[field] = int(value)
        except (TypeError, ValueError):
            continue
    return out


async def overlay(scan: Scan) -> dict[str, int]:
    """Return the live counter view: row column PLUS Redis pending
    deltas for each field. Used by read paths that need a consistent
    "this is what the user sees" snapshot regardless of whether
    we're mid-scan or post-flush.

    Sums column + hash because the hash represents pending deltas
    (see ``flush_to_db``); a flushed scan has an empty hash so the
    overlay reduces to the row's value. A mid-scan call sees
    ``row_value + redis_delta`` — equivalent to what ``scan.x``
    would have read pre-v0.29.2.
    """
    live = await read(scan.id)
    out: dict[str, int] = {}
    for field in _PERMITTED_FIELDS:
        existing = int(getattr(scan, field, 0) or 0)
        out[field] = existing + int(live.get(field, 0))
    return out


async def flush_to_db(db: AsyncSession, scan: Scan) -> bool:
    """Apply the Redis hash values as deltas onto ``scan.*`` columns,
    then delete the hash. Idempotent on empty hashes.

    The hash represents counters PENDING flush — values accumulated
    via HINCRBY since the last flush. flush adds them to the row's
    existing values so cross-batch accumulation works the same as
    pre-v0.29.2's ``scan.x += N`` did, and a watchdog flush followed
    by a real terminal flush sums correctly (the watchdog drained one
    window of deltas; the real flush drains whatever was added after).

    Read paths (``overlay``) sum row + hash for the same reason.

    Called from terminal-status transitions (``complete_scan``,
    ``_maybe_finalize_scan``, watchdog) and from the per-final-batch
    path inside ingest. The caller commits — flush_to_db only mutates
    the in-session Scan instance.
    """
    live = await read(scan.id)
    if not live:
        return False
    for field in _PERMITTED_FIELDS:
        delta = live.get(field, 0)
        if not delta:
            continue
        existing = int(getattr(scan, field, 0) or 0)
        setattr(scan, field, existing + delta)
    try:
        redis = _redis_client()
        await redis.delete(_key(scan.id))
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "scan_counters.flush_to_db DEL failed for scan=%s: %s",
            scan.id, exc,
        )
    return True
