"""Scanner discovery (pending-claim) flow.

Layout:
  POST /api/scanners/discover                — scanner posts (no auth, gated)
  GET  /api/scanners/discover/{id}           — scanner long-polls (no auth)
  GET  /api/scanner-discovery-requests       — admin lists pending + recent
  POST /api/scanner-discovery-requests/{id}/approve — admin
  POST /api/scanner-discovery-requests/{id}/deny    — admin

The long-poll endpoint subscribes to the `scanners` pubsub channel
for up to ~25s so an admin's approve/deny click is visible to the
scanner within ~50ms instead of waiting for the next 5s poll.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import require_admin
from akashic.database import get_db
from akashic.models.scanner import Scanner
from akashic.models.scanner_discovery_request import ScannerDiscoveryRequest
from akashic.models.user import User
from akashic.protocol import PROTOCOL_VERSION
from akashic.services import scan_pubsub
from akashic.services.audit import record_event
from akashic.services.scanner_keys import fingerprint_of_pem
from akashic.services.server_settings import is_discovery_enabled

logger = logging.getLogger(__name__)

router = APIRouter(tags=["scanner-discovery"])

# 15-minute pending window — longer than typical "I just clicked
# `docker compose up`" → "I noticed it in the UI" gap, short enough
# that abandoned discoveries don't pile up.
_DISCOVERY_TTL_MINUTES = 15
# Long-poll window. Slightly under typical reverse-proxy idle (60s)
# so we close cleanly before nginx/traefik cuts the socket.
_LONG_POLL_SECONDS = 25
# Per-IP rate limit on POST /discover. Five attempts per minute is
# generous for a real scanner (it'd never need to retry that fast)
# while making a flood-the-pending-queue attack tedious.
_RATE_LIMIT_REQUESTS = 5
_RATE_LIMIT_WINDOW_S = 60.0
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)


def _generate_pairing_code() -> str:
    """8 chars from the Crockford base32 alphabet (no I/L/O/U), formatted
    `ABCD-EFGH`. The hyphen is for human readability; the code stored
    on disk is the formatted form so log/UI comparisons match."""
    alphabet = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


async def _check_rate_limit(request: Request) -> None:
    client = request.client
    ip = client.host if client else "unknown"
    now = time.monotonic()
    bucket = _rate_buckets[ip]
    while bucket and bucket[0] < now - _RATE_LIMIT_WINDOW_S:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="too many discovery requests; try again shortly",
        )
    bucket.append(now)


# ── Schemas ──────────────────────────────────────────────────────────────


class DiscoverRequest(BaseModel):
    public_key_pem: str = Field(min_length=1)
    hostname: str | None = Field(default=None, max_length=255)
    agent_version: str | None = Field(default=None, max_length=32)
    requested_pool: str | None = Field(default=None, max_length=64)


class DiscoverResponse(BaseModel):
    discovery_id: uuid.UUID
    pairing_code: str
    expires_at: datetime
    poll_after_seconds: int


class DiscoverStatus(BaseModel):
    status: str  # pending | approved | denied | expired
    expires_at: datetime | None = None
    scanner_id: uuid.UUID | None = None
    name: str | None = None
    pool: str | None = None
    server_protocol_version: int | None = None
    deny_reason: str | None = None


class DiscoveryAdminSummary(BaseModel):
    id: uuid.UUID
    pairing_code: str
    hostname: str | None
    agent_version: str | None
    requested_pool: str | None
    requested_at: datetime
    expires_at: datetime
    status: str
    decided_at: datetime | None
    deny_reason: str | None
    approved_scanner_id: uuid.UUID | None
    key_fingerprint: str


class ApproveBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    pool: str = Field(default="default", min_length=1, max_length=64)


class DenyBody(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


# ── Endpoints ────────────────────────────────────────────────────────────


@router.post(
    "/api/scanners/discover",
    response_model=DiscoverResponse,
    status_code=status.HTTP_201_CREATED,
)
async def discover_scanner(
    body: DiscoverRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not await is_discovery_enabled(db):
        # Hide the endpoint entirely when disabled — prevents
        # fingerprinting + signals to a misconfigured scanner that
        # discovery isn't an option here.
        raise HTTPException(status_code=404, detail="not found")
    await _check_rate_limit(request)
    try:
        fp = fingerprint_of_pem(body.public_key_pem)
    except ValueError as err:
        raise HTTPException(
            status_code=400, detail=f"invalid public_key_pem: {err}",
        )

    # Idempotent upsert: one pending row per public key. A scanner
    # restarting mid-discovery sees its existing pairing code, not a
    # fresh one (avoids confusing the operator who already noted the
    # code from the first attempt).
    existing = (await db.execute(
        select(ScannerDiscoveryRequest).where(
            ScannerDiscoveryRequest.key_fingerprint == fp,
            ScannerDiscoveryRequest.status == "pending",
        )
    )).scalar_one_or_none()
    if existing is not None:
        return DiscoverResponse(
            discovery_id=existing.id,
            pairing_code=existing.pairing_code,
            expires_at=existing.expires_at,
            poll_after_seconds=5,
        )

    row = ScannerDiscoveryRequest(
        public_key_pem=body.public_key_pem,
        key_fingerprint=fp,
        pairing_code=_generate_pairing_code(),
        hostname=body.hostname,
        agent_version=body.agent_version,
        requested_pool=body.requested_pool,
        expires_at=datetime.now(timezone.utc)
        + timedelta(minutes=_DISCOVERY_TTL_MINUTES),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    await scan_pubsub.publish_scanner_event({
        "kind": "scanner.discovery_requested",
        "discovery_id": str(row.id),
        "pairing_code": row.pairing_code,
        "hostname": row.hostname,
        "agent_version": row.agent_version,
        "requested_pool": row.requested_pool,
        "expires_at": row.expires_at.isoformat(),
        "key_fingerprint": row.key_fingerprint,
    })
    await record_event(
        db=db, user=None, event_type="scanner_discovery_requested",
        request=request,
        payload={
            "discovery_id": str(row.id),
            "hostname": row.hostname,
            "agent_version": row.agent_version,
            "key_fingerprint": row.key_fingerprint,
        },
    )
    return DiscoverResponse(
        discovery_id=row.id,
        pairing_code=row.pairing_code,
        expires_at=row.expires_at,
        poll_after_seconds=5,
    )


def _to_status(row: ScannerDiscoveryRequest, scanner: Scanner | None) -> DiscoverStatus:
    if row.status == "approved":
        return DiscoverStatus(
            status="approved",
            scanner_id=row.approved_scanner_id,
            name=scanner.name if scanner else None,
            pool=scanner.pool if scanner else None,
            server_protocol_version=PROTOCOL_VERSION,
        )
    if row.status == "denied":
        return DiscoverStatus(status="denied", deny_reason=row.deny_reason)
    if row.status == "expired":
        return DiscoverStatus(status="expired")
    return DiscoverStatus(status="pending", expires_at=row.expires_at)


@router.get(
    "/api/scanners/discover/{discovery_id}",
    response_model=DiscoverStatus,
)
async def discover_status(
    discovery_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Long-poll for the discovery's terminal state. Returns
    immediately when the row is already terminal; otherwise waits up
    to ~25s on the pubsub for a matching event before falling back to
    a fresh DB read.

    The endpoint is unauthenticated because the discovery_id is
    effectively a per-request secret (UUID v4, 122 bits of entropy)
    and the response only carries information the requesting scanner
    already needs to know to function. We do NOT short-circuit on
    "row not found" — discovery rows are real data.
    """
    if not await is_discovery_enabled(db):
        raise HTTPException(status_code=404, detail="not found")
    row = (await db.execute(
        select(ScannerDiscoveryRequest).where(
            ScannerDiscoveryRequest.id == discovery_id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="discovery not found")

    if row.status != "pending" or row.expires_at <= datetime.now(timezone.utc):
        if row.status == "pending":
            row.status = "expired"
            await db.commit()
        scanner = None
        if row.approved_scanner_id is not None:
            scanner = (await db.execute(
                select(Scanner).where(Scanner.id == row.approved_scanner_id)
            )).scalar_one_or_none()
        return _to_status(row, scanner)

    # Long-poll: wait on pubsub for a relevant event for at most
    # _LONG_POLL_SECONDS, then return the current state regardless.
    target_id = str(discovery_id)
    deadline = time.monotonic() + _LONG_POLL_SECONDS

    async def _wait() -> None:
        async for event in scan_pubsub.subscribe_scanners():
            kind = event.get("kind") if isinstance(event, dict) else None
            if not isinstance(kind, str):
                continue
            if not kind.startswith("scanner.discovery_"):
                continue
            if event.get("discovery_id") == target_id:
                return

    try:
        await asyncio.wait_for(
            _wait(),
            timeout=max(0.0, deadline - time.monotonic()),
        )
    except asyncio.TimeoutError:
        pass

    await db.refresh(row)
    scanner = None
    if row.approved_scanner_id is not None:
        scanner = (await db.execute(
            select(Scanner).where(Scanner.id == row.approved_scanner_id)
        )).scalar_one_or_none()
    return _to_status(row, scanner)


@router.get(
    "/api/scanner-discovery-requests",
    response_model=list[DiscoveryAdminSummary],
)
async def list_discovery_requests(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Pending + recently-decided (last 24h) requests for the admin
    pane. The pane shows pending first; decided rows give context
    ('I approved this 5 minutes ago')."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = (await db.execute(
        select(ScannerDiscoveryRequest)
        .where(
            (ScannerDiscoveryRequest.status == "pending")
            | (ScannerDiscoveryRequest.decided_at >= cutoff)
        )
        .order_by(ScannerDiscoveryRequest.requested_at.desc())
    )).scalars().all()
    return [
        DiscoveryAdminSummary(
            id=r.id, pairing_code=r.pairing_code,
            hostname=r.hostname, agent_version=r.agent_version,
            requested_pool=r.requested_pool,
            requested_at=r.requested_at, expires_at=r.expires_at,
            status=r.status, decided_at=r.decided_at,
            deny_reason=r.deny_reason,
            approved_scanner_id=r.approved_scanner_id,
            key_fingerprint=r.key_fingerprint,
        )
        for r in rows
    ]


def _terminal_or_404(row: ScannerDiscoveryRequest | None) -> ScannerDiscoveryRequest:
    if row is None:
        raise HTTPException(status_code=404, detail="discovery not found")
    if row.status != "pending":
        raise HTTPException(
            status_code=410,
            detail=f"discovery already {row.status}",
        )
    if row.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="discovery has expired")
    return row


