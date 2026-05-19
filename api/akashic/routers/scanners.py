"""Scanner registration + handshake + lease + complete endpoints.

Two trust boundaries:
  - admin endpoints (CRUD): user JWT + `require_admin` dep
  - agent endpoints (handshake / heartbeat / lease / complete):
    scanner JWT signed with the scanner's Ed25519 private key, verified
    against the registered public key in the `scanners` row.

The lease endpoint atomically claims one pending scan via a single
SELECT … FOR UPDATE SKIP LOCKED → UPDATE … RETURNING round-trip; this
serialises concurrent leases without advisory locks.

Phase 1 ships these alongside the existing subprocess-spawn flow —
Phase 3 deletes the spawn path.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator

from akashic.services.rate_limit import make_limiter
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import require_admin
from akashic.auth.jwt import create_ingest_token
from akashic.database import get_db
from akashic.models.scan import Scan
from akashic.models.scanner import Scanner
from akashic.models.source import Source
from akashic.models.user import User
from akashic.protocol import ACCEPTED_MAX, ACCEPTED_MIN, PROTOCOL_VERSION
from akashic.schemas.reachability import ProbeReport
from akashic.services.scanner_auth import verify_scanner_jwt
from akashic.services.scanner_keys import generate_keypair
from akashic.services.source_config import (
    effective_chunk_size,
    effective_max_parallel_scanners,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["scanners"])


# ── Schemas ──────────────────────────────────────────────────────────────


_ALLOWED_SCAN_TYPES = {"incremental", "full"}


class ScannerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    pool: str = Field(default="default", min_length=1, max_length=64)


class ScannerPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    pool: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None
    # Sentinels: omitted = leave unchanged; explicit `null` (via the
    # `clear_*` flags below) = clear back to "unrestricted".
    allowed_source_ids: list[uuid.UUID] | None = None
    allowed_scan_types: list[str] | None = None
    clear_allowed_source_ids: bool = False
    clear_allowed_scan_types: bool = False


class ScannerCreated(BaseModel):
    """Returned ONCE on /POST /api/scanners. The private_key_pem field
    isn't persisted on the api side — copy it now or rotate later."""

    id: uuid.UUID
    name: str
    pool: str
    public_key_pem: str
    private_key_pem: str
    key_fingerprint: str
    protocol_version: int


class ScannerSummary(BaseModel):
    id: uuid.UUID
    name: str
    pool: str
    key_fingerprint: str
    hostname: str | None
    version: str | None
    protocol_version: int | None
    registered_at: datetime
    last_seen_at: datetime | None
    enabled: bool
    online: bool
    allowed_source_ids: list[uuid.UUID] | None = None
    allowed_scan_types: list[str] | None = None

    model_config = {"from_attributes": True}


class HandshakeRequest(BaseModel):
    protocol_version: int
    version: str | None = None
    hostname: str | None = None


class HandshakeResponse(BaseModel):
    accepted: bool
    server_protocol_version: int
    accepted_min: int
    accepted_max: int
    reason: str | None = None


class LeasedSource(BaseModel):
    id: uuid.UUID
    type: str
    connection_config: dict
    exclude_patterns: list[str] | None = None
    # Cap on cooperating scanners per scan (default 1). Agents that
    # support unit-coordinated scanning use this to switch between the
    # legacy single-walker path and the work-units lease loop. Older
    # agents simply ignore the field. v0.35.0 — this is the *resolved*
    # value (source ?? host ?? 1); the agent receives a concrete int and
    # never has to know about host inheritance.
    max_parallel_scanners: int = 1
    # v0.35.0 — work-unit entry budget (resolved source ?? host ?? 2000).
    # The agent passes it to scanner.Run as the shallow-split budget;
    # an older API that omits the field leaves it 0 and the scanner
    # falls back to its own default.
    scan_chunk_size: int = 2000


class LeasedScan(BaseModel):
    scan_id: uuid.UUID
    scan_type: str
    source: LeasedSource
    api_jwt: str | None
    """Short-lived user JWT for the agent to use on /api/ingest/batch
    and /api/scans/{id}/heartbeat. None if the api couldn't determine
    a default user (no admin found) — agents treat this as a 5xx."""


# ── Online-ness ──────────────────────────────────────────────────────────

# A scanner that's checked in within this window is shown as online in
# the admin UI. Heartbeats fire every 30s by default; 90s gives two
# missed heartbeats of grace before flipping offline.
ONLINE_WINDOW_SECONDS = 90


def _is_online(scanner: Scanner) -> bool:
    if scanner.last_seen_at is None:
        return False
    age = datetime.now(timezone.utc) - scanner.last_seen_at
    return age.total_seconds() < ONLINE_WINDOW_SECONDS


def _to_summary(s: Scanner) -> ScannerSummary:
    return ScannerSummary(
        id=s.id, name=s.name, pool=s.pool,
        key_fingerprint=s.key_fingerprint,
        hostname=s.hostname, version=s.version,
        protocol_version=s.protocol_version,
        registered_at=s.registered_at,
        last_seen_at=s.last_seen_at,
        enabled=s.enabled,
        online=_is_online(s),
        allowed_source_ids=s.allowed_source_ids,
        allowed_scan_types=s.allowed_scan_types,
    )


# ── Admin CRUD ───────────────────────────────────────────────────────────


