"""Runner for admin-triggered long-running maintenance tasks.

The Meilisearch reindex and the three backfill tools take minutes on a
large catalog — too long for a synchronous HTTP request (the browser
hangs, a reverse proxy may time the request out). Instead the endpoint
records a `running` MaintenanceJob row and `start_job` kicks off an
in-process `asyncio` task; `_run` flips the row to `succeeded`/`failed`
with the row count, and the Maintenance page polls the table.

The task does not survive an API restart — `reconcile_orphans` marks
any row left `running` as `failed` on the next startup.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import func, select, update

from akashic.database import async_session
from akashic.models.maintenance_job import MaintenanceJob
from akashic.models.user import User
from akashic.tools import (
    backfill_subtree_sizes,
    backfill_viewable,
    reindex_search,
    warm_groups,
)

logger = logging.getLogger(__name__)

# Default batch sizes mirror each tool's CLI default.
_REINDEX_BATCH = 100
_VIEWABLE_BATCH = 500


class JobAlreadyRunning(Exception):
    """Raised when a job of the same kind is already running."""

    def __init__(self, kind: str) -> None:
        super().__init__(f"a {kind} job is already running")
        self.kind = kind


def _source_id(params: dict) -> uuid.UUID | None:
    raw = params.get("source_id")
    return uuid.UUID(str(raw)) if raw else None


async def _do_reindex(params: dict) -> int:
    batch = int(params.get("batch_size") or _REINDEX_BATCH)
    return await reindex_search._reindex(batch)


async def _do_subtree_sizes(params: dict) -> int:
    return await backfill_subtree_sizes._backfill(_source_id(params))


async def _do_viewable(params: dict) -> int:
    batch = int(params.get("batch_size") or _VIEWABLE_BATCH)
    # A one-shot run wants no resume state — use a fresh temp checkpoint
    # (it won't exist, so the backfill starts from the beginning) and
    # drop it afterwards.
    ckpt = Path(tempfile.gettempdir()) / f"akashic-maint-viewable-{uuid.uuid4().hex}.ckpt"
    try:
        return await backfill_viewable._backfill(batch, ckpt, _source_id(params))
    finally:
        ckpt.unlink(missing_ok=True)


async def _do_warm_groups(params: dict) -> int:
    return await warm_groups._warm(_source_id(params))


# kind → coroutine. The Maintenance router validates `kind` against these
# keys before calling start_job.
JOB_KINDS: dict[str, object] = {
    "reindex_search": _do_reindex,
    "backfill_subtree_sizes": _do_subtree_sizes,
    "backfill_viewable": _do_viewable,
    "warm_groups": _do_warm_groups,
}

# Hold a strong reference to each in-flight task — asyncio only keeps a
# weak reference, so without this the task could be garbage-collected
# mid-run.
_tasks: set[asyncio.Task] = set()


async def start_job(
    db, kind: str, params: dict | None, user: User | None,
) -> MaintenanceJob:
    """Insert a `running` MaintenanceJob row and launch its task.

    Raises ValueError for an unknown kind and JobAlreadyRunning if a job
    of this kind is already in flight (the router maps those to 400/409).
    """
    if kind not in JOB_KINDS:
        raise ValueError(f"unknown maintenance job kind: {kind}")

    params = params or {}
    running = await db.execute(
        select(MaintenanceJob.id).where(
            MaintenanceJob.kind == kind, MaintenanceJob.status == "running",
        )
    )
    if running.first() is not None:
        raise JobAlreadyRunning(kind)

    job = MaintenanceJob(
        kind=kind,
        status="running",
        params=params,
        triggered_by=user.id if user is not None else None,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    task = asyncio.create_task(_run(job.id, kind, params))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job


async def _run(job_id: uuid.UUID, kind: str, params: dict) -> None:
    """Execute the job's tool function and write the terminal row state."""
    status = "succeeded"
    result: dict | None = None
    error: str | None = None
    try:
        rows = await JOB_KINDS[kind](params)  # type: ignore[operator]
        result = {"rows_affected": int(rows)}
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("maintenance job %s (%s) failed", job_id, kind)

    try:
        async with async_session() as db:
            await db.execute(
                update(MaintenanceJob)
                .where(MaintenanceJob.id == job_id)
                .values(
                    status=status, result=result, error=error,
                    finished_at=func.now(),
                )
            )
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("failed to record maintenance job %s outcome", job_id)


async def reconcile_orphans() -> int:
    """Mark any job still `running` as `failed` — the in-process task did
    not survive the previous API process. Called once on startup."""
    async with async_session() as db:
        result = await db.execute(
            update(MaintenanceJob)
            .where(MaintenanceJob.status == "running")
            .values(
                status="failed",
                error="interrupted by API restart",
                finished_at=func.now(),
            )
        )
        await db.commit()
        count = result.rowcount or 0
    if count:
        logger.info("reconciled %d orphaned maintenance job(s) to failed", count)
    return count