@router.post(
    "/api/scanner-discovery-requests/{discovery_id}/approve",
)
async def approve_discovery(
    discovery_id: uuid.UUID,
    body: ApproveBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    row = _terminal_or_404((await db.execute(
        select(ScannerDiscoveryRequest).where(
            ScannerDiscoveryRequest.id == discovery_id,
        )
    )).scalar_one_or_none())

    # Defensive name uniqueness — operator-supplied names collide
    # easily ("scanner-1" twice).
    name_clash = (await db.execute(
        select(Scanner).where(Scanner.name == body.name)
    )).scalar_one_or_none()
    if name_clash is not None:
        raise HTTPException(
            status_code=409, detail="scanner name already in use",
        )

    scanner = Scanner(
        name=body.name,
        pool=body.pool,
        public_key_pem=row.public_key_pem,
        key_fingerprint=row.key_fingerprint,
        hostname=row.hostname,
        version=row.agent_version,
    )
    db.add(scanner)
    await db.flush()

    row.status = "approved"
    row.decided_at = datetime.now(timezone.utc)
    row.decided_by_user_id = user.id
    row.approved_scanner_id = scanner.id
    await db.commit()
    await db.refresh(scanner)

    await scan_pubsub.publish_scanner_event({
        "kind": "scanner.discovery_approved",
        "discovery_id": str(row.id),
        "scanner_id": str(scanner.id),
        "scanner_name": scanner.name,
        "pool": scanner.pool,
    })
    await record_event(
        db=db, user=user, event_type="scanner_discovery_approved",
        request=request,
        payload={
            "discovery_id": str(row.id),
            "scanner_id": str(scanner.id),
            "name": scanner.name,
            "pool": scanner.pool,
        },
    )
    return {"scanner_id": str(scanner.id), "name": scanner.name, "pool": scanner.pool}


@router.post(
    "/api/scanner-discovery-requests/{discovery_id}/deny",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def deny_discovery(
    discovery_id: uuid.UUID,
    body: DenyBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    row = _terminal_or_404((await db.execute(
        select(ScannerDiscoveryRequest).where(
            ScannerDiscoveryRequest.id == discovery_id,
        )
    )).scalar_one_or_none())
    row.status = "denied"
    row.decided_at = datetime.now(timezone.utc)
    row.decided_by_user_id = user.id
    row.deny_reason = body.reason
    await db.commit()
    await scan_pubsub.publish_scanner_event({
        "kind": "scanner.discovery_denied",
        "discovery_id": str(row.id),
        "deny_reason": row.deny_reason,
    })
    await record_event(
        db=db, user=user, event_type="scanner_discovery_denied",
        request=request,
        payload={"discovery_id": str(row.id), "reason": row.deny_reason},
    )
