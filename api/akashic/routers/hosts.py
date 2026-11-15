"""CRUD for the `Host` model — reusable connection targets that
attach to many `Source` rows. See models/host.py for rationale."""
from __future__ import annotations
from typing import Annotated

import asyncio
import uuid
from datetime import datetime, timezone

from akashic.routing import ApiRouter
from fastapi import Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import Principal, require_scope
from akashic.database import get_db
from akashic.models.host import Host
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.host import (
    AddSharesRequest,
    AddSharesResponse,
    HostCreate,
    HostResponse,
    HostUpdate,
    ListSharesResponse,
)
from akashic.services import share_enumerator
from akashic.services.audit import record_event
from akashic.services.source_config import (
    merge_host_and_source,
    validate_scan_controls,
)
from akashic.services.source_defaults import infer_is_removable
from akashic.services.source_merge import (
    field_diff,
    merge_connection_config,
    reject_sentinel_in_create,
)
from akashic.services.source_tester import TestResult

router = ApiRouter(prefix="/hosts", tags=["hosts"])


_HOST_TYPES = {"smb", "nfs", "s3"}


async def _publish_host_changed(host_id: uuid.UUID) -> None:
    """Notify /ws/v1/scans subscribers that a host row changed so the Hosts
    page refreshes without polling. Rides the sources channel (hosts and
    sources are the same domain) — fire-and-forget like the source events."""
    from akashic.services import scan_pubsub
    await scan_pubsub.publish_source_event({
        "kind": "host.changed",
        "host_id": str(host_id),
    })


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
        "credential_profile_id": host.credential_profile_id,
        "max_parallel_scanners": host.max_parallel_scanners,
        "scan_chunk_size": host.scan_chunk_size,
        "source_count": source_count,
        "created_at": host.created_at,
        "updated_at": host.updated_at,
    }
    return HostResponse.model_validate(payload)


async def _validate_profile_type(
    db: AsyncSession, profile_id: uuid.UUID | None, host_or_source_type: str,
) -> None:
    """Reject mismatched profile/type pairings before they break a scan."""
    if profile_id is None:
        return
    from akashic.models.credential_profile import CredentialProfile
    p = (await db.execute(
        select(CredentialProfile).where(CredentialProfile.id == profile_id)
    )).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail="Credential profile not found")
    if p.type != host_or_source_type:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Credential profile type {p.type!r} does not match "
                f"target type {host_or_source_type!r}."
            ),
        )


@router.post(
    "",
    response_model=HostResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a host",
    description=(
        "Creates a network host (SMB, NFS or S3) that sources attach to. Its connection config "
        "and credential profile are inherited by those sources, and the optional scan- "
        "distribution limits apply to every attached source that does not set its own. Secrets in "
        "the response are masked. Records a `host_created` audit event.\n"
        "Requires scope `hosts:write`.\n"
        "Errors: 400 for an unsupported `type`, a masked-secret placeholder in "
        "`connection_config`, out-of-range scan controls, or a credential profile of a different "
        "type; 404 if the credential profile does not exist; 409 if the name is already used."
    ),
)
async def create_host(
    data: HostCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("hosts:write"),
):
    user = principal.user
    if data.type not in _HOST_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported host type: {data.type!r} (expected one of {sorted(_HOST_TYPES)})",
        )
    err = reject_sentinel_in_create(data.connection_config)
    if err:
        raise HTTPException(status_code=400, detail=err)
    ctrl_err = validate_scan_controls(
        max_parallel_scanners=data.max_parallel_scanners,
        scan_chunk_size=data.scan_chunk_size,
    )
    if ctrl_err:
        raise HTTPException(status_code=400, detail=ctrl_err)
    await _validate_profile_type(db, data.credential_profile_id, data.type)
    host = Host(
        name=data.name,
        type=data.type,
        connection_config=data.connection_config,
        credential_profile_id=data.credential_profile_id,
        max_parallel_scanners=data.max_parallel_scanners,
        scan_chunk_size=data.scan_chunk_size,
    )
    db.add(host)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"host name {data.name!r} already in use")
    await db.refresh(host)
    await _publish_host_changed(host.id)
    await record_event(
        db=db, user=user, event_type="host_created",
        request=request,
        payload={"host_id": str(host.id), "name": host.name, "type": host.type},
    )
    return _serialize(host, source_count=0)


