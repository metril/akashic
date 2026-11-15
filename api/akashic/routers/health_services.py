"""Service health + activity surface (v0.29.0).

Pre-v0.29.0 the only liveness signal was the bare `/health` TCP bind
check — sufficient for compose dependency ordering but useless for
operators wondering "is Tika actually processing anything?" or
"why isn't search updating?".

Two endpoints, both admin-auth, 5-second in-process cache:

- `/api/v1/health/services` — liveness only. Returns per-service ``{ok,
  latency_ms, error}`` for Postgres + Redis + Meilisearch + Tika.
  Drives the admin sidebar chip.

- `/api/v1/health/services/activity` — rich activity. Tika: RQ queue
  depth + per-minute throughput counters + last-extraction timestamp,
  pulled from Redis keys the extraction worker bumps. Meilisearch:
  document count + pending task count + last task status, pulled from
  Meili's own ``/indexes/files/stats`` and ``/tasks`` endpoints.

Liveness probes have a 2 s timeout each so one slow downstream can't
stall the dashboard. Activity reads degrade gracefully — a partially
unreachable backend returns ``ok: false`` rather than 500.

The existing public ``/health`` endpoint is unchanged (TCP-bind only,
no auth, no downstream calls).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from akashic.routing import ApiRouter
from fastapi import Depends
from akashic.schemas.base import ApiModel
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import Principal, require_scope
from akashic.config import settings
from akashic.services.runtime_settings import rt
from akashic.database import get_db
from akashic.models.user import User

logger = logging.getLogger(__name__)

router = ApiRouter(prefix="/health/services", tags=["health"])

# In-process cache. Per-key (liveness vs activity) so the cheaper
# liveness poll doesn't fall behind the activity poll's TTL.
class ServiceProbe(ApiModel):
    ok: bool = Field(description="Whether the service answered within 2 seconds.")
    latency_ms: float | None = Field(description="Round-trip time in milliseconds; null when the probe failed.")
    error: str | None = Field(default=None, description="Why the probe failed; absent when it succeeded.")


class ServicesHealth(ApiModel):
    postgres: ServiceProbe = Field(description="Postgres probe.")
    redis: ServiceProbe = Field(description="Redis probe.")
    meilisearch: ServiceProbe = Field(description="Meilisearch probe.")
    tika: ServiceProbe = Field(description="Tika probe.")
    checked_at: datetime = Field(description="When the probes ran (cached for 5 seconds).")

    model_config = {"json_schema_extra": {'examples': [{'postgres': {'ok': True, 'latency_ms': 1.2}, 'redis': {'ok': True, 'latency_ms': 0.8}, 'meilisearch': {'ok': True, 'latency_ms': 4.1}, 'tika': {'ok': True, 'latency_ms': 12.5}, 'checked_at': '2026-04-27T22:00:00Z'}]}}


class TikaActivity(ApiModel):
    extracted_total: int | None = Field(description="Lifetime count of files whose text was extracted; null if unreadable.")
    extracted_last_5min: int | None = Field(description="Files extracted in the last 5 minutes; null if unreadable.")
    last_extracted_at: str | None = Field(description="ISO-8601 time the latest extraction was reported, as stored.")
    ok: bool = Field(description="False when Redis could not be reached.")
    error: str | None = Field(default=None, description="Why the counters could not be read.")


class MeiliActivity(ApiModel):
    documents_in_index: int | None = Field(description="Documents in the file index; null if unreadable.")
    pending_tasks: int | None = Field(description="Enqueued or processing index tasks; null if unreadable.")
    last_task_at: str | None = Field(description="Time the latest index task finished or was enqueued, as reported by Meilisearch.")
    last_task_status: str | None = Field(description="Status of the latest index task, e.g. `succeeded`.")
    ok: bool = Field(description="False when Meilisearch could not be reached.")
    error: str | None = Field(default=None, description="Why Meilisearch could not be read.")


class ServicesActivity(ApiModel):
    tika: TikaActivity = Field(description="Text extraction throughput.")
    meilisearch: MeiliActivity = Field(description="Search index activity.")
    checked_at: datetime = Field(description="When the data was gathered (cached for 5 seconds).")

    model_config = {"json_schema_extra": {'examples': [{'tika': {'extracted_total': 5300, 'extracted_last_5min': 42, 'last_extracted_at': '2026-04-27T22:00:00Z', 'ok': True}, 'meilisearch': {'documents_in_index': 120000, 'pending_tasks': 0, 'last_task_at': '2026-04-27T21:59:58Z', 'last_task_status': 'succeeded', 'ok': True}, 'checked_at': '2026-04-27T22:00:00Z'}]}}


_CACHE_TTL_SECONDS = 5.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _cached(key: str) -> Optional[dict[str, Any]]:
    entry = _cache.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if time.monotonic() >= expires_at:
        return None
    return value


def _store(key: str, value: dict[str, Any]) -> None:
    _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, value)


async def _probe_postgres(db: AsyncSession) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=2.0)
        return {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "latency_ms": None, "error": str(exc)[:200]}


async def _probe_redis() -> dict[str, Any]:
    from akashic.services.scan_pubsub import _client as _redis_client
    started = time.perf_counter()
    try:
        redis = _redis_client()
        await asyncio.wait_for(redis.ping(), timeout=2.0)
        return {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "latency_ms": None, "error": str(exc)[:200]}


async def _probe_meilisearch() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(
                f"{settings.meili_url}/health",
                headers={"Authorization": f"Bearer {settings.meili_key}"},
            )
        if r.status_code != 200:
            return {
                "ok": False, "latency_ms": None,
                "error": f"HTTP {r.status_code}",
            }
        return {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "latency_ms": None, "error": str(exc)[:200]}


async def _probe_tika() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            # Tika's root `/tika` returns "This is Tika Server (Apache
            # Tika x.y.z). Please PUT" on GET — cheap liveness check
            # without needing to upload anything.
            r = await client.get(f"{rt().tika_url}/tika")
        if r.status_code != 200:
            return {
                "ok": False, "latency_ms": None,
                "error": f"HTTP {r.status_code}",
            }
        return {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "latency_ms": None, "error": str(exc)[:200]}


@router.get(
    "",
    response_model=ServicesHealth,
    summary="Check service health",
    description=(
        "Liveness of Postgres, Redis, Meilisearch and Tika, each probed with a 2 second timeout, "
        "with `ok`, `latency_ms` and `error` per service and a `checked_at` timestamp. The result "
        "is cached for 5 seconds.\n"
        "Requires scope `health:read`."
    ),
)
async def services_health(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("health:read"),
) -> dict[str, Any]:
    """Per-service liveness, 5-s cached. Admin-only.

    Each probe has its own 2 s timeout so the response itself is
    bounded at ~2 s even when several backends are down. Per-service
    `{ok, latency_ms, error}` so the frontend chip can colour-code
    by whichever service flipped first.
    """
    cached = _cached("liveness")
    if cached is not None:
        return cached

    postgres, redis, meili, tika = await asyncio.gather(
        _probe_postgres(db),
        _probe_redis(),
        _probe_meilisearch(),
        _probe_tika(),
    )
    payload = {
        "postgres": postgres,
        "redis": redis,
        "meilisearch": meili,
        "tika": tika,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    _store("liveness", payload)
    return payload


async def _tika_activity() -> dict[str, Any]:
    """Text-extraction throughput from Redis.

    v0.30.0 — extraction moved out of the standalone RQ worker into the
    scanner, so there is no `rq:queue:extraction` job list or
    `rq:failed` registry to read. Throughput is now derived purely from
    the activity counters the `/api/v1/ingest/content` endpoint bumps:

      * `akashic:tika:extracted_total` — lifetime files-extracted count
      * `akashic:tika:last_extracted_at` — ISO timestamp
      * `akashic:tika:extracted:YYYYMMDDHHMM` × last 5 minutes — sum
        for "extracted in last 5 min" throughput.

    Best-effort: any single read failure surfaces as a null value
    rather than crashing the whole payload.
    """
    from akashic.services.scan_pubsub import _client as _redis_client
    out: dict[str, Any] = {
        "extracted_total": None,
        "extracted_last_5min": None,
        "last_extracted_at": None,
        "ok": True,
    }
    try:
        redis = _redis_client()
    except Exception as exc:  # noqa: BLE001
        out["ok"] = False
        out["error"] = f"redis client init failed: {exc}"
        return out

    try:
        total = await redis.get("akashic:tika:extracted_total")
        out["extracted_total"] = int(total) if total else 0
    except Exception as exc:  # noqa: BLE001
        logger.debug("tika activity: total read failed: %s", exc)
    try:
        last_at = await redis.get("akashic:tika:last_extracted_at")
        out["last_extracted_at"] = last_at if last_at else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("tika activity: last_at read failed: %s", exc)

    # Per-minute bucket sum for the last 5 minutes. Bucket key shape
    # mirrors what routers/ingest.py:_bump_tika_counters writes.
    try:
        now = datetime.now(timezone.utc)
        keys = []
        for offset_min in range(5):
            ts = now.timestamp() - (offset_min * 60)
            bucket = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d%H%M")
            keys.append(f"akashic:tika:extracted:{bucket}")
        if keys:
            values = await redis.mget(*keys)
            out["extracted_last_5min"] = sum(int(v) for v in values if v)
    except Exception as exc:  # noqa: BLE001
        logger.debug("tika activity: 5-min sum failed: %s", exc)

    return out


async def _meili_activity() -> dict[str, Any]:
    """Meilisearch document count + pending-task count + last-task
    state, pulled directly from Meili's own admin endpoints.

    Best-effort: a Meili-side outage returns `ok=false` with the error
    surfaced; the rest of the activity payload still renders so the
    operator can see Tika is working even if Meili is down.
    """
    out: dict[str, Any] = {
        "documents_in_index": None,
        "pending_tasks": None,
        "last_task_at": None,
        "last_task_status": None,
        "ok": True,
    }
    headers = {"Authorization": f"Bearer {settings.meili_key}"}
    try:
        async with httpx.AsyncClient(timeout=2.0, headers=headers) as client:
            stats_resp, pending_resp, last_resp = await asyncio.gather(
                client.get(f"{settings.meili_url}/indexes/files/stats"),
                client.get(
                    f"{settings.meili_url}/tasks",
                    params={"statuses": "enqueued,processing", "limit": 1},
                ),
                client.get(f"{settings.meili_url}/tasks", params={"limit": 1}),
                return_exceptions=True,
            )
    except Exception as exc:  # noqa: BLE001
        out["ok"] = False
        out["error"] = str(exc)[:200]
        return out

    if isinstance(stats_resp, Exception):
        out["ok"] = False
        out["error"] = f"stats: {stats_resp}"[:200]
    elif stats_resp.status_code == 200:
        try:
            out["documents_in_index"] = int(stats_resp.json().get("numberOfDocuments", 0))
        except Exception:  # noqa: BLE001
            pass

    if not isinstance(pending_resp, Exception) and pending_resp.status_code == 200:
        try:
            body = pending_resp.json()
            # Meili returns either {"total": int, ...} (preferred) or
            # {"results": [...]} depending on version. Treat either as
            # "at least N pending"; total is the canonical count.
            total = body.get("total")
            if total is None:
                total = len(body.get("results") or [])
            out["pending_tasks"] = int(total)
        except Exception:  # noqa: BLE001
            pass

    if not isinstance(last_resp, Exception) and last_resp.status_code == 200:
        try:
            body = last_resp.json()
            results = body.get("results") or []
            if results:
                last = results[0]
                out["last_task_at"] = last.get("finishedAt") or last.get("enqueuedAt")
                out["last_task_status"] = last.get("status")
        except Exception:  # noqa: BLE001
            pass

    return out


@router.get(
    "/activity",
    response_model=ServicesActivity,
    summary="Show service activity",
    description=(
        "Tika extraction throughput (queue depth, per-minute counters, last extraction) and "
        "Meilisearch activity (document count, pending tasks, last task status). An unreachable "
        "backend reports `ok: false` rather than failing the request. Cached for 5 seconds.\n"
        "Requires scope `health:read`."
    ),
)
async def services_activity(
    principal: Principal = require_scope("health:read"),
) -> dict[str, Any]:
    """Tika extraction throughput + Meilisearch activity. 5-second
    cached, admin-only.

    Driving the "Admin → System Status" page so operators can answer
    "is anything actually happening" without grepping logs.
    """
    cached = _cached("activity")
    if cached is not None:
        return cached

    tika, meili = await asyncio.gather(_tika_activity(), _meili_activity())
    payload = {
        "tika": tika,
        "meilisearch": meili,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    _store("activity", payload)
    return payload
