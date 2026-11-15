"""Scanner discovery (pending-claim) flow.

Layout:
  POST /api/v1/scanners/discover                — scanner posts (no auth, gated)
  GET  /api/v1/scanners/discover/{id}           — scanner long-polls (no auth)
  GET  /api/v1/scanner-discovery-requests       — admin lists pending + recent
  POST /api/v1/scanner-discovery-requests/{id}/approve — admin
  POST /api/v1/scanner-discovery-requests/{id}/deny    — admin

The long-poll endpoint subscribes to the `scanners` pubsub channel
for up to ~25s so an admin's approve/deny click is visible to the
scanner within ~50ms instead of waiting for the next 5s poll.
"""
from __future__ import annotations

from typing import Annotated
import asyncio
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

from akashic.schemas.ws_events import WsScannerDiscoveryApproved, WsScannerDiscoveryDenied, WsScannerDiscoveryRequested
from akashic.routing import ApiRouter
from fastapi import Depends, HTTPException, Path, Request, status
from akashic.schemas.base import ApiModel
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import Principal, require_scope
from akashic.database import get_db
from akashic.openapi import SCANNER_TAG
from akashic.models.scanner import Scanner
from akashic.models.scanner_discovery_request import ScannerDiscoveryRequest
from akashic.models.user import User
from akashic.protocol import PROTOCOL_VERSION
from akashic.services import scan_pubsub
from akashic.services.audit import record_event
from akashic.services.rate_limit import make_limiter
from akashic.services.scanner_keys import fingerprint_of_pem
from akashic.services.server_settings import is_discovery_enabled

logger = logging.getLogger(__name__)

router = ApiRouter()

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
_discover_limiter = make_limiter(
    max_requests=5,
    window_seconds=60.0,
    message="too many discovery requests; try again shortly",
)


def _generate_pairing_code() -> str:
    """8 chars from the Crockford base32 alphabet (no I/L/O/U), formatted
    `ABCD-EFGH`. The hyphen is for human readability; the code stored
    on disk is the formatted form so log/UI comparisons match."""
    alphabet = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


async def _check_rate_limit(request: Request) -> None:
    _discover_limiter.check(request)


# ── Schemas ──────────────────────────────────────────────────────────────


class DiscoverRequest(ApiModel):
    public_key_pem: str = Field(min_length=1, description="The scanner's freshly generated Ed25519 public key, PEM encoded.")
    hostname: str | None = Field(default=None, max_length=255, description="Scanner host name, shown to the approving admin.")
    agent_version: str | None = Field(default=None, max_length=32, description="Agent build version.")
    requested_pool: str | None = Field(default=None, max_length=64, description="Pool the scanner asks to join; the admin decides the actual pool.")

    model_config = {"json_schema_extra": {'examples': [{'public_key_pem': '-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEA...\n-----END PUBLIC KEY-----\n', 'hostname': 'scanner01.example.com', 'agent_version': '0.48.0', 'requested_pool': 'default'}]}}


class DiscoverResponse(ApiModel):
    discovery_id: uuid.UUID = Field(description="Id to poll for the outcome.")
    pairing_code: str = Field(description="Short code (`ABCD-EFGH`) to display so the admin can match the request to this host.")
    expires_at: datetime = Field(description="When the request lapses if undecided.")
    poll_after_seconds: int = Field(description="Suggested delay before polling the status endpoint.")


class DiscoverStatus(ApiModel):
    status: str = Field(description="`pending`, `approved`, `denied` or `expired`.")
    expires_at: datetime | None = Field(default=None, description="While pending: when the request lapses.")
    scanner_id: uuid.UUID | None = Field(default=None, description="When approved: id of the registered scanner (the `kid` for signing JWTs).")
    name: str | None = Field(default=None, description="When approved: scanner name.")
    pool: str | None = Field(default=None, description="When approved: scanner pool.")
    server_protocol_version: int | None = Field(default=None, description="When approved: the protocol version the server speaks.")
    deny_reason: str | None = Field(default=None, description="When denied: the admin's reason, if given.")


class DiscoveryAdminSummary(ApiModel):
    id: uuid.UUID = Field(description="Discovery request id.")
    pairing_code: str = Field(description="Pairing code the scanner displays.")
    hostname: str | None = Field(description="Scanner host name.")
    agent_version: str | None = Field(description="Agent build version.")
    requested_pool: str | None = Field(description="Pool the scanner asked for.")
    requested_at: datetime = Field(description="When the request was made.")
    expires_at: datetime = Field(description="When the request lapses if undecided.")
    status: str = Field(description="`pending`, `approved`, `denied` or `expired`.")
    decided_at: datetime | None = Field(description="When an admin approved or denied it.")
    deny_reason: str | None = Field(description="Reason given on denial.")
    approved_scanner_id: uuid.UUID | None = Field(description="Scanner created on approval.")
    key_fingerprint: str = Field(description="Fingerprint of the scanner's public key; compare it with the one shown on the host.")


class ApproveBody(ApiModel):
    name: str = Field(min_length=1, max_length=64, description="Name for the new scanner; must be unique.", examples=['nas-scanner-1'])
    pool: str = Field(default="default", min_length=1, max_length=64, description="Pool the scanner joins.")

    model_config = {"json_schema_extra": {'examples': [{'name': 'nas-scanner-1', 'pool': 'default'}]}}


class DiscoveryApproved(ApiModel):
    """The scanner created for an approved discovery request."""

    scanner_id: uuid.UUID = Field(description="Id of the newly registered scanner.")
    name: str = Field(description="Scanner name.")
    pool: str = Field(description="Scanner pool.")