@router.get(
    "",
    response_model=list[HostResponse],
    summary="List hosts",
    description=(
        "Every host, with `source_count` so a list view can show how many shares are attached. "
        "Secrets are masked. Requires scope `hosts:read`."
    ),
)
async def list_hosts(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("hosts:read"),
):
    """List all hosts. Includes a `source_count` per row so the UI can
    show "3 shares" without a follow-up call.

    Visible to any authenticated user — same access tier as the source
    list (which any user can browse, filtered by per-source ACL on
    detail endpoints). Editing is admin-only.
    """
    user = principal.user
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


@router.get(
    "/{host_id}",
    response_model=HostResponse,
    summary="Get a host",
    description="Requires scope `hosts:read`. Errors: 404 if the host does not exist.",
)
async def get_host(
    host_id: Annotated[uuid.UUID, Path(description="Host id.")],
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("hosts:read"),
):
    host = (await db.execute(select(Host).where(Host.id == host_id))).scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")
    return _serialize(host, source_count=await _source_count_for(db, host.id))


@router.patch(
    "/{host_id}",
    response_model=HostResponse,
    summary="Update a host",
    description=(
        "Partial update. In `connection_config`, masked secret values (`***`) keep the stored "
        "secret. Only the scan controls present in the body are validated, and an explicit null "
        "clears one so it falls back to the default. The type cannot change. Records a "
        "`host_updated` audit event with the changed fields when something changed.\n"
        "Requires scope `hosts:write`.\n"
        "Errors: 400 for out-of-range scan controls or a credential profile of a different type; "
        "404 if the host or credential profile does not exist; 409 if the new name is already "
        "used."
    ),
)
async def update_host(
    host_id: Annotated[uuid.UUID, Path(description="Host id.")],
    data: HostUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("hosts:write"),
):
    user = principal.user
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
    if "credential_profile_id" in incoming:
        await _validate_profile_type(db, incoming["credential_profile_id"], host.type)
        host.credential_profile_id = incoming["credential_profile_id"]
    if "max_parallel_scanners" in incoming or "scan_chunk_size" in incoming:
        # Only the fields actually present in the patch are range-checked;
        # an explicit NULL (clear → inherit / built-in default) passes.
        ctrl_err = validate_scan_controls(
            max_parallel_scanners=incoming.get("max_parallel_scanners"),
            scan_chunk_size=incoming.get("scan_chunk_size"),
        )
        if ctrl_err:
            raise HTTPException(status_code=400, detail=ctrl_err)
        if "max_parallel_scanners" in incoming:
            host.max_parallel_scanners = incoming["max_parallel_scanners"]
        if "scan_chunk_size" in incoming:
            host.scan_chunk_size = incoming["scan_chunk_size"]
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="host name already in use")
    await db.refresh(host)
    await _publish_host_changed(host.id)

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


@router.delete(
    "/{host_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a host",
    description=(
        "Deletes a host that has no sources attached. Records a `host_deleted` audit event.\n"
        "Requires scope `hosts:write`.\n"
        "Errors: 404 if the host does not exist; 409 if sources are still attached (delete or "
        "detach them first)."
    ),
)
async def delete_host(
    host_id: Annotated[uuid.UUID, Path(description="Host id.")],
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("hosts:write"),
):
    user = principal.user
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
    await _publish_host_changed(host_id)
    await record_event(
        db=db, user=user, event_type="host_deleted",
        request=request, payload=snapshot,
    )


