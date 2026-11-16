"""Directory sync: fetch AD / Authentik, upsert the cache, lock via run rows.

`directory_sync_runs` doubles as the lock: a partial unique index allows one
`running` row per backend, so a racing manual/scheduled start fails with
`IntegrityError` -> `SyncAlreadyRunning`. A failed sync never touches the
cache; a successful one replaces that backend's rows.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.database import async_session
from akashic.models.directory import DirectoryEntry, DirectorySyncRun
from akashic.services.directory.ad import ad_configured, fetch_ad_person, fetch_all_ad
from akashic.services.directory.authentik import (
    authentik_configured, fetch_all_authentik, fetch_authentik_person,
)
from akashic.services.directory.records import DirectoryError, DirRecord, NotConfigured
from akashic.services.runtime_settings import rt

logger = logging.getLogger(__name__)

AD_SYNC_TIMEOUT = 10.0
AUTHENTIK_SYNC_TIMEOUT = 30.0
PERSON_REFRESH_TIMEOUT = 3.0
_CHUNK = 1000

# Strong references so in-flight sync tasks are not garbage-collected.
_tasks: set[asyncio.Task] = set()
# One stop event per run: shutdown sets every live run's event so its AD fetch
# thread stops, and a later start gets a fresh event that can never re-arm an
# old run's.
_stops: dict[uuid.UUID, threading.Event] = {}
_LOCK_CONSTRAINT = "uq_directory_sync_runs_backend_running"


class SyncAlreadyRunning(Exception):
    pass


class NoBackendConfigured(Exception):
    pass


class RefreshFailed(DirectoryError):
    """Every attempted live read failed; the message is already redacted."""


def _configured_backends() -> list[str]:
    out = []
    if ad_configured():
        out.append("ad")
    if authentik_configured():
        out.append("authentik")
    return out


def _is_lock_conflict(exc: IntegrityError) -> bool:
    cause = getattr(exc.orig, "__cause__", None)
    name = getattr(cause, "constraint_name", None)
    if name is not None:
        return name == _LOCK_CONSTRAINT
    return _LOCK_CONSTRAINT in str(exc.orig)  # driver without constraint_name


async def start_sync(
    *, trigger: Literal["scheduled", "manual"], triggered_by: uuid.UUID | None,
) -> list[DirectorySyncRun]:
    backends = _configured_backends()
    if not backends:
        raise NoBackendConfigured()
    async with async_session() as db:
        runs = [
            DirectorySyncRun(
                backend=b, status="running", trigger=trigger, triggered_by=triggered_by,
            )
            for b in backends
        ]
        db.add_all(runs)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            # Only the running-lock index is a conflict; anything else
            # (e.g. a triggered_by FK violation) is a real error.
            if not _is_lock_conflict(exc):
                raise
            raise SyncAlreadyRunning() from None
        for r in runs:
            await db.refresh(r)
    for r in runs:
        stop = _stops[r.id] = threading.Event()
        task = asyncio.create_task(run_backend(r.id, r.backend, stop))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        task.add_done_callback(lambda _t, rid=r.id: _stops.pop(rid, None))
    return runs


async def shutdown_directory_sync() -> None:
    """Cancel in-flight sync tasks; startup reconcile recovers their run rows."""
    for ev in list(_stops.values()):
        ev.set()
    tasks = list(_tasks)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _values(rec: DirRecord, synced_at) -> dict:
    return {
        "backend": rec.backend,
        "ref": rec.ref,
        "kind": rec.kind,
        "display_name": rec.display_name,
        "username": rec.username,
        "email": rec.email,
        "sid": rec.sid,
        "dn": rec.dn,
        "group_sids": list(rec.group_sids),
        "groups_status": rec.groups_status,
        "groups_synced_at": synced_at if rec.groups_status == "ok" else None,
        "uid_number": rec.uid_number,
        "gid_number": rec.gid_number,
        "synced_at": synced_at,
    }


async def _upsert(db: AsyncSession, records: list[DirRecord], synced_at) -> None:
    """Upsert on (backend, ref). A failed group read keeps the stored groups.

    Rows already newer than ``synced_at`` (e.g. refreshed mid-sync) are left alone.
    """
    by_key = {(r.backend, r.ref): r for r in records}
    rows = [_values(r, synced_at) for r in by_key.values()]
    for i in range(0, len(rows), _CHUNK):
        stmt = pg_insert(DirectoryEntry).values(rows[i:i + _CHUNK])
        ex = stmt.excluded
        keep = ex.groups_status == "failed"
        stmt = stmt.on_conflict_do_update(
            constraint="uq_directory_entries_backend_ref",
            where=DirectoryEntry.synced_at <= ex.synced_at,
            set_={
                "kind": ex.kind,
                "display_name": ex.display_name,
                "username": ex.username,
                "email": ex.email,
                "sid": ex.sid,
                "dn": ex.dn,
                "uid_number": ex.uid_number,
                "gid_number": ex.gid_number,
                "synced_at": ex.synced_at,
                "groups_status": ex.groups_status,
                "group_sids": case((keep, DirectoryEntry.group_sids), else_=ex.group_sids),
                "groups_synced_at": case(
                    (keep, DirectoryEntry.groups_synced_at), else_=ex.groups_synced_at,
                ),
            },
        )
        await db.execute(stmt)


async def _fetch(backend: str, stop: threading.Event) -> list[DirRecord]:
    if backend == "ad":
        return await asyncio.to_thread(fetch_all_ad, AD_SYNC_TIMEOUT, stop)
    return await fetch_all_authentik(AUTHENTIK_SYNC_TIMEOUT)


async def _fail_run(run_id: uuid.UUID, exc: Exception) -> None:
    msg = str(exc) if isinstance(exc, DirectoryError) else type(exc).__name__
    async with async_session() as db:
        await db.execute(
            update(DirectorySyncRun)
            .where(DirectorySyncRun.id == run_id, DirectorySyncRun.status == "running")
            .values(status="failed", error=msg, finished_at=func.now())
        )
        await db.commit()


async def run_backend(
    run_id: uuid.UUID, backend: str, stop: threading.Event | None = None,
) -> None:
    stop = stop if stop is not None else threading.Event()
    try:
        async with async_session() as db:
            started_at = (await db.execute(
                select(DirectorySyncRun.started_at).where(DirectorySyncRun.id == run_id)
            )).scalar_one()
        records = await _fetch(backend, stop)
        if stop.is_set():
            # Shut down mid-read: write nothing; startup reconcile recovers the row.
            logger.info("directory sync (%s) stopped; result discarded", backend)
            return
        async with async_session() as db:
            await _upsert(db, records, started_at)
            removed = (await db.execute(
                delete(DirectoryEntry).where(
                    DirectoryEntry.backend == backend,
                    DirectoryEntry.synced_at < started_at,
                )
            )).rowcount or 0
            counts = {
                "users": sum(1 for r in records if r.kind == "person"),
                "groups": sum(1 for r in records if r.kind == "group"),
                "removed": removed,
                "groups_failed": sum(1 for r in records if r.groups_status == "failed"),
            }
            marked = await db.execute(
                update(DirectorySyncRun)
                .where(DirectorySyncRun.id == run_id, DirectorySyncRun.status == "running")
                .values(status="succeeded", counts=counts, error=None, finished_at=func.now())
            )
            if not marked.rowcount:
                # The run was reconciled/failed meanwhile: leave the cache untouched.
                await db.rollback()
                logger.warning("directory sync (%s) run no longer running; cache untouched", backend)
                return
            await db.commit()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("directory sync (%s) failed: %s", backend, type(exc).__name__)
        if stop.is_set():
            return  # stopped run: leave the row for startup reconcile
        try:
            await _fail_run(run_id, exc)
        except Exception as inner:  # noqa: BLE001
            logger.warning("could not record directory sync failure: %s", type(inner).__name__)


async def reconcile_directory_runs() -> int:
    """Flip runs left `running` by a previous API process to `failed`."""
    async with async_session() as db:
        result = await db.execute(
            update(DirectorySyncRun)
            .where(DirectorySyncRun.status == "running")
            .values(status="failed", error="interrupted by API restart", finished_at=func.now())
        )
        await db.commit()
        count = result.rowcount or 0
    if count:
        logger.info("reconciled %d orphaned directory sync run(s) to failed", count)
    return count


async def sync_due(db: AsyncSession, now: datetime) -> bool:
    interval = rt().directory_sync_interval_minutes
    if interval <= 0:
        return False
    running = (await db.execute(
        select(DirectorySyncRun.id).where(DirectorySyncRun.status == "running").limit(1)
    )).scalar_one_or_none()
    if running is not None:
        return False
    # started_at, not finished_at: a run reconciled after a restart is stamped
    # finished now and must not postpone the next sync by a full interval.
    last = (await db.execute(select(func.max(DirectorySyncRun.started_at)))).scalar_one()
    return last is None or now - last >= timedelta(minutes=interval)


def _to_record(e: DirectoryEntry) -> DirRecord:
    return DirRecord(
        backend=e.backend, ref=e.ref, kind=e.kind, display_name=e.display_name,
        username=e.username, email=e.email, sid=e.sid, dn=e.dn,
        group_sids=tuple(e.group_sids or ()), groups_status=e.groups_status,
        uid_number=e.uid_number, gid_number=e.gid_number,
    )


_KEEP = object()  # record not applicable to a live read (group, akashic)


async def _read_live(rec: DirRecord) -> DirRecord | None | DirectoryError | object:
    """Live re-read of one record: record, ``None`` (not found), a
    ``DirectoryError`` (read failed) or ``_KEEP`` (not applicable)."""
    if rec.kind != "person" or rec.backend not in ("ad", "authentik"):
        return _KEEP
    try:
        if rec.backend == "ad":
            if not ad_configured():
                return DirectoryError("AD is not configured")
            return await asyncio.to_thread(
                lambda: fetch_ad_person(
                    sid=rec.sid, dn=rec.dn, timeout=PERSON_REFRESH_TIMEOUT)
            )
        if not authentik_configured():
            return DirectoryError("Authentik is not configured")
        return await fetch_authentik_person(rec.ref, PERSON_REFRESH_TIMEOUT)
    except DirectoryError as exc:
        return exc
    except NotConfigured as exc:
        return DirectoryError(str(exc))


async def refresh_records(db: AsyncSession, records: list[DirRecord]) -> list[DirRecord]:
    """Re-read AD/Authentik *people* live (groups are kept as cached); upsert
    found, delete not-found. Commits the caller's session.

    Records whose read failed, and non-applicable records, are returned
    unchanged. If every attempted read failed, raises ``RefreshFailed`` before
    writing anything.
    """
    lives = await asyncio.gather(*(_read_live(r) for r in records))
    attempted = [l for l in lives if l is not _KEEP]
    if attempted and all(isinstance(l, DirectoryError) for l in attempted):
        raise RefreshFailed("; ".join(dict.fromkeys(str(l) for l in attempted)))
    now = func.clock_timestamp()
    fresh = [l for l in lives if isinstance(l, DirRecord)]
    gone = [r for r, l in zip(records, lives) if l is None]
    if fresh:
        await _upsert(db, fresh, now)
    for r in gone:
        await db.execute(delete(DirectoryEntry).where(
            DirectoryEntry.backend == r.backend, DirectoryEntry.ref == r.ref))
    await db.commit()

    stored: dict[tuple[str, str], DirRecord] = {}
    if fresh:
        rows = (await db.execute(select(DirectoryEntry).where(
            DirectoryEntry.backend.in_({f.backend for f in fresh}),
            DirectoryEntry.ref.in_({f.ref for f in fresh}),
        ))).scalars().all()
        stored = {(e.backend, e.ref): _to_record(e) for e in rows}
    out: list[DirRecord] = []
    for r, l in zip(records, lives):
        if l is None:
            continue
        out.append(stored.get((l.backend, l.ref), l) if isinstance(l, DirRecord) else r)
    return out
