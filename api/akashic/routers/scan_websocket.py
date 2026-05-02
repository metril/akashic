"""WebSocket fan-out for live scan progress and log streams.

Browser opens `WS /ws/scans/{id}?token=<jwt>` (token is supplied as a query
param because browsers can't set Authorization headers on WS handshakes).
The handler:

1. Authenticates and authorizes the user against the scan's source.
2. Sends a snapshot frame (current scan state + last 100 log lines).
3. Subscribes to Redis channel `scan:{id}` and forwards each event verbatim.

On disconnect: tears down the pubsub subscription. The caller backfills any
missed lines via `GET /api/scans/{id}/log?since=<last_ts>`.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import decode_access_token
from akashic.database import async_session
from akashic.models.scan import Scan
from akashic.models.scan_log_entry import ScanLogEntry
from akashic.models.user import User, SourcePermission
from akashic.services import scan_pubsub

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["scan-websocket"])

# Heartbeat interval to detect half-open clients. Must be smaller than any
# upstream proxy idle timeout (nginx defaults to 60 s).
_SERVER_HEARTBEAT_SECONDS = 30


async def _resolve_user(token: str, db: AsyncSession) -> User | None:
    payload = decode_access_token(token)
    if payload is None:
        return None
    sub = payload.get("sub")
    if sub is None:
        return None
    try:
        user_id = uuid.UUID(sub)
    except (ValueError, TypeError):
        return None
    return (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()


async def _user_can_read_source(user: User, source_id: uuid.UUID, db: AsyncSession) -> bool:
    if user.role == "admin":
        return True
    perm = (
        await db.execute(
            select(SourcePermission).where(
                SourcePermission.user_id == user.id,
                SourcePermission.source_id == source_id,
            )
        )
    ).scalar_one_or_none()
    return perm is not None  # any access level can subscribe to read-only stream


def _scan_snapshot(scan: Scan) -> dict:
    return {
        "kind": "snapshot",
        "scan_id": str(scan.id),
        "source_id": str(scan.source_id),
        "status": scan.status,
        "phase": scan.phase,
        "current_path": scan.current_path,
        "files_found": scan.files_found,
        "files_new": scan.files_new,
        "files_changed": scan.files_changed,
        "files_deleted": scan.files_deleted,
        "files_skipped": scan.files_skipped,
        "bytes_scanned_so_far": scan.bytes_scanned_so_far,
        "dirs_walked": scan.dirs_walked,
        "dirs_queued": scan.dirs_queued,
        "total_estimated": scan.total_estimated,
        "previous_scan_files": scan.previous_scan_files,
        "started_at": scan.started_at.isoformat() if scan.started_at else None,
        "completed_at": scan.completed_at.isoformat() if scan.completed_at else None,
        "last_heartbeat_at": (
            scan.last_heartbeat_at.isoformat() if scan.last_heartbeat_at else None
        ),
        "error_message": scan.error_message,
    }


def _log_line(row: ScanLogEntry) -> dict:
    return {
        "id": str(row.id),
        "ts": row.ts.isoformat(),
        "level": row.level,
        "message": row.message,
    }


@router.websocket("/scans/{scan_id}")
async def scan_stream(
    websocket: WebSocket,
    scan_id: uuid.UUID,
    token: str = Query(..., description="JWT access token"),
):
    """Single-scan stream. Authenticated via `?token=<jwt>` because browsers
    can't set headers on the WS handshake."""
    # Open the DB session in our own context — Depends() doesn't kick in
    # until after `accept`, and we want auth-failure to close before
    # accepting (cleaner client-side error).
    async with async_session() as db:
        user = await _resolve_user(token, db)
        if user is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="invalid token")
            return

        scan = (await db.execute(select(Scan).where(Scan.id == scan_id))).scalar_one_or_none()
        if scan is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="scan not found")
            return

        if not await _user_can_read_source(user, scan.source_id, db):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="forbidden")
            return

        # Build the snapshot synchronously (last 100 lines newest-first then
        # reversed so the client can append in order).
        recent = (
            await db.execute(
                select(ScanLogEntry)
                .where(ScanLogEntry.scan_id == scan_id)
                .order_by(ScanLogEntry.ts.desc())
                .limit(100)
            )
        ).scalars().all()
        snapshot = _scan_snapshot(scan)
        snapshot["recent_lines"] = [_log_line(r) for r in reversed(list(recent))]

    await websocket.accept()
    try:
        await websocket.send_json(snapshot)
    except Exception as exc:  # noqa: BLE001
        logger.debug("scan_stream snapshot send failed: %s", exc)
        return

    # Subscribe to Redis pub/sub. The iterator yields events as dicts.
    # On Redis failure (broker unreachable / config error), `_forward`
    # sends one diagnostic frame so the client knows the live stream is
    # silent for an infrastructure reason, not because nothing is
    # happening on the scan.
    forward_task: asyncio.Task | None = None
    heartbeat_task: asyncio.Task | None = None
    receive_task: asyncio.Task | None = None

    async def _forward() -> None:
        try:
            async for event in scan_pubsub.subscribe(scan_id):
                await websocket.send_json(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning("scan_stream %s: pub/sub error: %s", scan_id, exc)
            try:
                await websocket.send_json({
                    "kind": "error",
                    "message": "live stream unavailable; falling back to polling",
                })
            except Exception:  # noqa: BLE001
                pass

    async def _heartbeat() -> None:
        while True:
            await asyncio.sleep(_SERVER_HEARTBEAT_SECONDS)
            await websocket.send_json({"kind": "ping"})

    try:
        forward_task = asyncio.create_task(_forward())
        heartbeat_task = asyncio.create_task(_heartbeat())
        # `receive_text()` resolves when the peer closes; `wait` returns
        # on the first completion. The receive task exists solely as a
        # disconnect detector — we never read its return value.
        receive_task = asyncio.create_task(_drain_inbound(websocket))
        done, pending = await asyncio.wait(
            {forward_task, heartbeat_task, receive_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("scan_stream %s: %s", scan_id, exc)
    finally:
        # Cancel ALL three tasks — including receive_task — so a
        # mid-handler crash doesn't leak a background coroutine.
        for t in (forward_task, heartbeat_task, receive_task):
            if t is not None and not t.done():
                t.cancel()
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass


async def _drain_inbound(websocket: WebSocket) -> None:
    """Reads (and ignores) anything the client sends. The only purpose is
    to detect disconnection — `receive_text` raises WebSocketDisconnect
    when the peer closes."""
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        return


# ── List-level stream (/ws/scans) ─────────────────────────────────────────


@router.websocket("/scans")
async def scans_stream(
    websocket: WebSocket,
    token: str = Query(..., description="JWT access token"),
):
    """Forwards every scan/source state transition the user has access
    to. One WebSocket per browser tab; the Sources page + Dashboard
    share it via reference-counted client-side hook.

    Auth shape mirrors `/ws/scans/{id}`: query-param JWT, validated
    before accept; permitted_source_ids resolved once on connect; events
    for sources outside that set are silently dropped server-side.
    """
    from akashic.auth.dependencies import get_permitted_source_ids
    from akashic.models.source import Source as SourceModel

    async with async_session() as db:
        user = await _resolve_user(token, db)
        if user is None:
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION, reason="invalid token",
            )
            return
        # `None` means admin / no source-permission filter.
        permitted = await get_permitted_source_ids(user, db)

        # Snapshot: the current pending/running/recently-failed scans
        # the user can see. Same shape useActiveScans was building
        # client-side from /scans?status=…&limit=200 — but here we
        # send it as a single frame so the client doesn't need a
        # separate REST hop.
        scans_q = select(Scan).where(
            Scan.status.in_(["pending", "running", "failed"]),
        )
        if permitted is not None:
            if not permitted:
                # User can't see anything. Send empty snapshot and
                # then live-filter every event (which will also drop
                # everything). Cheap.
                snapshot_scans = []
            else:
                scans_q = scans_q.where(Scan.source_id.in_(permitted))
                snapshot_scans = list((await db.execute(scans_q)).scalars().all())
        else:
            snapshot_scans = list((await db.execute(scans_q)).scalars().all())

        # Look up scanner names so the snapshot frame carries them.
        from akashic.models.scanner import Scanner as ScannerModel

        scanner_ids = {
            s.assigned_scanner_id for s in snapshot_scans
            if s.assigned_scanner_id is not None
        }
        scanner_names: dict[uuid.UUID, str] = {}
        if scanner_ids:
            rows = (await db.execute(
                select(ScannerModel).where(ScannerModel.id.in_(scanner_ids))
            )).scalars().all()
            scanner_names = {r.id: r.name for r in rows}

        # And source statuses for the snapshot.
        source_ids_in_snapshot = {s.source_id for s in snapshot_scans}
        source_statuses: dict[uuid.UUID, str] = {}
        if source_ids_in_snapshot:
            rows = (await db.execute(
                select(SourceModel).where(SourceModel.id.in_(source_ids_in_snapshot))
            )).scalars().all()
            source_statuses = {r.id: r.status for r in rows}

    snapshot = {
        "kind": "snapshot",
        "scans": [
            {
                "scan_id": str(s.id),
                "source_id": str(s.source_id),
                "scan_status": s.status,
                "source_status": source_statuses.get(s.source_id, "unknown"),
                "scanner_id": (
                    str(s.assigned_scanner_id)
                    if s.assigned_scanner_id else None
                ),
                "scanner_name": (
                    scanner_names.get(s.assigned_scanner_id)
                    if s.assigned_scanner_id else None
                ),
                "scan_type": s.scan_type,
                "files_found": s.files_found or 0,
                "current_path": s.current_path,
                "started_at": s.started_at.isoformat() if s.started_at else None,
            }
            for s in snapshot_scans
        ],
    }

    await websocket.accept()
    try:
        await websocket.send_json(snapshot)
    except Exception as exc:  # noqa: BLE001
        logger.debug("scans_stream snapshot send failed: %s", exc)
        return

    permitted_set: set[str] | None = (
        {str(sid) for sid in permitted} if permitted is not None else None
    )

    forward_task: asyncio.Task | None = None
    heartbeat_task: asyncio.Task | None = None
    receive_task: asyncio.Task | None = None

    async def _forward() -> None:
        try:
            async for event in scan_pubsub.subscribe_sources():
                # Per-event RBAC filter. None == admin / no filter.
                if permitted_set is not None:
                    src = event.get("source_id")
                    if src is not None and src not in permitted_set:
                        continue
                await websocket.send_json(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning("scans_stream pub/sub error: %s", exc)
            try:
                await websocket.send_json({
                    "kind": "error",
                    "message": "live stream unavailable; reconnect to retry",
                })
            except Exception:  # noqa: BLE001
                pass

    async def _heartbeat() -> None:
        while True:
            await asyncio.sleep(_SERVER_HEARTBEAT_SECONDS)
            await websocket.send_json({"kind": "ping"})

    try:
        forward_task = asyncio.create_task(_forward())
        heartbeat_task = asyncio.create_task(_heartbeat())
        receive_task = asyncio.create_task(_drain_inbound(websocket))
        done, pending = await asyncio.wait(
            {forward_task, heartbeat_task, receive_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("scans_stream: %s", exc)
    finally:
        for t in (forward_task, heartbeat_task, receive_task):
            if t is not None and not t.done():
                t.cancel()
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass


# ── Admin scanner-lifecycle stream (/ws/scanners) ────────────────────────


@router.websocket("/scanners")
async def scanners_stream(
    websocket: WebSocket,
    token: str = Query(..., description="JWT access token"),
):
    """Admin-only push stream of scanner lifecycle events.

    Powers the SettingsScanners "Pending claims" pane:
      - snapshot frame on connect: current pending discovery requests
      - subsequent frames: events from the `scanners` pubsub channel

    Auth shape mirrors the per-scan handler — query-param JWT validated
    before accept — but rejects non-admin tokens (1008). Discovery
    requests carry no source-permission concept so per-event filtering
    isn't needed.
    """
    from akashic.models.scanner_discovery_request import (
        ScannerDiscoveryRequest,
    )

    async with async_session() as db:
        user = await _resolve_user(token, db)
        if user is None:
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="invalid token",
            )
            return
        if user.role != "admin":
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="admin required",
            )
            return

        rows = (await db.execute(
            select(ScannerDiscoveryRequest).where(
                ScannerDiscoveryRequest.status == "pending",
            )
            .order_by(ScannerDiscoveryRequest.requested_at.desc())
        )).scalars().all()

    snapshot = {
        "kind": "snapshot",
        "pending_discoveries": [
            {
                "id": str(r.id),
                "pairing_code": r.pairing_code,
                "hostname": r.hostname,
                "agent_version": r.agent_version,
                "requested_pool": r.requested_pool,
                "requested_at": r.requested_at.isoformat(),
                "expires_at": r.expires_at.isoformat(),
                "key_fingerprint": r.key_fingerprint,
            }
            for r in rows
        ],
    }

    await websocket.accept()
    try:
        await websocket.send_json(snapshot)
    except Exception as exc:  # noqa: BLE001
        logger.debug("scanners_stream snapshot send failed: %s", exc)
        return

    forward_task: asyncio.Task | None = None
    heartbeat_task: asyncio.Task | None = None
    receive_task: asyncio.Task | None = None

    async def _forward() -> None:
        try:
            async for event in scan_pubsub.subscribe_scanners():
                # `setting.changed` is internal cache-bust noise — don't
                # leak server-settings events to the operator's UI.
                if isinstance(event, dict) and event.get("kind") == "setting.changed":
                    continue
                await websocket.send_json(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning("scanners_stream pub/sub error: %s", exc)
            try:
                await websocket.send_json({
                    "kind": "error",
                    "message": "live stream unavailable; reconnect to retry",
                })
            except Exception:  # noqa: BLE001
                pass

    async def _heartbeat() -> None:
        while True:
            await asyncio.sleep(_SERVER_HEARTBEAT_SECONDS)
            await websocket.send_json({"kind": "ping"})

    try:
        forward_task = asyncio.create_task(_forward())
        heartbeat_task = asyncio.create_task(_heartbeat())
        receive_task = asyncio.create_task(_drain_inbound(websocket))
        done, pending = await asyncio.wait(
            {forward_task, heartbeat_task, receive_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("scanners_stream: %s", exc)
    finally:
        for t in (forward_task, heartbeat_task, receive_task):
            if t is not None and not t.done():
                t.cancel()
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass


# Suppress unused-import warning (timedelta imported at the top is used
# by the per-scan handler; keep the import on the module).
_ = timedelta