class OnlineCheckResponse(BaseModel):
    """Result of POST /api/v1/hosts/{id}/online-check.

    Pure TCP probe from the API process — opens a socket to the
    server's port (SMB→445, NFS→2049) or HEADs the S3 endpoint. No
    credentials, no share listing. Answers "is the server up on the
    network from where the API sits?" — fast triage when reachability
    fails (so the user can tell DNS/firewall apart from credentials).

    Reachability proper is what the scanners report — see
    `POST /api/v1/sources/{id}/test-scanners`.
    """

    result: TestResult = Field(description="Outcome of the network probe.")
    checked_at: datetime = Field(description="When the probe ran.")


_DEFAULT_PORTS = {"smb": 445, "nfs": 2049}


def _probe_host(host_type: str, cfg: dict) -> TestResult:
    """TCP-only "is it online?" probe.

    Opens a socket to host:port (SMB/NFS) or HEADs the S3 endpoint.
    No protocol, no auth, no listing — just "does the server respond?"
    Credentialed reachability lives on the scanners (see
    services/probe_dispatch.py) and is exercised via /test-scanners.
    """
    import socket
    if host_type in {"smb", "nfs"}:
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
    "/{host_id}/online-check",
    response_model=OnlineCheckResponse,
    summary="Check whether a host is online",
    description=(
        "A network-level probe from the API process: a TCP connect to the SMB (445) or NFS (2049) "
        "port, or a request to the S3 endpoint. No credentials are used and no shares are listed, "
        "so it separates DNS and firewall problems from credential problems. Credentialed "
        "reachability is reported by scanners (see `POST /sources/{source_id}/test-scanners`). "
        "Records a `host_online_check` audit event.\n"
        "Requires scope `hosts:write`. Errors: 404 if the host does not exist."
    ),
)
async def host_online_check(
    host_id: Annotated[uuid.UUID, Path(description="Host id.")],
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("hosts:write"),
):
    """TCP "is it online?" probe. v0.28.1 renamed from /test-connection
    to make the API's role explicit: the API can only check whether the
    server responds on the network. Credentialed reachability is a
    scanner-side fact and lives on /api/v1/sources/{id}/test-scanners.
    """
    user = principal.user
    host = (await db.execute(select(Host).where(Host.id == host_id))).scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")
    # The TCP probe doesn't actually need credentials, but we layer them
    # in for symmetry with the rest of the host-config path and so the
    # audit log captures the same merged shape used elsewhere.
    result = await asyncio.to_thread(
        _probe_host, host.type, merge_host_and_source(host, None)
    )
    now = datetime.now(timezone.utc)
    await record_event(
        db=db, user=user, event_type="host_online_check",
        request=request,
        payload={
            "host_id": str(host.id),
            "ok": result.ok,
            "step": result.step,
            "error": result.error,
        },
    )
    return OnlineCheckResponse(result=result, checked_at=now)


# ── Bulk reachability test across attached shares (v0.28.1) ──────────────


class TestSharesRequest(BaseModel):
    """Optional per-source filter; default = every attached share."""

    source_ids: list[uuid.UUID] | None = Field(default=None, description="Only test these sources attached to the host; omit to test every attached share.")

    model_config = {"json_schema_extra": {'examples': [{'source_ids': ['0d4a9a0e-2b1c-4c5e-8f6d-1a2b3c4d5e6f']}]}}


class TestSharesResultRow(BaseModel):
    """One (source, scanner) probe outcome."""

    source_id: uuid.UUID = Field(description="Source that was probed.")
    source_name: str = Field(description="Name of that source.")
    scanner_id: uuid.UUID = Field(description="Scanner that ran the probe.")
    ok: bool | None = Field(description="Whether the scanner reached the source; null while the probe is pending.")
    step: str | None = Field(description="Step that failed (`connect`, `auth`, `mount`, `list` or `config`); null on success or while pending.")
    error: str | None = Field(description="Failure message; null on success or while pending.")
    pending: bool = Field(default=False, description="True when the scanner has not answered yet; the result arrives later on the source-reachability WebSocket channel.")
    completed_at: datetime | None = Field(default=None, description="When the probe finished; null while pending.")


