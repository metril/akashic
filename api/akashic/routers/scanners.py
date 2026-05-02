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
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import require_admin
from akashic.auth.jwt import create_access_token
from akashic.database import get_db
from akashic.models.scan import Scan
from akashic.models.scanner import Scanner
from akashic.models.source import Source
from akashic.models.user import User
from akashic.protocol import ACCEPTED_MAX, ACCEPTED_MIN, PROTOCOL_VERSION
from akashic.services.scanner_auth import verify_scanner_jwt
from akashic.services.scanner_keys import generate_keypair

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


class ClaimRequest(BaseModel):
    token: str = Field(min_length=8, max_length=128)
    public_key_pem: str = Field(min_length=1)
    hostname: str | None = Field(default=None, max_length=255)
    agent_version: str | None = Field(default=None, max_length=32)


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


async def _mint_ingest_jwt(db: AsyncSession) -> str | None:
    """Pick any admin user and mint a 24h JWT scoped to their identity
    so the agent can call /api/ingest/batch and the per-scan heartbeat
    endpoint, both of which still gate on a user dep today.

    Phase-1 expedient: the JWT is bound to "some admin," not to the
    user who triggered the scan. Phase 3 of the multi-scanner work
    refactors this to a service-account or scanner-JWT flow."""
    res = await db.execute(
        select(User).where(User.role == "admin").order_by(User.created_at).limit(1)
    )
    admin = res.scalar_one_or_none()
    if admin is None:
        return None
    return create_access_token(
        {"sub": str(admin.id)}, expires_delta=timedelta(hours=24),
    )


@router.post("/api/scans/lease")
async def lease_scan(
    response: Response,
    db: AsyncSession = Depends(get_db),
    scanner: Scanner = Depends(verify_scanner_jwt),
):
    """Atomically claim one pending scan whose pool matches the
    leasing scanner's pool (or whose pool is NULL). Returns 204 with
    no body when there's nothing to do — agents back off and retry."""
    if not scanner.enabled:
        raise HTTPException(status_code=403, detail="scanner is disabled")

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

    lease_sql = text(f"""
        WITH next_scan AS (
            SELECT id FROM scans
             WHERE status IN ('pending', 'running')
               AND (assigned_scanner_id IS NULL OR lease_expires_at < now())
               AND (pool = :pool OR pool IS NULL)
               {extra_where}
             ORDER BY started_at ASC NULLS FIRST, id ASC
             LIMIT 1
             FOR UPDATE SKIP LOCKED
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
    })

    api_jwt = await _mint_ingest_jwt(db)
    return LeasedScan(
        scan_id=scan_id,
        scan_type=scan_type or "incremental",
        source=LeasedSource(
            id=source.id,
            type=source.type,
            connection_config=source.connection_config or {},
            exclude_patterns=source.exclude_patterns,
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

    # Phase-2 status transition: source.status mirrors the scan's
    # terminal state. Cancelled scans don't mark the source failed
    # (the user pulled the plug; the source itself isn't broken).
    source = (await db.execute(
        select(Source).where(Source.id == scan.source_id)
    )).scalar_one_or_none()
    if source is not None:
        if body.status == "completed":
            source.status = "online"
            source.last_scan_at = datetime.now(timezone.utc)
        elif body.status == "failed":
            source.status = "failed"
        elif body.status == "cancelled":
            source.status = "online"
    await db.commit()

    if source is not None:
        from akashic.services import scan_pubsub
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
        })


# Suppress unused-import warning when running with non-time-aware tools.
_ = time