@router.post(
    "/api/scanners",
    response_model=ScannerCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_scanner(
    body: ScannerCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Mint a new scanner. The api generates the keypair, stores only
    the public key, and returns the private key once for the admin to
    deliver to the scanner host."""
    existing = await db.execute(select(Scanner).where(Scanner.name == body.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="scanner name already in use")

    kp = generate_keypair()
    scanner = Scanner(
        name=body.name,
        pool=body.pool,
        public_key_pem=kp.public_pem,
        key_fingerprint=kp.fingerprint,
    )
    db.add(scanner)
    await db.commit()
    await db.refresh(scanner)
    return ScannerCreated(
        id=scanner.id,
        name=scanner.name,
        pool=scanner.pool,
        public_key_pem=kp.public_pem,
        private_key_pem=kp.private_pem,
        key_fingerprint=kp.fingerprint,
        protocol_version=PROTOCOL_VERSION,
    )


@router.get("/api/scanners", response_model=list[ScannerSummary])
async def list_scanners(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    rows = (await db.execute(select(Scanner).order_by(Scanner.name))).scalars().all()
    return [_to_summary(s) for s in rows]


class ScannerCounts(BaseModel):
    """Lightweight count summary for the Sources page banner — avoids
    re-fetching the whole scanner list per page mount."""

    registered: int
    online: int


@router.get("/api/scanners/summary", response_model=ScannerCounts)
async def scanners_summary(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    rows = (await db.execute(select(Scanner))).scalars().all()
    return ScannerCounts(
        registered=len(rows),
        online=sum(1 for s in rows if _is_online(s)),
    )


@router.patch("/api/scanners/{scanner_id}", response_model=ScannerSummary)
async def patch_scanner(
    scanner_id: uuid.UUID,
    body: ScannerPatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    scanner = (await db.execute(
        select(Scanner).where(Scanner.id == scanner_id)
    )).scalar_one_or_none()
    if scanner is None:
        raise HTTPException(status_code=404, detail="scanner not found")
    if body.name is not None:
        scanner.name = body.name
    if body.pool is not None:
        scanner.pool = body.pool
    if body.enabled is not None:
        scanner.enabled = body.enabled
    if body.allowed_source_ids is not None:
        await _validate_source_ids(db, body.allowed_source_ids)
        scanner.allowed_source_ids = body.allowed_source_ids
    elif body.clear_allowed_source_ids:
        scanner.allowed_source_ids = None
    if body.allowed_scan_types is not None:
        _validate_scan_types(body.allowed_scan_types)
        scanner.allowed_scan_types = body.allowed_scan_types
    elif body.clear_allowed_scan_types:
        scanner.allowed_scan_types = None
    await db.commit()
    await db.refresh(scanner)
    return _to_summary(scanner)


def _validate_scan_types(types: list[str]) -> None:
    bad = [t for t in types if t not in _ALLOWED_SCAN_TYPES]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown scan_type(s) {bad!r}; allowed: "
                f"{sorted(_ALLOWED_SCAN_TYPES)}"
            ),
        )


async def _validate_source_ids(
    db: AsyncSession, source_ids: list[uuid.UUID],
) -> None:
    if not source_ids:
        return
    res = await db.execute(
        select(Source.id).where(Source.id.in_(source_ids))
    )
    found = {row[0] for row in res.all()}
    missing = [str(s) for s in source_ids if s not in found]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"unknown source_id(s): {missing}",
        )


@router.post(
    "/api/scanners/{scanner_id}/rotate",
    response_model=ScannerCreated,
)
async def rotate_scanner(
    scanner_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Re-issue the keypair. The previous private key stops
    authenticating immediately because we replace public_key_pem +
    key_fingerprint atomically before responding."""
    scanner = (await db.execute(
        select(Scanner).where(Scanner.id == scanner_id)
    )).scalar_one_or_none()
    if scanner is None:
        raise HTTPException(status_code=404, detail="scanner not found")
    kp = generate_keypair()
    scanner.public_key_pem = kp.public_pem
    scanner.key_fingerprint = kp.fingerprint
    await db.commit()
    return ScannerCreated(
        id=scanner.id,
        name=scanner.name,
        pool=scanner.pool,
        public_key_pem=kp.public_pem,
        private_key_pem=kp.private_pem,
        key_fingerprint=kp.fingerprint,
        protocol_version=PROTOCOL_VERSION,
    )


@router.delete(
    "/api/scanners/{scanner_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_scanner(
    scanner_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Delete the scanner. The FK on `scans.assigned_scanner_id` is
    `ON DELETE SET NULL`, so any in-flight lease drops back to
    unassigned and the next polling scanner picks it up."""
    scanner = (await db.execute(
        select(Scanner).where(Scanner.id == scanner_id)
    )).scalar_one_or_none()
    if scanner is None:
        raise HTTPException(status_code=404, detail="scanner not found")
    # Re-queue any in-flight lease this scanner was holding.
    await db.execute(
        text("""
            UPDATE scans SET status = 'pending',
                             assigned_scanner_id = NULL,
                             lease_expires_at = NULL
             WHERE assigned_scanner_id = :sid
               AND status IN ('pending', 'running')
        """),
        {"sid": scanner_id},
    )
    await db.delete(scanner)
    await db.commit()


# ── Join tokens (self-registration) ──────────────────────────────────────


class ClaimTokenCreate(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    pool: str = Field(default="default", min_length=1, max_length=64)
    ttl_minutes: int = Field(default=60, ge=1, le=60 * 24 * 30)
    allowed_source_ids: list[uuid.UUID] | None = None
    allowed_scan_types: list[str] | None = None


class ClaimTokenCreated(BaseModel):
    """Returned ONCE on POST /api/scanner-claim-tokens. The plaintext
    `token` field isn't persisted on the api side (we keep only its
    sha256 hash) — copy it now or revoke and regenerate."""

    id: uuid.UUID
    label: str
    pool: str
    allowed_source_ids: list[uuid.UUID] | None
    allowed_scan_types: list[str] | None
    token: str
    expires_at: datetime
    snippets: dict[str, str]


class ClaimTokenSummary(BaseModel):
    id: uuid.UUID
    label: str
    pool: str
    allowed_source_ids: list[uuid.UUID] | None
    allowed_scan_types: list[str] | None
    status: str  # "active" | "used" | "expired"
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    used_by_scanner_id: uuid.UUID | None


_HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9.\-]{0,253}[a-zA-Z0-9])?$")

# Per-IP rate limit on POST /api/scanners/claim (review A-I5). Five
# attempts per minute is generous for a real scanner (it'd never need
# to retry that fast) but throttles brute-force / token-spray traffic.
_claim_limiter = make_limiter(
    max_requests=5,
    window_seconds=60.0,
    message="too many claim attempts; try again shortly",
)


def _check_claim_rate_limit(request: Request) -> None:
    _claim_limiter.check(request)


class ClaimRequest(BaseModel):
    token: str = Field(min_length=8, max_length=128)
    public_key_pem: str = Field(min_length=1)
    hostname: str | None = Field(default=None, max_length=255)
    agent_version: str | None = Field(default=None, max_length=32)

    @field_validator("hostname")
    @classmethod
    def _validate_hostname(cls, v: str | None) -> str | None:
        # RFC1123-ish hostname check (review A-I5). Pre-fix the field
        # was accepted as any string up to 255 chars, including special
        # characters that could be a stored-injection risk in any
        # downstream renderer that doesn't escape (audit_event payload,
        # admin UI, terminal logs).
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        if not _HOSTNAME_RE.match(v):
            raise ValueError(
                "hostname must be RFC1123-compatible (alphanumeric, dots, hyphens)"
            )
        return v


class ClaimResponse(BaseModel):
    scanner_id: uuid.UUID
    name: str
    pool: str
    server_protocol_version: int


def _api_url_from_request(request: Request) -> str:
    """Best-effort api URL for snippet rendering. The browser always
    sees an X-Forwarded-Host / Origin in dev + prod; fall back to the
    request URL's scheme+netloc when those headers aren't present."""
    fwd_host = request.headers.get("x-forwarded-host")
    fwd_proto = request.headers.get("x-forwarded-proto")
    if fwd_host:
        proto = fwd_proto or request.url.scheme
        return f"{proto}://{fwd_host}".rstrip("/")
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/")
    return f"{request.url.scheme}://{request.url.netloc}".rstrip("/")


@router.post(
    "/api/scanner-claim-tokens",
    response_model=ClaimTokenCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_claim_token(
    body: ClaimTokenCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    if body.allowed_scan_types is not None:
        _validate_scan_types(body.allowed_scan_types)
    if body.allowed_source_ids is not None:
        await _validate_source_ids(db, body.allowed_source_ids)

    from akashic.services.scanner_claim import mint_token
    from akashic.services.scanner_snippets import render_snippets
    from akashic.services.audit import record_event

    plain, row = await mint_token(
        db=db,
        label=body.label,
        pool=body.pool,
        ttl_minutes=body.ttl_minutes,
        created_by_user_id=user.id,
        allowed_source_ids=body.allowed_source_ids,
        allowed_scan_types=body.allowed_scan_types,
    )
    await db.commit()
    await db.refresh(row)

    api_url = _api_url_from_request(request)
    snippets = render_snippets(api_url=api_url, token=plain, label=body.label)

    await record_event(
        db=db, user=user, event_type="scanner_claim_token_created",
        request=request,
        payload={
            "token_id": str(row.id),
            "label": row.label,
            "pool": row.pool,
            "expires_at": row.expires_at.isoformat(),
            "allowed_source_ids": [str(s) for s in (row.allowed_source_ids or [])] or None,
            "allowed_scan_types": row.allowed_scan_types,
        },
    )
    return ClaimTokenCreated(
        id=row.id,
        label=row.label,
        pool=row.pool,
        allowed_source_ids=row.allowed_source_ids,
        allowed_scan_types=row.allowed_scan_types,
        token=plain,
        expires_at=row.expires_at,
        snippets=snippets,
    )


@router.get(
    "/api/scanner-claim-tokens",
    response_model=list[ClaimTokenSummary],
)
async def list_claim_tokens(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    from akashic.models.scanner_claim_token import ScannerClaimToken
    from akashic.services.scanner_claim import derive_status

    rows = (
        await db.execute(
            select(ScannerClaimToken).order_by(ScannerClaimToken.created_at.desc())
        )
    ).scalars().all()
    return [
        ClaimTokenSummary(
            id=r.id, label=r.label, pool=r.pool,
            allowed_source_ids=r.allowed_source_ids,
            allowed_scan_types=r.allowed_scan_types,
            status=derive_status(r),
            created_at=r.created_at, expires_at=r.expires_at,
            used_at=r.used_at,
            used_by_scanner_id=r.used_by_scanner_id,
        )
        for r in rows
    ]


@router.delete(
    "/api/scanner-claim-tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_claim_token(
    token_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    from akashic.models.scanner_claim_token import ScannerClaimToken
    from akashic.services.audit import record_event

    row = (await db.execute(
        select(ScannerClaimToken).where(ScannerClaimToken.id == token_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="claim token not found")
    if row.used_at is not None:
        # Already used — revocation is a no-op against the lifecycle
        # but we surface a clear error so the UI can refresh its list.
        raise HTTPException(
            status_code=410, detail="claim token has already been used",
        )
    # Set expires_at to now() so the row is treated as 'expired' by
    # the list endpoint and rejected by the claim path. Keep the row
    # around for the audit trail.
    row.expires_at = datetime.now(timezone.utc)
    await db.commit()
    await record_event(
        db=db, user=user, event_type="scanner_claim_token_revoked",
        request=request,
        payload={"token_id": str(row.id), "label": row.label},
    )


@router.post(
    "/api/scanners/claim",
    response_model=ClaimResponse,
    status_code=status.HTTP_201_CREATED,
)
async def claim_with_token(
    body: ClaimRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Self-registration endpoint for a scanner host that has been
    handed a join token. No bearer auth — the token IS the auth.

    The scanner sends its own freshly-generated public key; the api
    creates the Scanner row with the token's pre-set scope and marks
    the token row used. The private key never reaches the server.
    """
    _check_claim_rate_limit(request)
    from akashic.services.scanner_claim import ClaimError, lookup_for_claim
    from akashic.services.scanner_keys import fingerprint_of_pem
    from akashic.services.audit import record_event

    try:
        token_row = await lookup_for_claim(db, body.token)
    except ClaimError as err:
        raise HTTPException(status_code=err.status_code, detail=str(err))

    try:
        fp = fingerprint_of_pem(body.public_key_pem)
    except ValueError as err:
        raise HTTPException(
            status_code=400, detail=f"invalid public_key_pem: {err}",
        )

    short = str(token_row.id)[:8]
    name = f"{token_row.label}-{short}"
    # Defensive: name unique constraint may collide if the same label
    # was used for a previous scanner (label re-use is fine; name
    # collisions are not). Append more entropy on conflict.
    existing = (await db.execute(
        select(Scanner).where(Scanner.name == name)
    )).scalar_one_or_none()
    if existing is not None:
        name = f"{token_row.label}-{uuid.uuid4().hex[:12]}"

    scanner = Scanner(
        name=name,
        pool=token_row.pool,
        public_key_pem=body.public_key_pem,
        key_fingerprint=fp,
        hostname=body.hostname,
        version=body.agent_version,
        allowed_source_ids=token_row.allowed_source_ids,
        allowed_scan_types=token_row.allowed_scan_types,
    )
    db.add(scanner)
    try:
        await db.flush()
    except Exception as exc:  # pragma: no cover — fingerprint collision is exceptional
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"public key already registered or name collision: {exc}",
        )

    token_row.used_at = datetime.now(timezone.utc)
    token_row.used_by_scanner_id = scanner.id
    await db.commit()
    await db.refresh(scanner)

    from akashic.services import scan_pubsub
    await scan_pubsub.publish_scanner_event({
        "kind": "scanner.claim_redeemed",
        "scanner_id": str(scanner.id),
        "scanner_name": scanner.name,
        "pool": scanner.pool,
        "token_id": str(token_row.id),
    })
    await record_event(
        db=db, user=None, event_type="scanner_claim_token_redeemed",
        request=request,
        payload={
            "token_id": str(token_row.id),
            "label": token_row.label,
            "scanner_id": str(scanner.id),
            "scanner_name": scanner.name,
            "pool": scanner.pool,
            "hostname": body.hostname,
            "agent_version": body.agent_version,
        },
    )
    return ClaimResponse(
        scanner_id=scanner.id,
        name=scanner.name,
        pool=scanner.pool,
        server_protocol_version=PROTOCOL_VERSION,
    )


# ── Agent endpoints (scanner-JWT auth) ───────────────────────────────────


@router.post("/api/scanners/handshake", response_model=HandshakeResponse)
async def handshake(
    body: HandshakeRequest,
    db: AsyncSession = Depends(get_db),
    scanner: Scanner = Depends(verify_scanner_jwt),
):
    """Agent-startup version check + identity claim. Updates
    last_seen_at + reported metadata even if rejected, so the admin UI
    surfaces stale agents trying to handshake."""
    scanner.protocol_version = body.protocol_version
    scanner.version = body.version
    scanner.hostname = body.hostname
    scanner.last_seen_at = datetime.now(timezone.utc)
    await db.commit()

    accepted = ACCEPTED_MIN <= body.protocol_version <= ACCEPTED_MAX
    if not accepted:
        return Response(
            content=HandshakeResponse(
                accepted=False,
                server_protocol_version=PROTOCOL_VERSION,
                accepted_min=ACCEPTED_MIN,
                accepted_max=ACCEPTED_MAX,
                reason=(
                    f"agent protocol_version={body.protocol_version} "
                    f"outside accepted range [{ACCEPTED_MIN}, {ACCEPTED_MAX}]"
                ),
            ).model_dump_json(),
            status_code=status.HTTP_426_UPGRADE_REQUIRED,
            media_type="application/json",
        )
    return HandshakeResponse(
        accepted=True,
        server_protocol_version=PROTOCOL_VERSION,
        accepted_min=ACCEPTED_MIN,
        accepted_max=ACCEPTED_MAX,
    )


@router.post(
    "/api/scanners/{scanner_id}/heartbeat",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def scanner_heartbeat(
    scanner_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    scanner: Scanner = Depends(verify_scanner_jwt),
):
    """Liveness ping at scanner level (separate from per-scan
    heartbeat). The url's `scanner_id` must match the JWT's sub —
    agents can't ping for a scanner they don't own."""
    if scanner.id != scanner_id:
        raise HTTPException(status_code=403, detail="scanner_id mismatch")
    scanner.last_seen_at = datetime.now(timezone.utc)
    await db.commit()


# ── Lease + complete ─────────────────────────────────────────────────────


_LEASE_DURATION_SECONDS = 60


async def _mint_ingest_jwt(
    db: AsyncSession, scanner_id: uuid.UUID,
) -> str | None:
    """Pick any admin user and mint an ingest-audience JWT bound to
    their identity so the agent can call /api/ingest/batch and the
    per-scan heartbeat endpoint, both of which still need an
    authenticated user for ACL/audit purposes.

    v0.28.2 — embeds the leasing ``scanner_id`` as a JWT claim so the
    scan-progress endpoints can persist scanner attribution on every
    heartbeat / log / stderr row. Trustworthy because the claim is
    minted server-side, not lifted from a client header.

    Audience is "akashic-ingest" (not "akashic-api"), so even though
    the user identity behind the token is admin, the token itself
    will fail decode_access_token() against any non-ingest endpoint —
    a compromised scanner host can't pivot from ingest to admin (review
    A-C1)."""
    res = await db.execute(
        select(User).where(User.role == "admin").order_by(User.created_at).limit(1)
    )
    admin = res.scalar_one_or_none()
    if admin is None:
        return None
    return create_ingest_token(
        str(admin.id),
        scanner_id=str(scanner_id),
        expires_delta=timedelta(hours=24),
    )


class LeaseRequest(BaseModel):
    """Optional lease-poll body. v0.30.2 — the agent reports its
    running build version so `scanners.version` reflects the live
    binary even after an in-place image upgrade (the claim-time
    version otherwise goes stale). Older agents post an empty object;
    the field stays None and nothing changes."""
    agent_version: str | None = Field(default=None, max_length=32)


@router.post("/api/scans/lease")
async def lease_scan(
    response: Response,
    db: AsyncSession = Depends(get_db),
    scanner: Scanner = Depends(verify_scanner_jwt),
    body: LeaseRequest | None = None,
):
    """Atomically claim one pending scan whose pool matches the
    leasing scanner's pool (or whose pool is NULL). Returns 204 with
    no body when there's nothing to do — agents back off and retry."""
    if not scanner.enabled:
        raise HTTPException(status_code=403, detail="scanner is disabled")

    # v0.30.2 — refresh the scanner's reported build version on every
    # lease poll (~5 s) so Settings → Scanners reflects the running
    # binary even after an in-place `docker compose pull` (the version
    # is otherwise only written at claim time and goes stale). Applies
    # on both the no-op and the successful-lease commit paths below.
    if body is not None and body.agent_version:
        scanner.version = body.agent_version

    # SKIP LOCKED makes parallel leases serialise without blocking.
    # The CTE picks one row, locks it, and the outer UPDATE flips its
    # state in the same round trip.
    # Order: never-started pending scans first (started_at IS NULL),
    # then re-leasable rows with the oldest started_at first. id as a
    # final tiebreaker so concurrent leases pick the same row
    # deterministically and SKIP LOCKED keeps the duplicates apart.
    # Scope enforcement: NULL scope columns mean "unrestricted on this
    # dimension". When set, a scanner can only claim work that's both
    # in its pool AND on its source whitelist AND of an allowed type.
    # Building the WHERE clause conditionally avoids the SQLAlchemy
    # `:name::type[]` lexer ambiguity (`::` collides with the colon
    # parameter prefix) and keeps each query free of parameters that
    # don't need binding.
    extra_where = ""
    params: dict[str, object] = {
        "pool": scanner.pool,
        "scanner_id": scanner.id,
        "lease_seconds": _LEASE_DURATION_SECONDS,
    }
    if scanner.allowed_source_ids:
        extra_where += " AND scans.source_id = ANY(:allowed_source_ids)"
        params["allowed_source_ids"] = scanner.allowed_source_ids
    if scanner.allowed_scan_types:
        extra_where += " AND scans.scan_type = ANY(:allowed_scan_types)"
        params["allowed_scan_types"] = scanner.allowed_scan_types

    # v0.28.0: scan-lease no longer pre-filters local sources by
    # recent reachability_check failures. Reachability is now an
    # on-demand probe (see /api/sources/{id}/test-scanners) and the
    # signal is purely advisory — if the scanner can't reach the
    # local path at scan time, the scan failure surfaces it directly,
    # which is the same path SMB/NFS scans have always taken.
    lease_sql = text(f"""
        WITH next_scan AS (
            SELECT scans.id FROM scans
             JOIN sources ON sources.id = scans.source_id
             WHERE scans.status IN ('pending', 'running')
               AND (scans.assigned_scanner_id IS NULL OR scans.lease_expires_at < now())
               AND (scans.pool = :pool OR scans.pool IS NULL)
               {extra_where}
             ORDER BY scans.started_at ASC NULLS FIRST, scans.id ASC
             LIMIT 1
             FOR UPDATE OF scans SKIP LOCKED
        )
        UPDATE scans
           SET assigned_scanner_id = :scanner_id,
               lease_expires_at    = now() + (:lease_seconds * interval '1 second'),
               status              = 'running',
               started_at          = COALESCE(started_at, now())
          FROM next_scan
         WHERE scans.id = next_scan.id
        RETURNING scans.id, scans.source_id, scans.scan_type
    """)
    res = await db.execute(lease_sql, params)
    row = res.first()
    if row is None:
        await db.commit()  # close the transaction even on no-op
        response.status_code = status.HTTP_204_NO_CONTENT
        return None

    scan_id, source_id, scan_type = row
    source = (await db.execute(
        select(Source).where(Source.id == source_id)
    )).scalar_one_or_none()
    if source is None:
        # Source disappeared between scan-creation and lease — let the
        # watchdog clean this up; from the agent's POV this lease is
        # pointless. Mark the scan failed.
        await db.execute(
            text("UPDATE scans SET status='failed', "
                 "error_message='source missing at lease time' "
                 "WHERE id = :sid"),
            {"sid": scan_id},
        )
        await db.commit()
        response.status_code = status.HTTP_204_NO_CONTENT
        return None

    # Phase-2 status transition: source.status flips to 'scanning'
    # only now, when an agent has *actually* claimed the work.
    source.status = "scanning"
    # Refresh scanner.last_seen_at on every successful lease.
    scanner.last_seen_at = datetime.now(timezone.utc)
    await db.commit()

    # Push the state change to the list-level WS subscribers.
    from akashic.services import scan_pubsub
    # Re-fetch the scan to pick up the started_at written by the
    # lease UPDATE (the RETURNING clause didn't expose it). We need
    # the real timestamp for the scan.state event so the frontend's
    # recomputeBySource picks this running scan over older terminal
    # scans for the same source. v0.4.10.
    started_at_row = (await db.execute(
        text("SELECT started_at FROM scans WHERE id = :sid"),
        {"sid": scan_id},
    )).first()
    started_at_iso = (
        started_at_row[0].isoformat()
        if started_at_row and started_at_row[0]
        else None
    )

    await scan_pubsub.publish_source_event({
        "kind": "scan.state",
        "source_id": str(source.id),
        "scan_id": str(scan_id),
        "scan_status": "running",
        "source_status": "scanning",
        "scanner_id": str(scanner.id),
        "scanner_name": scanner.name,
        "scan_type": scan_type or "incremental",
        "files_found": 0,
        "current_path": None,
        "started_at": started_at_iso,
    })
    # v0.4.11: seed the change-detection snapshot so the heartbeat
    # arriving 1s later doesn't immediately re-broadcast the same state.
    from akashic.services import scan_broadcast
    await scan_broadcast.record_broadcast(
        str(scan_id),
        phase=None,
        status="running",
        files_found=0,
        total_estimated=None,
    )

    api_jwt = await _mint_ingest_jwt(db, scanner.id)
    # Merge host config (if any) under the source's share-only fields so
    # the scanner sees one combined dict, regardless of where the
    # connection-level keys live. Legacy sources without a host_id keep
    # behaving exactly as before.
    from akashic.services.source_config import merge_host_and_source
    merged_config = merge_host_and_source(getattr(source, "host", None), source)
    # v0.14.0 — OAuth-shaped sources (gdrive, onedrive, …) get a fresh
    # access token injected here. The scanner reads `access_token` from
    # connection_config; for scans that outlast the access-token TTL,
    # the agent re-mints via POST /api/scanners/oauth/access-token.
    from akashic.services.source_oauth import (
        mint_access_token_for_source,
        OAuthExchangeFailed,
    )
    try:
        oauth_pair = await mint_access_token_for_source(db, source.id)
    except OAuthExchangeFailed as exc:
        # Refresh failed — the OAuth grant is broken. Don't lease this
        # scan; mark it failed so the watchdog moves on and the UI's
        # reachability badge surfaces the broken state.
        await db.execute(
            text(
                "UPDATE scans SET status='failed', "
                "error_message=:msg WHERE id = :sid"
            ),
            {"msg": f"oauth refresh failed: {exc.detail[:200]}", "sid": scan_id},
        )
        await db.commit()
        response.status_code = status.HTTP_204_NO_CONTENT
        return None
    if oauth_pair is not None:
        access_token, expires_at = oauth_pair
        merged_config["access_token"] = access_token
        if expires_at is not None:
            merged_config["access_token_expires_at"] = expires_at.isoformat()

    # v0.29.8 — fire notify_eligible_joiners at claim time, not just at
    # split_units time. Pre-fix: the second scanner only got woken up
    # AFTER scanner A had mounted the source, walked the root, and
    # called /scan-work/split_units — commonly 30-60s for an SMB share.
    # Now scanner B receives its LPUSH immediately and starts mounting
    # in parallel. Helper short-circuits when max_parallel_scanners<=1.
    if effective_max_parallel_scanners(source) > 1:
        from akashic.services import scan_join
        try:
            scan = (await db.execute(
                select(Scan).where(Scan.id == scan_id)
            )).scalar_one_or_none()
            if scan is not None:
                notified = await scan_join.notify_eligible_joiners(
                    db=db, scan=scan, source=source,
                    exclude_scanner_id=scanner.id,
                )
                logger.info(
                    "claim: scan=%s source=%s scanner=%s notified=%d "
                    "eligible joiner(s) at lease time",
                    scan_id, source.id, scanner.id, notified,
                )
        except Exception as exc:  # noqa: BLE001
            # Notification is best-effort. A failure here must not
            # block the lease return — the holder still gets the work,
            # joiners will be picked up at the next split's notify
            # exactly as before this change.
            logger.warning(
                "claim: notify_eligible_joiners failed scan=%s: %s",
                scan_id, exc,
            )

    return LeasedScan(
        scan_id=scan_id,
        scan_type=scan_type or "incremental",
        source=LeasedSource(
            id=source.id,
            type=source.type,
            connection_config=merged_config,
            exclude_patterns=source.exclude_patterns,
            max_parallel_scanners=effective_max_parallel_scanners(source),
            scan_chunk_size=effective_chunk_size(source),
        ),
        api_jwt=api_jwt,
    )


class CompleteRequest(BaseModel):
    status: str = Field(pattern="^(completed|failed|cancelled)$")
    error_message: str | None = None


@router.post(
    "/api/scans/{scan_id}/complete",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def complete_scan(
    scan_id: uuid.UUID,
    body: CompleteRequest,
    db: AsyncSession = Depends(get_db),
    scanner: Scanner = Depends(verify_scanner_jwt),
):
    """Release the lease and write the terminal status. Only the
    leasing scanner may complete its own scan."""
    scan = (await db.execute(
        select(Scan).where(Scan.id == scan_id)
    )).scalar_one_or_none()
    if scan is None:
        raise HTTPException(status_code=404, detail="scan not found")
    if scan.assigned_scanner_id != scanner.id:
        raise HTTPException(
            status_code=403, detail="scanner is not the lease holder",
        )
    scan.status = body.status
    scan.completed_at = datetime.now(timezone.utc)
    if body.error_message is not None:
        scan.error_message = body.error_message
    scan.lease_expires_at = None
    # v0.29.8 — distinguish scanner-driven terminal states so a sibling
    # scanner that races a heartbeat against the close logs the right
    # message. Joining scanners on a cooperative scan all post their
    # own /complete; whichever lands first sets the reason, the rest
    # see the existing reason on the next heartbeat 409.
    if scan.cancellation_reason is None:
        if body.status == "completed":
            scan.cancellation_reason = "completed"
        elif body.status == "failed":
            short = (body.error_message or "").splitlines()[0][:80] if body.error_message else ""
            scan.cancellation_reason = f"failed:{short}" if short else "failed"
        else:
            # cancelled via the /complete path — preserve "user" if a
            # prior /cancel already wrote it.
            scan.cancellation_reason = "user"

    # v0.29.2 — flush the Redis-backed counters back onto scan.* so
    # the row is authoritative post-scan. Single-scanner /complete
    # paths hit this; the multi-scanner work-units path flushes from
    # _maybe_finalize_scan instead.
    from akashic.services import scan_counters
    await scan_counters.flush_to_db(db, scan)
    # v0.29.2 — drain any pending Meilisearch index entries for this
    # source so search reflects scan-final state at "completed" time
    # without waiting for the 5 s debounce window.
    if scan.source_id is not None:
        from akashic.services import meili_indexer
        try:
            await meili_indexer.flush(scan.source_id)
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).debug(
                "meili flush on scan complete failed: %s", exc,
            )

    # Phase-2 status transition: source.status mirrors the scan's
    # terminal state. Cancelled scans don't mark the source failed
    # (the user pulled the plug; the source itself isn't broken).
    source = (await db.execute(
        select(Source).where(Source.id == scan.source_id)
    )).scalar_one_or_none()
    if source is not None:
        if body.status == "completed":
            source.status = "online"
            now = datetime.now(timezone.utc)
            source.last_scan_at = now
            # v0.28.0: a successful scan is the strongest reachability
            # proof we'll ever get — record an implicit
            # reachability_results row with no scanner attribution
            # (the scan is the witness; the scanner is implicit). The
            # /reachability-summary endpoint uses this to keep the
            # source-level badge fresh without an explicit probe.
            from akashic.services import reachability_results
            await reachability_results.record_result(
                db=db, source_id=source.id, scanner_id=scanner.id,
                ok=True, step=None, error=None,
                started_at=now, triggered_by=None,
            )
        elif body.status == "failed":
            source.status = "failed"
        elif body.status == "cancelled":
            source.status = "online"
    await db.commit()

    if source is not None:
        from akashic.services import scan_broadcast, scan_pubsub
        await scan_pubsub.publish_source_event({
            "kind": "scan.state",
            "source_id": str(source.id),
            "scan_id": str(scan_id),
            "scan_status": body.status,
            "source_status": source.status,
            "scanner_id": str(scanner.id),
            "scanner_name": scanner.name,
            "scan_type": scan.scan_type,
            "files_found": scan.files_found or 0,
            "current_path": None,
            "started_at": scan.started_at.isoformat() if scan.started_at else None,
        })
        # v0.4.11: terminal state — clear the snapshot so a future
        # re-trigger of this scan_id (rare; mostly tests) starts fresh.
        await scan_broadcast.clear_broadcast(str(scan_id))


# ── On-demand probe transport (v0.28.0) ─────────────────────────────────
#
# Replaces the old continuous-poll reachability_checks queue. The agent
# subscribes to its per-id Redis channel via long-poll; when a user
# triggers /api/sources/{id}/test-scanners (or its scanner-side
# mirror), the API publishes one ProbeRequest per scanner. The agent
# runs the probe and POSTs back to /probes/{request_id}/report. The
# report fans out to (a) the dispatcher waiting on the originating
# /test-… call, and (b) any frontend subscribed to the per-source
# reachability event channel.


@router.get("/api/scanners/{scanner_id}/probes/long-poll")
async def long_poll_probes(
    scanner_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
    scanner: Scanner = Depends(verify_scanner_jwt),
):
    """Block until a probe is published to this scanner, or 30 s elapses.

    Auth: scanner JWT (same audience as /scans/lease). The scanner_id
    in the URL must match the JWT's subject — defends against a
    compromised scanner subscribing to another scanner's channel.
    """
    if scanner_id != scanner.id:
        raise HTTPException(status_code=403, detail="scanner_id mismatch")
    if not scanner.enabled:
        raise HTTPException(status_code=403, detail="scanner is disabled")

    # Heartbeat — long-poll is the agent's primary keep-alive between
    # scans, so refresh last_seen_at here too.
    scanner.last_seen_at = datetime.now(timezone.utc)
    await db.commit()

    from akashic.services.probe_dispatch import wait_for_probe
    payload = await wait_for_probe(scanner.id, timeout_s=30.0)
    if payload is None:
        response.status_code = status.HTTP_204_NO_CONTENT
        return None
    return payload


@router.get("/api/scanners/{scanner_id}/scans/long-poll")
async def long_poll_scan_joins(
    scanner_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
    scanner: Scanner = Depends(verify_scanner_jwt),
):
    """Block until a multi-scanner scan needs help, or 30 s elapses.

    v0.29.0 — discovery channel for the second-and-later scanner on a
    source with ``max_parallel_scanners > 1``. The lease holder calls
    ``POST /scans/{id}/work/split`` after enumerating; the API LPUSHes
    a join notification to each eligible-other scanner's queue; this
    endpoint BRPOPs from that queue and returns a ``LeasedScan``-shaped
    payload so the agent can dispatch straight into
    ``runUnitCoordinated`` (same code path as a fresh lease).

    Returns 204 on timeout (agent reconnects immediately). On 200 the
    payload mirrors ``/api/scans/lease`` exactly so the agent can reuse
    its dispatch code without branching.

    Auth: scanner JWT; scanner_id in the URL must match the JWT subject
    so a compromised scanner can't subscribe to another scanner's
    queue.
    """
    if scanner_id != scanner.id:
        raise HTTPException(status_code=403, detail="scanner_id mismatch")
    if not scanner.enabled:
        raise HTTPException(status_code=403, detail="scanner is disabled")

    # Long-poll counts as a keep-alive (mirrors probes long-poll).
    scanner.last_seen_at = datetime.now(timezone.utc)
    await db.commit()

    from akashic.services.scan_join import wait_for_scan_join
    payload = await wait_for_scan_join(scanner.id, timeout_s=30.0)
    if payload is None:
        response.status_code = status.HTTP_204_NO_CONTENT
        return None
    return payload


@router.post(
    "/api/scanners/{scanner_id}/probes/{request_id}/report",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def report_probe_result(
    scanner_id: uuid.UUID,
    request_id: uuid.UUID,
    body: "ProbeReport",
    db: AsyncSession = Depends(get_db),
    scanner: Scanner = Depends(verify_scanner_jwt),
):
    """Persist a probe result and fan out to subscribers.

    The body carries a `source_id` field via header or — in this design —
    via the original ProbeRequest. We take it from the URL route would
    be nice, but the request_id alone doesn't tell us which source it
    was for. The simplest fix: include source_id in the report body so
    the API doesn't need to keep request → source state in memory.
    """
    if scanner_id != scanner.id:
        raise HTTPException(status_code=403, detail="scanner_id mismatch")

    from akashic.services import probe_dispatch, reachability_results

    started = datetime.now(timezone.utc)
    # Persist against the source_id the probe carried. Pull it from the
    # request payload via a header the agent forwards from the original
    # ProbeRequest. No request → source state on the API side.
    src_id_str = body.source_id
    src = (await db.execute(
        select(Source).where(Source.id == src_id_str)
    )).scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=404, detail="source not found")

    await reachability_results.record_result(
        db=db,
        source_id=src.id,
        scanner_id=scanner.id,
        ok=body.ok,
        step=body.step,
        error=body.error,
        started_at=started,
        triggered_by=None,
    )
    scanner.last_seen_at = started
    await db.commit()

    await probe_dispatch.publish_report(
        request_id=request_id,
        scanner_id=scanner.id,
        source_id=src.id,
        ok=body.ok,
        step=body.step,
        error=body.error,
        completed_at=started,
    )


# ── Eligibility-discovery for the scanner-side modal (v0.5.6) ────────────


class SourceReachabilityRow(BaseModel):
    source_id: uuid.UUID
    source_name: str
    source_type: str
    host_name: str | None
    currently_allowed: bool
    ok: bool | None
    last_probed_at: datetime | None
    step: str | None
    error: str | None


@router.get("/api/scanners/{scanner_id}/source-reachability")
async def list_scanner_source_reachability(
    scanner_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """For a given scanner, return its latest probe result for every
    source. Feeds the scanner-side AllowedSourcesModal so the user
    can see "scanner X reaches source Y" before toggling allow/deny.
    """
    scanner = (await db.execute(
        select(Scanner).where(Scanner.id == scanner_id)
    )).scalar_one_or_none()
    if scanner is None:
        raise HTTPException(status_code=404, detail="scanner not found")

    allowed = set(scanner.allowed_source_ids or [])
    is_unrestricted = not scanner.allowed_source_ids

    # v0.28.0 — read latest result per source from reachability_results.
    # No staleness gate: under the on-demand model the panel just shows
    # what the most recent probe said.
    rows = (await db.execute(text("""
        SELECT s.id, s.name, s.type, h.name AS host_name,
               rr.ok, rr.completed_at, rr.step, rr.error
          FROM sources s
          LEFT JOIN hosts h ON h.id = s.host_id
          LEFT JOIN LATERAL (
              SELECT ok, completed_at, step, error
                FROM reachability_results
               WHERE source_id = s.id
                 AND scanner_id = :scanner_id
               ORDER BY completed_at DESC
               LIMIT 1
          ) rr ON true
         ORDER BY s.name ASC
    """), {"scanner_id": scanner.id})).fetchall()

    return [
        SourceReachabilityRow(
            source_id=r[0], source_name=r[1], source_type=r[2], host_name=r[3],
            currently_allowed=is_unrestricted or r[0] in allowed,
            ok=r[4], last_probed_at=r[5], step=r[6], error=r[7],
        )
        for r in rows
    ]


# ── Scanner-side bulk probe trigger (v0.28.0) ────────────────────────────


class TestSourcesRequest(BaseModel):
    """Optional source_ids subset for POST /scanners/{id}/test-sources.

    Omit → test every source the scanner is currently allowed to claim.
    """

    source_ids: list[uuid.UUID] | None = None


class TestSourcesResultRow(BaseModel):
    source_id: uuid.UUID
    ok: bool | None
    step: str | None
    error: str | None
    pending: bool = False
    completed_at: datetime | None = None


class TestSourcesResponse(BaseModel):
    results: list[TestSourcesResultRow]


@router.post(
    "/api/scanners/{scanner_id}/test-sources",
    response_model=TestSourcesResponse,
)
async def test_scanner_sources(
    scanner_id: uuid.UUID,
    body: TestSourcesRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Mirror of /api/sources/{id}/test-scanners from the scanner's
    perspective — for the AllowedSourcesModal.

    For each (source, scanner) pair: non-local sources run inline via
    the API; local sources go through the long-poll dispatcher with
    a 5 s timeout per scanner. Slow probes return `pending=true`.
    """
    scanner = (await db.execute(
        select(Scanner).where(Scanner.id == scanner_id)
    )).scalar_one_or_none()
    if scanner is None:
        raise HTTPException(status_code=404, detail="scanner not found")

    requested_ids = (body.source_ids if body else None) or None
    src_q = select(Source)
    if requested_ids:
        src_q = src_q.where(Source.id.in_(requested_ids))
    sources = list((await db.execute(src_q)).scalars().all())

    if not requested_ids:
        # Filter to sources this scanner is currently allowed to claim.
        allowed = scanner.allowed_source_ids
        if allowed:
            allowed_set = set(allowed)
            sources = [s for s in sources if s.id in allowed_set]
        if scanner.pool != "default" or any(
            s.preferred_pool is not None for s in sources
        ):
            sources = [
                s for s in sources
                if s.preferred_pool is None or s.preferred_pool == scanner.pool
            ]

    from akashic.services import probe_dispatch

    rows: list[TestSourcesResultRow] = []
    local_sources = [s for s in sources if s.type == "local"]
    nonlocal_sources = [s for s in sources if s.type != "local"]

    # Non-local: API dials inline once per source.
    for s in nonlocal_sources:
        result = await probe_dispatch.dispatch_inline(
            db=db, source=s, scanner_id=scanner.id, triggered_by=user.id,
        )
        rows.append(TestSourcesResultRow(
            source_id=s.id,
            ok=result.get("ok"),
            step=result.get("step"),
            error=result.get("error"),
            pending=False,
            completed_at=result.get("completed_at"),
        ))

    # Local: one round-trip dispatch per source against just this scanner.
    for s in local_sources:
        delivered = await probe_dispatch.dispatch_remote(
            source=s, scanner_ids=[scanner.id], timeout_s=5.0,
        )
        report = delivered.get(scanner.id)
        if report is None:
            rows.append(TestSourcesResultRow(
                source_id=s.id, ok=None, step=None, error=None,
                pending=True,
            ))
        else:
            rows.append(TestSourcesResultRow(
                source_id=s.id,
                ok=report.get("ok"),
                step=report.get("step"),
                error=report.get("error"),
                pending=False,
                completed_at=report.get("completed_at"),
            ))

    await db.commit()
    return TestSourcesResponse(results=rows)


# ── Scanner-facing OAuth refresh (v0.14.0) ──────────────────────────────────


class OAuthAccessTokenRequest(BaseModel):
    source_id: uuid.UUID


class OAuthAccessTokenResponse(BaseModel):
    access_token: str
    expires_at: datetime | None


@router.post(
    "/api/scanners/oauth/access-token",
    response_model=OAuthAccessTokenResponse,
)
async def scanner_mint_oauth_access_token(
    body: OAuthAccessTokenRequest,
    db: AsyncSession = Depends(get_db),
    scanner: Scanner = Depends(verify_scanner_jwt),
) -> OAuthAccessTokenResponse:
    """Mint a fresh access token for a leased OAuth-shaped source.

    Auth: scanner JWT. The scanner must hold an active scan lease on
    this source — checked here so a compromised scanner that's lost its
    lease can't mint tokens for arbitrary sources.

    Used when a long scan exhausts the access-token TTL mid-walk. The
    initial access token comes from the scan-lease payload; subsequent
    refreshes go through this endpoint.
    """
    # Active-lease check: scanner currently holds a running-scan lease for
    # this source — either as the single-scanner scan owner, or as a
    # work-unit lease holder under a multi-scanner scan. Either path is
    # legitimate; both gate against compromised-scanner-with-stale-token.
    now = datetime.now(timezone.utc)
    has_lease_q = await db.execute(
        text(
            """
            SELECT 1
              FROM scans
             WHERE source_id = :sid
               AND assigned_scanner_id = :scanner_id
               AND status = 'running'
             LIMIT 1
            """
        ),
        {"sid": body.source_id, "scanner_id": scanner.id},
    )
    if has_lease_q.first() is None:
        unit_lease_q = await db.execute(
            text(
                """
                SELECT 1
                  FROM scan_work_units u
                  JOIN scans s ON s.id = u.scan_id
                 WHERE s.source_id = :sid
                   AND u.assigned_scanner_id = :scanner_id
                   AND u.status = 'running'
                   AND u.lease_expires_at > :now
                 LIMIT 1
                """
            ),
            {"sid": body.source_id, "scanner_id": scanner.id, "now": now},
        )
        has_lease = unit_lease_q.first() is not None
    else:
        has_lease = True
    if not has_lease:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No active lease for this source",
        )

    from akashic.services.source_oauth import (
        OAuthExchangeFailed,
        mint_access_token_for_source,
    )

    try:
        pair = await mint_access_token_for_source(db, body.source_id)
    except OAuthExchangeFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.detail
        ) from exc

    if pair is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source has no connected OAuth credential",
        )
    access_token, expires_at = pair
    return OAuthAccessTokenResponse(
        access_token=access_token, expires_at=expires_at
    )