class TestSharesResponse(BaseModel):
    results: list[TestSharesResultRow] = Field(description="One row per (source, eligible scanner).")


@router.post(
    "/{host_id}/test-shares",
    response_model=TestSharesResponse,
    summary="Test reachability of all shares",
    description=(
        "For every share (source) attached to the host, dispatches a credentialed probe to each "
        "online scanner permitted to claim it and returns one row per (source, scanner). The call "
        "waits up to 5 seconds per source; rows for scanners that have not answered yet have "
        "`pending: true` and their results arrive later on the source-reachability WebSocket "
        "channel. Sources with no eligible scanner produce no rows.\n"
        "Requires scope `hosts:write`. Errors: 404 if the host does not exist."
    ),
)
async def test_host_shares(
    host_id: Annotated[uuid.UUID, Path(description="Host id.")],
    body: TestSharesRequest | None = None,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("hosts:write"),
):
    """Bulk reachability fan-out: for every attached share, dispatch
    a credentialed probe to every scanner that's online and permitted
    to claim it. Returns flat per-(source, scanner) result rows.

    Reachability is what the scanners report — the API just orchestrates
    the fan-out. Slow scanners come back as `pending=true` and their
    results land later via the source-reachability WS channel.
    """
    user = principal.user
    host = (await db.execute(
        select(Host).where(Host.id == host_id)
    )).scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")

    requested_ids = (body.source_ids if body else None) or None
    src_q = select(Source).where(Source.host_id == host_id)
    if requested_ids:
        src_q = src_q.where(Source.id.in_(requested_ids))
    sources = list((await db.execute(src_q)).scalars().all())
    if not sources:
        return TestSharesResponse(results=[])

    from akashic.models.scanner import Scanner
    all_scanners = list((await db.execute(select(Scanner))).scalars().all())
    from akashic.routers.sources import _eligible_scanners_for
    from akashic.services import probe_dispatch

    rows: list[TestSharesResultRow] = []
    for src in sources:
        eligible = _eligible_scanners_for(src, all_scanners)
        if not eligible:
            continue
        delivered = await probe_dispatch.dispatch_remote(
            db=db, source=src,
            scanner_ids=[s.id for s in eligible],
            timeout_s=5.0,
            triggered_by=user.id,
        )
        for s in eligible:
            report = delivered.get(s.id)
            if report is None:
                rows.append(TestSharesResultRow(
                    source_id=src.id, source_name=src.name,
                    scanner_id=s.id,
                    ok=None, step=None, error=None, pending=True,
                ))
            else:
                rows.append(TestSharesResultRow(
                    source_id=src.id, source_name=src.name,
                    scanner_id=s.id,
                    ok=report.get("ok"),
                    step=report.get("step"),
                    error=report.get("error"),
                    pending=False,
                    completed_at=report.get("completed_at"),
                ))
    await db.commit()
    return TestSharesResponse(results=rows)


# ── Eligibility-management UI (v0.5.7) ─────────────────────────────────────


class HostScannerSummaryRow(BaseModel):
    scanner_id: uuid.UUID = Field(description="Scanner id.")
    name: str = Field(description="Scanner name.")
    pool: str | None = Field(description="Scanner pool; null when unassigned.")
    online: bool = Field(description="Whether the scanner has been seen recently (within about two minutes).")
    currently_allowed_count: int = Field(description="Number of the host's sources the scanner is allowed to claim.")
    reaches_count: int = Field(description="Number of the host's sources whose latest probe from this scanner succeeded.")
    unreachable_count: int = Field(description="Number of the host's sources whose latest probe from this scanner failed.")
    not_yet_probed_count: int = Field(description="Number of the host's sources this scanner has not been probed against.")
    total_sources: int = Field(description="Number of sources attached to the host.")


