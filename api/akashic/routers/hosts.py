"""CRUD for the `Host` model — reusable connection targets that
attach to many `Source` rows. See models/host.py for rationale."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user, require_admin
from akashic.database import get_db
from akashic.models.host import Host
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.host import HostCreate, HostResponse, HostUpdate
from akashic.services.audit import record_event
from akashic.services.source_merge import (
    field_diff,
    merge_connection_config,
    reject_sentinel_in_create,
)
from akashic.services.source_tester import TestResult

router = APIRouter(prefix="/api/hosts", tags=["hosts"])


_HOST_TYPES = {"ssh", "smb", "nfs", "s3"}


async def _source_count_for(db: AsyncSession, host_id: uuid.UUID) -> int:
    cnt = (await db.execute(
        select(func.count())
        .select_from(Source)
        .where(Source.host_id == host_id)
    )).scalar_one()
    return int(cnt or 0)


def _serialize(host: Host, source_count: int) -> HostResponse:
    payload = {
        "id": host.id,
        "name": host.name,
        "type": host.type,
        "connection_config": dict(host.connection_config or {}),
        "source_count": source_count,
        "created_at": host.created_at,
        "updated_at": host.updated_at,
    }
    return HostResponse.model_validate(payload)


@router.post("", response_model=HostResponse, status_code=status.HTTP_201_CREATED)
async def create_host(
    data: HostCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    if data.type not in _HOST_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported host type: {data.type!r} (expected one of {sorted(_HOST_TYPES)})",
        )
    err = reject_sentinel_in_create(data.connection_config)
    if err:
        raise HTTPException(status_code=400, detail=err)
    host = Host(name=data.name, type=data.type, connection_config=data.connection_config)
    db.add(host)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"host name {data.name!r} already in use")
    await db.refresh(host)
    await record_event(
        db=db, user=user, event_type="host_created",
        request=request,
        payload={"host_id": str(host.id), "name": host.name, "type": host.type},
    )
    return _serialize(host, source_count=0)


@router.get("", response_model=list[HostResponse])
async def list_hosts(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all hosts. Includes a `source_count` per row so the UI can
    show "3 shares" without a follow-up call.

    Visible to any authenticated user — same access tier as the source
    list (which any user can browse, filtered by per-source ACL on
    detail endpoints). Editing is admin-only.
    """
    rows = (await db.execute(select(Host).order_by(Host.name))).scalars().all()
    if not rows:
        return []
    counts_rows = (await db.execute(
        select(Source.host_id, func.count(Source.id))
        .where(Source.host_id.in_([h.id for h in rows]))
        .group_by(Source.host_id)
    )).all()
    counts = {hid: int(c) for hid, c in counts_rows}
    return [_serialize(h, source_count=counts.get(h.id, 0)) for h in rows]


@router.get("/{host_id}", response_model=HostResponse)
async def get_host(
    host_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    host = (await db.execute(select(Host).where(Host.id == host_id))).scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")
    return _serialize(host, source_count=await _source_count_for(db, host.id))


@router.patch("/{host_id}", response_model=HostResponse)
async def update_host(
    host_id: uuid.UUID,
    data: HostUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    host = (await db.execute(select(Host).where(Host.id == host_id))).scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")

    before = {
        "name": host.name,
        "connection_config": dict(host.connection_config or {}),
    }
    incoming = data.model_dump(exclude_unset=True)
    if "name" in incoming and incoming["name"] is not None:
        host.name = incoming["name"]
    if "connection_config" in incoming and incoming["connection_config"] is not None:
        # Reuse the same secret-merge helper as Source updates so the
        # UI can send back masked-secret sentinels and have them
        # preserved in place of the real persisted secret.
        host.connection_config = merge_connection_config(
            host.connection_config, incoming["connection_config"]
        )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="host name already in use")
    await db.refresh(host)

    after = {
        "name": host.name,
        "connection_config": dict(host.connection_config or {}),
    }
    diff_payload: dict = {}
    if before["name"] != after["name"]:
        diff_payload["name"] = {"before": before["name"], "after": after["name"]}
    cfg_diff = field_diff(before["connection_config"], after["connection_config"])
    if cfg_diff:
        diff_payload["connection_config"] = cfg_diff
    if diff_payload:
        await record_event(
            db=db, user=user, event_type="host_updated",
            request=request,
            payload={"host_id": str(host.id), "diff": diff_payload},
        )
    return _serialize(host, source_count=await _source_count_for(db, host.id))


@router.delete("/{host_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_host(
    host_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    host = (await db.execute(select(Host).where(Host.id == host_id))).scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")
    cnt = await _source_count_for(db, host.id)
    if cnt > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"host {host.name!r} has {cnt} attached source(s); "
                "delete or detach them before deleting the host."
            ),
        )
    snapshot = {"host_id": str(host.id), "name": host.name, "type": host.type}
    await db.delete(host)
    await db.commit()
    await record_event(
        db=db, user=user, event_type="host_deleted",
        request=request, payload=snapshot,
    )