class DenyBody(ApiModel):
    reason: str | None = Field(default=None, max_length=255, description="Reason shown to the scanner.")

    model_config = {"json_schema_extra": {'examples': [{'reason': 'Unknown host'}]}}


# ── Endpoints ────────────────────────────────────────────────────────────


@router.post(
    "/scanners/discover",
    response_model=DiscoverResponse,
    status_code=status.HTTP_201_CREATED,
    tags=[SCANNER_TAG],
    summary="Request scanner enrolment",
    description=(
        "Scanner protocol, enrolment by approval. An unenrolled scanner posts its freshly generated "
        "public key and receives a pairing code to show the operator; an admin approves or denies it "
        "in the UI (`POST /scanner-discovery-requests/{id}/approve`). Poll `GET "
        "/scanners/discover/{discovery_id}` for the outcome. A second request with the same key while "
        "one is pending returns the existing request. Requests expire after 15 minutes. Records a "
        "`scanner_discovery_requested` audit event.\n"
        "Public (the approval is the gate), rate limited to 5 requests per minute per client, and "
        "disabled by default: it answers 404 unless discovery is enabled in the server settings.\n"
        "Errors: 400 if `public_key_pem` is not a valid Ed25519 public key; 404 if discovery is "
        "disabled; 422 for a malformed body; 429 when rate limited."
    ),
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

    await scan_pubsub.publish_scanner_event(WsScannerDiscoveryRequested(
        discovery_id=str(row.id),
        pairing_code=row.pairing_code,
        hostname=row.hostname,
        agent_version=row.agent_version,
        requested_pool=row.requested_pool,
        expires_at=row.expires_at.isoformat(),
        key_fingerprint=row.key_fingerprint,
    ))
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
    "/scanners/discover/{discovery_id}",
    response_model=DiscoverStatus,
    tags=[SCANNER_TAG],
    summary="Poll a discovery request",
    description=(
        "Scanner protocol. Long-polls for the outcome of a discovery request: returns at once when it "
        "is no longer pending, otherwise waits up to about 25 seconds for the admin's decision and "
        "then answers with the current state. `status` is `pending`, `approved` (with `scanner_id`, "
        "`name`, `pool` and the server protocol version: continue with the handshake), `denied` (with "
        "`deny_reason`) or `expired`. A pending request past its expiry is marked `expired`.\n"
        "Public: the unguessable `discovery_id` is the credential. Answers 404 while discovery is "
        "disabled.\n"
        "Errors: 404 if discovery is disabled or the request does not exist."
    ),
)
async def discover_status(
    discovery_id: Annotated[uuid.UUID, Path(description="Id returned when the request was created.")],
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
    # Redis being unreachable degrades to "return immediately with the
    # current state" — the scanner just polls more often, no breakage.
    target_id = str(discovery_id)
    deadline = time.monotonic() + _LONG_POLL_SECONDS

    async def _wait() -> None:
        try:
            async for event in scan_pubsub.subscribe_scanners():
                kind = event.get("kind") if isinstance(event, dict) else None
                if not isinstance(kind, str):
                    continue
                if not kind.startswith("scanner.discovery_"):
                    continue
                if event.get("discovery_id") == target_id:
                    return
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "discover_status long-poll: pubsub unavailable, "
                "falling back to immediate response: %s", exc,
            )

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
    "/scanner-discovery-requests",
    response_model=list[DiscoveryAdminSummary],
    tags=["scanners"],
    summary="List scanner discovery requests",
    description=(
        "Pending requests plus those decided in the last 24 hours, newest first, each with its "
        "pairing code and the key fingerprint to compare with the scanner host.\n"
        "Requires scope `scanners:read`. Not available to source-limited tokens."
    ),
)
async def list_discovery_requests(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("scanners:read"),
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
    "/scanner-discovery-requests/{discovery_id}/approve",
    response_model=DiscoveryApproved,
    tags=["scanners"],
    summary="Approve a scanner discovery request",
    description=(
        "Registers the requesting scanner under the given name and pool and tells the waiting scanner "
        "it was approved. The scanner is created with no source or scan-type restriction; use `PATCH "
        "/scanners/{scanner_id}` to restrict it. Records a `scanner_discovery_approved` audit event.\n"
        "Requires scope `scanners:write`. Errors: 404 if the request does not exist; 409 if the "
        "scanner name is in use; 410 if the request was already decided or has expired; 422 for a "
        "malformed body."
    ),
)
async def approve_discovery(
    discovery_id: Annotated[uuid.UUID, Path(description="Discovery request to approve.")],
    body: ApproveBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("scanners:write"),
):
    user = principal.user
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

    await scan_pubsub.publish_scanner_event(WsScannerDiscoveryApproved(
        discovery_id=str(row.id),
        scanner_id=str(scanner.id),
        scanner_name=scanner.name,
        pool=scanner.pool,
    ))
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
    "/scanner-discovery-requests/{discovery_id}/deny",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["scanners"],
    summary="Deny a scanner discovery request",
    description=(
        "Rejects a pending request, optionally with a reason the scanner sees. Records a "
        "`scanner_discovery_denied` audit event.\n"
        "Requires scope `scanners:write`. Errors: 404 if the request does not exist; 410 if it was "
        "already decided or has expired; 422 for a malformed body."
    ),
)
async def deny_discovery(
    discovery_id: Annotated[uuid.UUID, Path(description="Discovery request to deny.")],
    body: DenyBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("scanners:write"),
):
    user = principal.user
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
    await scan_pubsub.publish_scanner_event(WsScannerDiscoveryDenied(
        discovery_id=str(row.id),
        deny_reason=row.deny_reason,
    ))
    await record_event(
        db=db, user=user, event_type="scanner_discovery_denied",
        request=request,
        payload={"discovery_id": str(row.id), "reason": row.deny_reason},
    )