@router.get(
    "/{host_id}/scanner-reachability-summary",
    response_model=list[HostScannerSummaryRow],
    summary="Summarise scanner reachability for a host",
    description=(
        "Per scanner, how many of the host's attached sources it is currently allowed to claim, "
        "can reach, cannot reach, or has not been probed against, based on each pair's latest "
        "probe result. Feeds the host-level scanner eligibility checklist.\n"
        "Requires scope `hosts:read`. Errors: 404 if the host does not exist."
    ),
)
async def host_scanner_reachability_summary(
    host_id: Annotated[uuid.UUID, Path(description="Host id.")],
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("hosts:read"),
):
    """Per-scanner aggregation across this host's attached sources.
    Feeds the host-level eligibility checklist."""
    host = (await db.execute(
        select(Host).where(Host.id == host_id)
    )).scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")

    # v0.28.0: read from reachability_results (latest per pair, any age).
    # The 15-min staleness threshold is gone — under the new on-demand
    # model, "this scanner reached this source" stays true until a fresh
    # failed probe contradicts it. The history disclosure in the panel
    # is where age + trend information surfaces.
    from sqlalchemy import text
    rows = (await db.execute(text("""
        WITH attached AS (
            SELECT id FROM sources WHERE host_id = :host_id
        ),
        latest AS (
            SELECT DISTINCT ON (rr.source_id, rr.scanner_id)
                   rr.source_id, rr.scanner_id, rr.ok
              FROM reachability_results rr
              JOIN attached a ON a.id = rr.source_id
             ORDER BY rr.source_id, rr.scanner_id, rr.completed_at DESC
        )
        SELECT
            sc.id,
            sc.name,
            sc.pool,
            (sc.last_seen_at IS NOT NULL
             AND sc.last_seen_at > now() - interval '2 minutes') AS online,
            sc.allowed_source_ids,
            (SELECT count(*) FROM latest l
              WHERE l.scanner_id = sc.id AND l.ok = true) AS reaches_count,
            (SELECT count(*) FROM latest l
              WHERE l.scanner_id = sc.id AND l.ok = false) AS unreachable_count,
            (SELECT count(*) FROM attached) AS total_sources
          FROM scanners sc
         ORDER BY sc.name ASC
    """), {"host_id": host_id})).fetchall()

    out: list[HostScannerSummaryRow] = []
    attached_ids = list((await db.execute(
        select(Source.id).where(Source.host_id == host_id)
    )).scalars().all())
    attached_set = set(attached_ids)
    for r in rows:
        allowed = r[4]
        if allowed is None:
            allowed_count = len(attached_ids)  # NULL = all
        else:
            allowed_count = sum(1 for a in allowed if a in attached_set)
        not_probed = max(0, int(r[7]) - int(r[5]) - int(r[6]))
        out.append(HostScannerSummaryRow(
            scanner_id=r[0], name=r[1], pool=r[2], online=bool(r[3]),
            currently_allowed_count=allowed_count,
            reaches_count=int(r[5]),
            unreachable_count=int(r[6]),
            not_yet_probed_count=not_probed,
            total_sources=int(r[7]),
        ))
    return out


class HostAllowedScannersRequest(BaseModel):
    scanner_ids: list[uuid.UUID] = Field(description="Scanners that should be allowed on every source of the host; all others are removed. May be empty.")

    model_config = {"json_schema_extra": {'examples': [{'scanner_ids': ['2f1d9e1a-6c1b-4f43-8d6a-3e9f7b1c2a40']}]}}


class HostAllowedScannersResponse(BaseModel):
    sources_touched: int = Field(description="Number of sources attached to the host.")
    scanners_updated: int = Field(description="Number of scanners whose allow-list actually changed.")


