import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import check_source_access, get_current_user
from akashic.database import get_db
from akashic.models.entry import Entry, EntryEvent, EntryVersion
from akashic.models.scan import Scan
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.scan import ScanBatchIn, ScanBatchResponse
from akashic.services.ingest import (
    compute_viewable_buckets,
    entry_state_changed,
    serialize_acl,
)
from akashic.services.tag_inheritance import (
    propagate_to_new_entry,
    rebalance_on_move,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


def _parent_path(path: str) -> str:
    parent = os.path.dirname(path) or "/"
    return parent


# Cached background-task engines, keyed by db_url. Each background
# task used to spin up a fresh AsyncEngine and dispose it on completion;
# under sustained ingest that meant churning through dozens of short-
# lived connections. Cache here so tasks share one pool per URL.
# Keyed by URL (not module-globally) so tests pointing at a different
# database get their own engine and don't bleed into the production
# pool — `akashic.database` already manages the latter for request-
# path code.
_bg_session_makers: dict[str, "async_sessionmaker[AsyncSession]"] = {}


def _bg_session(db_url: str) -> "async_sessionmaker[AsyncSession]":
    """Return a process-cached async_sessionmaker for the given URL.

    Background tasks call this instead of building their own engine so
    a busy ingest doesn't create + dispose dozens of engines.
    """
    sm = _bg_session_makers.get(db_url)
    if sm is None:
        from sqlalchemy.ext.asyncio import (
            async_sessionmaker as _asm,
            create_async_engine,
        )
        engine = create_async_engine(db_url)
        sm = _asm(engine, class_=AsyncSession, expire_on_commit=False)
        _bg_session_makers[db_url] = sm
    return sm


async def _index_files_to_meilisearch(entry_ids: list[str], db_url: str):
    """Background task: index ingested file entries into Meilisearch."""
    from akashic.services.search import build_entry_doc, index_files_batch
    from akashic.services.tag_inheritance import get_tags_for_entries

    try:
        async with _bg_session(db_url)() as db:
            uuids = [uuid.UUID(i) for i in entry_ids]
            tag_map = await get_tags_for_entries(db, entry_ids=uuids)
            # Bulk-fetch all entries in one round-trip rather than one
            # SELECT per id. The downstream Meili push is already a
            # single batch call.
            result = await db.execute(
                select(Entry).where(Entry.id.in_(uuids))
            )
            entries = [e for e in result.scalars() if e.kind == "file"]
            if entries:
                await index_files_batch([
                    build_entry_doc(e, tags=tag_map.get(e.id, []))
                    for e in entries
                ])
    except Exception as exc:
        logger.warning("Meilisearch indexing failed: %s", exc)


def _enqueue_extraction_jobs(entry_ids: list[str], redis_url: str):
    """Background task: enqueue text extraction jobs to Redis."""
    try:
        from redis import Redis
        from rq import Queue

        conn = Redis.from_url(redis_url)
        q = Queue("extraction", connection=conn)
        for entry_id in entry_ids:
            q.enqueue("akashic.workers.extraction.process_file_extraction", entry_id)
        logger.info("Enqueued %d extraction jobs", len(entry_ids))
    except Exception as exc:
        logger.warning("Failed to enqueue extraction jobs: %s", exc)


async def _rollup_subtree_aggregates(source_id: str, db_url: str):
    """Background task: refresh subtree_size / file_count / dir_count
    on every directory in the source after an ingest's final batch
    has settled. Cheap on incremental scans (most aggregates didn't
    change), bounded by directory count on full scans. Failures are
    logged but don't propagate — the StorageExplorer treemap will
    just look stale until the next scan."""
    from akashic.services.subtree_rollup import rollup_source, rollup_top_children

    try:
        async with _bg_session(db_url)() as db:
            # Phase B safety net: only fill in NULL rows. Connectors
            # that emit subtree totals at scan time (Local from Phase B
            # onward) win; the rollup back-fills directories the
            # connector couldn't compute (S3 streaming, legacy data).
            await rollup_source(db, uuid.UUID(source_id), null_only=True)
            # v0.4.11 Phase 8 — pre-compute top-K children per directory
            # so the storage tree read path can iteratively expand from
            # JSONB instead of running a recursive CTE. Must run AFTER
            # rollup_source because top_children needs the freshly-
            # computed subtree_size_bytes to rank correctly.
            await rollup_top_children(db, uuid.UUID(source_id))
            await db.commit()
    except Exception as exc:
        logger.warning(
            "subtree rollup failed for source_id=%s: %s", source_id, exc,
        )


async def _write_scan_snapshot(scan_id: str, source_id: str, db_url: str):
    """Background task: write a scan_snapshot row at scan completion.

    Done in the background (not in the ingest transaction) because the
    aggregates run a few SQL passes over the entries table, which we
    don't want blocking the final batch's response. Failure is logged
    but does not propagate — a missing snapshot row degrades analytics
    charts, but must not fail the scan."""
    from akashic.services.snapshot_writer import write_snapshot

    try:
        async with _bg_session(db_url)() as db:
            await write_snapshot(db, uuid.UUID(source_id), uuid.UUID(scan_id))
            await db.commit()
        logger.info("scan_snapshot written for scan_id=%s source_id=%s", scan_id, source_id)
    except Exception as exc:
        logger.warning(
            "scan_snapshot write failed for scan_id=%s source_id=%s: %s",
            scan_id, source_id, exc,
        )


async def _dispatch_scan_webhooks(scan_id: str, source_id: str, status: str, db_url: str):
    """Background task: dispatch webhooks for scan events.

    Scoped to webhook owners who have access to this source (admins or
    SourcePermission rows).
    """
    from akashic.models.user import SourcePermission, User
    from akashic.models.webhook import Webhook
    from akashic.services.webhooks import dispatch_webhook

    event_type = f"scan.{status}"
    try:
        async with _bg_session(db_url)() as db:
            admin_ids_result = await db.execute(
                select(User.id).where(User.role == "admin")
            )
            admin_ids = [row[0] for row in admin_ids_result.all()]

            perm_ids_result = await db.execute(
                select(SourcePermission.user_id).where(
                    SourcePermission.source_id == uuid.UUID(source_id)
                )
            )
            permitted_user_ids = [row[0] for row in perm_ids_result.all()]

            allowed_user_ids = list(set(admin_ids + permitted_user_ids))
            if not allowed_user_ids:
                return

            result = await db.execute(
                select(Webhook).where(
                    Webhook.event_type == event_type,
                    Webhook.enabled == True,  # noqa: E712
                    Webhook.user_id.in_(allowed_user_ids),
                )
            )
            for webhook in result.scalars().all():
                await dispatch_webhook(webhook, {
                    "event": event_type,
                    "scan_id": scan_id,
                    "source_id": source_id,
                })
    except Exception as exc:
        logger.warning("Webhook dispatch failed: %s", exc)


def _apply_entry_fields(target: Entry, src):
    """Copy versioned + descriptive fields from incoming EntryIn (or Entry) onto target."""
    target.kind = src.kind
    target.extension = src.extension
    target.size_bytes = src.size_bytes
    target.mime_type = src.mime_type
    target.content_hash = src.content_hash
    target.mode = src.mode
    target.uid = src.uid
    target.gid = src.gid
    target.owner_name = src.owner_name
    target.group_name = src.group_name
    target.acl = serialize_acl(src.acl)
    target.xattrs = src.xattrs
    target.fs_created_at = src.fs_created_at
    target.fs_modified_at = src.fs_modified_at
    target.fs_accessed_at = src.fs_accessed_at
    # Denormalize ACL → CRUDS token arrays. Same call also feeds the Meili
    # doc in build_entry_doc — kept in lockstep via compute_viewable_buckets.
    buckets = compute_viewable_buckets(src.acl, src.mode, src.uid, src.gid)
    target.viewable_by_read = buckets["read"]
    target.viewable_by_write = buckets["write"]
    target.viewable_by_delete = buckets["delete"]
    # Phase B — connectors that walk depth-first emit subtree totals
    # alongside each directory record. Trust the scanner when present
    # (zero is a valid value for an empty directory — `is not None`,
    # not truthiness). The post-scan rollup CTE backfills NULL rows as
    # a safety net for connectors that omit these.
    sub_size = getattr(src, "subtree_size_bytes", None)
    if sub_size is not None:
        target.subtree_size_bytes = sub_size
    sub_files = getattr(src, "subtree_file_count", None)
    if sub_files is not None:
        target.subtree_file_count = sub_files
    sub_dirs = getattr(src, "subtree_dir_count", None)
    if sub_dirs is not None:
        target.subtree_dir_count = sub_dirs


def _snapshot_version(entry: Entry, scan_id) -> EntryVersion:
    """Capture the current state of an entry as an EntryVersion row."""
    return EntryVersion(
        entry_id=entry.id,
        scan_id=scan_id,
        content_hash=entry.content_hash,
        size_bytes=entry.size_bytes,
        mode=entry.mode,
        uid=entry.uid,
        gid=entry.gid,
        owner_name=entry.owner_name,
        group_name=entry.group_name,
        acl=entry.acl,
        xattrs=entry.xattrs,
    )


@router.post("/batch", response_model=ScanBatchResponse)
async def ingest_batch(
    batch: ScanBatchIn,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await check_source_access(batch.source_id, user, db, required_level="write")

    # scan_start sits 1ms before now so stale-detection (last_seen_at < scan.started_at)
    # works on single-batch scans.
    scan_start = datetime.now(timezone.utc) - timedelta(milliseconds=1)
    now = datetime.now(timezone.utc)

    result = await db.execute(select(Scan).where(Scan.id == batch.scan_id))
    scan = result.scalar_one_or_none()
    if scan:
        if scan.source_id != batch.source_id:
            raise HTTPException(
                status_code=400, detail="scan_id does not belong to this source"
            )
        if scan.started_at is None:
            scan.started_at = scan_start
        if scan.status == "pending":
            scan.status = "running"
    else:
        scan = Scan(
            id=batch.scan_id,
            source_id=batch.source_id,
            scan_type="incremental",
            status="running",
            started_at=scan_start,
            files_found=0,
            files_new=0,
            files_changed=0,
            files_deleted=0,
        )
        db.add(scan)

    files_processed = 0
    new_file_ids: list[str] = []
    changed_file_ids: list[str] = []

    # Bulk-load existing entries for dedup in a single round-trip.
    # Pre-v0.4.x this was one SELECT per incoming entry, which dominated
    # ingest latency at 10M+-file scale (1k-entry batch = 1k round-trips).
    incoming_paths = [incoming.path for incoming in batch.entries]
    existing_by_path: dict[str, Entry] = {}
    if incoming_paths:
        existing_result = await db.execute(
            select(Entry).where(
                Entry.source_id == batch.source_id,
                Entry.path.in_(incoming_paths),
            )
        )
        for entry in existing_result.scalars():
            existing_by_path[entry.path] = entry

    for incoming in batch.entries:
        existing = existing_by_path.get(incoming.path)

        if existing:
            if entry_state_changed(existing, incoming):
                # Snapshot the old state before overwriting
                db.add(_snapshot_version(existing, batch.scan_id))
                _apply_entry_fields(existing, incoming)
                if existing.kind == "file":
                    scan.files_changed += 1
                    changed_file_ids.append(str(existing.id))
            existing.last_seen_at = now
            existing.is_deleted = False
            existing.deleted_at = None
        else:
            buckets = compute_viewable_buckets(
                incoming.acl, incoming.mode, incoming.uid, incoming.gid
            )
            new_entry = Entry(
                source_id=batch.source_id,
                kind=incoming.kind,
                parent_path=_parent_path(incoming.path),
                path=incoming.path,
                name=incoming.name,
                extension=incoming.extension,
                size_bytes=incoming.size_bytes,
                mime_type=incoming.mime_type,
                content_hash=incoming.content_hash,
                mode=incoming.mode,
                uid=incoming.uid,
                gid=incoming.gid,
                owner_name=incoming.owner_name,
                group_name=incoming.group_name,
                acl=serialize_acl(incoming.acl),
                xattrs=incoming.xattrs,
                viewable_by_read=buckets["read"],
                viewable_by_write=buckets["write"],
                viewable_by_delete=buckets["delete"],
                # Phase B subtree totals — None for connectors that don't
                # emit them; the post-scan rollup CTE backfills.
                subtree_size_bytes=incoming.subtree_size_bytes,
                subtree_file_count=incoming.subtree_file_count,
                subtree_dir_count=incoming.subtree_dir_count,
                fs_created_at=incoming.fs_created_at,
                fs_modified_at=incoming.fs_modified_at,
                fs_accessed_at=incoming.fs_accessed_at,
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(new_entry)
            await db.flush()
            # Seed a v0 row so version history starts on first observation.
            db.add(_snapshot_version(new_entry, batch.scan_id))

            # Phase C — a new entry under a tagged ancestor inherits the
            # ancestor's tags. Cheap on the common case (no tagged
            # ancestors); one indexed SELECT either way.
            await propagate_to_new_entry(
                db,
                entry_id=new_entry.id,
                source_id=batch.source_id,
                path=new_entry.path,
            )

            if incoming.kind == "file":
                new_file_ids.append(str(new_entry.id))
                scan.files_new += 1

        if incoming.kind == "file":
            files_processed += 1

    scan.files_found += files_processed

    if batch.source_security_metadata is not None:
        source_result = await db.execute(
            select(Source).where(Source.id == batch.source_id)
        )
        source_row = source_result.scalar_one_or_none()
        if source_row is not None:
            source_row.security_metadata = batch.source_security_metadata

    if batch.is_final:
        scan.status = "completed"
        scan.completed_at = now

        source_result = await db.execute(
            select(Source).where(Source.id == batch.source_id)
        )
        source = source_result.scalar_one_or_none()
        if source:
            source.last_scan_at = now
            source.status = "online"

        if scan.started_at:
            stale_result = await db.execute(
                select(Entry).where(
                    Entry.source_id == batch.source_id,
                    Entry.is_deleted == False,  # noqa: E712
                    Entry.last_seen_at < scan.started_at,
                )
            )
            for stale in stale_result.scalars().all():
                stale.is_deleted = True
                stale.deleted_at = now
                if stale.kind == "file":
                    scan.files_deleted += 1

                if stale.kind == "file" and stale.content_hash:
                    new_location = await db.execute(
                        select(Entry).where(
                            Entry.content_hash == stale.content_hash,
                            Entry.kind == "file",
                            Entry.is_deleted == False,  # noqa: E712
                            Entry.last_seen_at >= scan.started_at,
                            Entry.id != stale.id,
                        ).limit(1)
                    )
                    moved_to = new_location.scalar_one_or_none()
                    if moved_to:
                        db.add(EntryEvent(
                            event_type="moved",
                            content_hash=stale.content_hash,
                            old_source_id=stale.source_id,
                            old_path=stale.path,
                            new_source_id=moved_to.source_id,
                            new_path=moved_to.path,
                            scan_id=batch.scan_id,
                        ))

    await db.commit()

    # v0.4.11: event-driven scan.state broadcast. Batches arrive only
    # when the scanner has accumulated files to ingest — this is a real
    # event, so we always publish (and update the change-detection
    # snapshot so a heartbeat arriving 1s later doesn't immediately
    # re-broadcast the same state).
    from akashic.services import scan_broadcast, scan_pubsub
    await scan_pubsub.publish_source_event({
        "kind": "scan.state",
        "source_id": str(batch.source_id),
        "scan_id": str(batch.scan_id),
        "scan_status": scan.status,
        "source_status": "online" if batch.is_final else "scanning",
        "scanner_id": (
            str(scan.assigned_scanner_id) if scan.assigned_scanner_id else None
        ),
        "scanner_name": None,
        "scan_type": scan.scan_type,
        "files_found": scan.files_found,
        "current_path": scan.current_path,
        "started_at": scan.started_at.isoformat() if scan.started_at else None,
    })
    await scan_broadcast.record_broadcast(
        str(batch.scan_id),
        phase=scan.phase,
        status=scan.status,
        files_found=scan.files_found,
        total_estimated=scan.total_estimated,
    )

    # v0.4.11 Phase 8e — mark touched parent paths dirty for the
    # streaming top_children worker. No-op when the feature flag is
    # off; otherwise the worker drains the set on its next tick and
    # recomputes the affected parents' top_children JSONB.
    from akashic.services import top_children_worker
    touched_parents = list({_parent_path(e.path) for e in batch.entries})
    await top_children_worker.mark_dirty(batch.source_id, touched_parents)

    from akashic.config import settings

    indexed_ids = list(set(new_file_ids + changed_file_ids))
    if indexed_ids:
        background_tasks.add_task(
            _index_files_to_meilisearch, indexed_ids, settings.database_url
        )
        background_tasks.add_task(
            _enqueue_extraction_jobs, indexed_ids, settings.redis_url
        )

    if batch.is_final:
        # Order matters: rollup runs BEFORE the snapshot writer so the
        # snapshot's totals see the freshly-computed subtree aggregates.
        # Both run in the background — the user sees the ScanBatchResponse
        # immediately and these settle asynchronously.
        background_tasks.add_task(
            _rollup_subtree_aggregates,
            str(batch.source_id),
            settings.database_url,
        )
        background_tasks.add_task(
            _write_scan_snapshot,
            str(batch.scan_id),
            str(batch.source_id),
            settings.database_url,
        )
        background_tasks.add_task(
            _dispatch_scan_webhooks,
            str(batch.scan_id),
            str(batch.source_id),
            scan.status,
            settings.database_url,
        )

    return ScanBatchResponse(files_processed=files_processed, scan_id=batch.scan_id)