class CheckHostReachabilityResponse(BaseModel):
    """Result of POST /api/hosts/{id}/test-connection.

    Runs the same probe as the source-tester, against the host's
    connection_config alone. Useful for verifying credentials right
    after creating or rotating them, without needing a share row.
    """

    result: TestResult
    checked_at: datetime


_DEFAULT_PORTS = {"ssh": 22, "smb": 445, "nfs": 2049}


def _probe_host(host_type: str, cfg: dict) -> TestResult:
    """Lightweight host-level reachability probe.

    Doesn't speak any protocol — just opens a TCP connection to the
    host:port (SSH/SMB/NFS) or pings the S3 endpoint. The point is
    to validate the host *exists and is reachable*; actual auth and
    share-level checks happen through the source-level test once the
    user attaches a share.
    """
    import socket
    if host_type in {"ssh", "smb", "nfs"}:
        host = (cfg.get("host") or "").strip()
        if not host:
            return TestResult(ok=False, step="config", error="host required")
        port = int(cfg.get("port") or _DEFAULT_PORTS[host_type])
        try:
            with socket.create_connection((host, port), timeout=5):
                return TestResult(ok=True)
        except OSError as exc:
            return TestResult(ok=False, step="connect", error=f"{host}:{port}: {exc}")
    if host_type == "s3":
        endpoint = (cfg.get("endpoint") or "").strip()
        region = (cfg.get("region") or "").strip()
        if not endpoint and not region:
            return TestResult(
                ok=False, step="config",
                error="endpoint (custom) or region (AWS) required",
            )
        # An HTTP HEAD against the endpoint is enough to confirm the
        # host resolves and serves; credentials get exercised by the
        # full source-test path.
        import urllib.error
        import urllib.request
        url = endpoint or f"https://s3.{region}.amazonaws.com"
        try:
            req = urllib.request.Request(url, method="HEAD")
            urllib.request.urlopen(req, timeout=5)  # noqa: S310
            return TestResult(ok=True)
        except urllib.error.HTTPError as exc:
            # 4xx means "we reached the service, just don't have the
            # right path/credentials" — fine for a host-level probe.
            if 400 <= exc.code < 500:
                return TestResult(ok=True)
            return TestResult(ok=False, step="connect", error=f"HTTP {exc.code}")
        except Exception as exc:  # noqa: BLE001
            return TestResult(ok=False, step="connect", error=str(exc))
    return TestResult(ok=False, step="config", error=f"unsupported host type {host_type!r}")


@router.post(
    "/{host_id}/test-connection",
    response_model=CheckHostReachabilityResponse,
)
async def test_host_connection(
    host_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    host = (await db.execute(select(Host).where(Host.id == host_id))).scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")
    result = await asyncio.to_thread(
        _probe_host, host.type, dict(host.connection_config or {})
    )
    now = datetime.now(timezone.utc)
    await record_event(
        db=db, user=user, event_type="host_connection_tested",
        request=request,
        payload={
            "host_id": str(host.id),
            "ok": result.ok,
            "step": result.step,
            "error": result.error,
        },
    )
    return CheckHostReachabilityResponse(result=result, checked_at=now)