@router.patch(
    "/{host_id}/allowed-scanners",
    response_model=HostAllowedScannersResponse,
    summary="Set allowed scanners for a host",
    description=(
        "Applies one allowed-scanner set to every source attached to the host: scanners in "
        "`scanner_ids` are added to each source's allow-list and all other scanners are removed "
        "from it. Idempotent. Records a `host_allowed_scanners_applied` audit event.\n"
        "Requires scope `hosts:write`.\n"
        "Errors: 400 if any scanner id is unknown; 404 if the host does not exist."
    ),
)
async def patch_host_allowed_scanners(
    host_id: Annotated[uuid.UUID, Path(description="Host id.")],
    body: HostAllowedScannersRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("hosts:write"),
):
    """Bulk-apply an allowed-scanner set across every source attached
    to this host. Idempotent. Audit: `host_allowed_scanners_applied`.
    """
    user = principal.user
    host = (await db.execute(
        select(Host).where(Host.id == host_id)
    )).scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")

    attached = list((await db.execute(
        select(Source.id).where(Source.host_id == host_id)
    )).scalars().all())
    if not attached:
        return HostAllowedScannersResponse(sources_touched=0, scanners_updated=0)

    from akashic.models.scanner import Scanner
    scanners = list((await db.execute(select(Scanner))).scalars().all())
    requested = set(body.scanner_ids)
    all_known = {s.id for s in scanners}
    unknown = requested - all_known
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown scanner_ids: {', '.join(str(u) for u in sorted(unknown))}",
        )

    attached_set = set(attached)
    scanners_updated = 0
    for s in scanners:
        in_requested = s.id in requested
        if s.allowed_source_ids is None:
            if in_requested:
                continue  # NULL allows all → nothing to change
            # Drop these host-attached sources from "all" by writing
            # an explicit list excluding them.
            all_others = list((await db.execute(
                select(Source.id).where(~Source.id.in_(attached_set))
            )).scalars().all())
            s.allowed_source_ids = all_others
            scanners_updated += 1
            continue

        current = list(s.allowed_source_ids or [])
        current_set = set(current)
        if in_requested:
            additions = [a for a in attached if a not in current_set]
            if additions:
                s.allowed_source_ids = current + additions
                scanners_updated += 1
        else:
            after = [c for c in current if c not in attached_set]
            if len(after) != len(current):
                s.allowed_source_ids = after
                scanners_updated += 1

    await db.commit()
    await record_event(
        db=db, user=user,
        event_type="host_allowed_scanners_applied",
        request=request,
        payload={
            "host_id": str(host_id),
            "scanner_ids": [str(i) for i in sorted(requested)],
            "sources_touched": len(attached),
            "scanners_updated": scanners_updated,
        },
    )
    return HostAllowedScannersResponse(
        sources_touched=len(attached),
        scanners_updated=scanners_updated,
    )


# ── Discover & batch-add shares (v0.5.4) ────────────────────────────────


# Per host type, the share-shaped key on Source.connection_config that
# identifies a single share. SMB → "share", NFS → "export_path",
# S3 → "bucket". Used by /add-shares to build the per-source config.
_SHARE_KEY_BY_TYPE = {
    "smb": "share",
    "nfs": "export_path",
    "s3":  "bucket",
}


@router.post(
    "/{host_id}/list-shares",
    response_model=ListSharesResponse,
    summary="List shares on a host",
    description=(
        "Enumerates the shares, exports or buckets visible to the host's credentials. On failure "
        "the response is still 200 with an empty list and the failing `step` and `error`. Records "
        "a `host_shares_listed` audit event.\n"
        "Requires scope `hosts:write`.\n"
        "Errors: 400 for host types without shares; 404 if the host does not exist."
    ),
)
async def list_host_shares(
    host_id: Annotated[uuid.UUID, Path(description="Host id.")],
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("hosts:write"),
):
    """Enumerate shares/exports/buckets visible to this host's
    credentials. Local hosts have no "shares" concept and return
    400 — use the regular Add Source form for them.
    """
    user = principal.user
    host = (await db.execute(
        select(Host).where(Host.id == host_id)
    )).scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")
    if host.type not in share_enumerator.SUPPORTED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"list-shares does not support host type {host.type!r}; "
                "use the Add Source form to attach shares manually."
            ),
        )
    # Probe is a blocking subprocess — same pattern as check-reachability.
    # Layer host.credential_profile.credentials under host.connection_config
    # so a profile-only host enumerates with the right creds.
    result = await asyncio.to_thread(
        share_enumerator.list_shares, host.type, merge_host_and_source(host, None),
    )
    await record_event(
        db=db, user=user, event_type="host_shares_listed",
        request=request,
        payload={
            "host_id": str(host.id),
            "share_count": len(result.shares),
            "step": result.step,
            "error": result.error,
        },
    )
    return ListSharesResponse(
        shares=result.shares, step=result.step, error=result.error,
    )


@router.post(
    "/{host_id}/add-shares",
    response_model=AddSharesResponse,
    summary="Add shares as sources",
    description=(
        "Creates one source per entry in `shares`, attached to the host. Each `share` becomes the "
        "share key of the source's connection config (`share` for SMB, `export_path` for NFS, "
        "`bucket` for S3); credentials stay on the host. The optional settings apply to every "
        "created source. Entries whose name is already used are skipped and counted in `skipped`. "
        "Records a `host_shares_batch_added` audit event when at least one source is created.\n"
        "Requires scope `hosts:write`.\n"
        "Errors: 400 for host types without shares or `max_parallel_scanners` outside 1-16; 404 "
        "if the host does not exist."
    ),
)
async def add_host_shares(
    host_id: Annotated[uuid.UUID, Path(description="Host id.")],
    body: AddSharesRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("hosts:write"),
):
    """Batch-create Source rows attached to this host. Each entry's
    `share` value populates the per-type share-shaped key
    (`share` / `export_path` / `bucket`); credentials live on the Host
    and aren't duplicated. Names that collide with existing sources
    are skipped — the response reports created/skipped counts so the
    caller can show "Added 3 of 5" in the UI.
    """
    user = principal.user
    host = (await db.execute(
        select(Host).where(Host.id == host_id)
    )).scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")
    share_key = _SHARE_KEY_BY_TYPE.get(host.type)
    if share_key is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"add-shares does not support host type {host.type!r}; "
                "use the Add Source form to attach a single source."
            ),
        )
    if body.max_parallel_scanners is not None and not (
        1 <= int(body.max_parallel_scanners) <= 16
    ):
        raise HTTPException(
            status_code=400,
            detail="max_parallel_scanners must be between 1 and 16",
        )

    created_ids: list[uuid.UUID] = []
    skipped = 0
    for item in body.shares:
        source = Source(
            name=item.name,
            type=host.type,
            host_id=host.id,
            connection_config={share_key: item.share},
            scan_schedule=body.scan_schedule,
            exclude_patterns=body.exclude_patterns,
            preferred_pool=body.preferred_pool,
            max_parallel_scanners=(
                int(body.max_parallel_scanners)
                if body.max_parallel_scanners is not None else None
            ),
            is_removable=(
                body.is_removable
                if body.is_removable is not None
                # Network sources default to false; we mirror the
                # default-inference helper rather than hard-coding false
                # so a future is_removable-by-type rule applies.
                else infer_is_removable(host.type, {share_key: item.share})
            ),
        )
        db.add(source)
        try:
            await db.flush()
            created_ids.append(source.id)
        except IntegrityError:
            await db.rollback()
            skipped += 1
            # Re-fetch the host so the next loop iteration has a clean
            # session. Without this the next flush fails with "session
            # is closed" / "object expired".
            host = (await db.execute(
                select(Host).where(Host.id == host_id)
            )).scalar_one_or_none()
            if host is None:
                raise HTTPException(status_code=404, detail="Host vanished mid-batch")
    await db.commit()

    if created_ids:
        await record_event(
            db=db, user=user, event_type="host_shares_batch_added",
            request=request,
            payload={
                "host_id": str(host_id),
                "created": len(created_ids),
                "skipped": skipped,
                "source_ids": [str(i) for i in created_ids],
            },
        )
    return AddSharesResponse(
        created=len(created_ids), skipped=skipped, sources=created_ids,
    )
