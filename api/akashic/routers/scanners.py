"""Admin-side scanner management: CRUD, join tokens, reachability and probes.

All endpoints here authenticate a user JWT with a `scanners:*` scope. The
scanner-agent protocol (claim, handshake, heartbeat, lease, complete,
long-polls) lives in `routers/scanner_protocol.py`.
"""
from __future__ import annotations

from typing import Annotated
import logging
import uuid
from datetime import datetime, timezone

from akashic.schemas.ws_events import WsScannerDeleted, WsScannerUpdated
from akashic.routing import ApiRouter
from fastapi import Depends, HTTPException, Path, Request, status
from akashic.schemas.base import ApiModel
from pydantic import Field, field_validator

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import Principal, require_scope
from akashic.database import get_db
from akashic.models.scanner import Scanner
from akashic.models.source import Source
from akashic.models.user import User
from akashic.protocol import PROTOCOL_VERSION
from akashic.services.scanner_keys import generate_keypair

logger = logging.getLogger(__name__)

router = ApiRouter(tags=["scanners"])


# ── Schemas ──────────────────────────────────────────────────────────────


_ALLOWED_SCAN_TYPES = {"incremental", "full"}


class ScannerCreate(ApiModel):
    name: str = Field(min_length=1, max_length=64, description="Unique scanner name.")
    pool: str = Field(default="default", min_length=1, max_length=64, description="Pool the scanner serves; scans are only leased to scanners in their pool.")

    model_config = {"json_schema_extra": {'examples': [{'name': 'nas-scanner-1', 'pool': 'default'}]}}


class ScannerPatch(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=64, description="New unique name.")
    pool: str | None = Field(default=None, min_length=1, max_length=64, description="New pool.")
    enabled: bool | None = Field(default=None, description="Enable or disable; a disabled scanner cannot authenticate.")
    # Sentinels: omitted = leave unchanged; explicit `null` (via the
    # `clear_*` flags below) = clear back to "unrestricted".
    allowed_source_ids: list[uuid.UUID] | None = Field(default=None, description="Restrict the scanner to these sources. Omit to leave unchanged.")
    allowed_scan_types: list[str] | None = Field(default=None, description="Restrict the scanner to these scan types (`incremental`, `full`). Omit to leave unchanged.")
    clear_allowed_source_ids: bool = Field(default=False, description="Remove the source restriction.")
    clear_allowed_scan_types: bool = Field(default=False, description="Remove the scan type restriction.")
    # Per-scanner config. Omitted = unchanged; use clear_* to revert to
    # the inherited default.
    tika_url: str | None = Field(default=None, max_length=512, description="Tika server for text extraction, `http://` or `https://`; an empty string disables extraction on this scanner.")
    extract_workers: int | None = Field(default=None, ge=1, le=64, description="Concurrent text extraction workers (1-64).")
    max_concurrent_units: int | None = Field(default=None, ge=1, le=32, description="Work units walked at once (1-32).")
    clear_tika_url: bool = Field(default=False, description="Revert to the server's Tika URL.")
    clear_extract_workers: bool = Field(default=False, description="Revert to the default worker count.")
    clear_max_concurrent_units: bool = Field(default=False, description="Revert to the default of 1.")

    @field_validator("tika_url")
    @classmethod
    def _tika_url_ok(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().rstrip("/")
        # Empty string = extraction explicitly disabled on this scanner.
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("tika_url must start with http:// or https://")
        return v

    model_config = {"json_schema_extra": {'examples': [{'enabled': True, 'allowed_scan_types': ['incremental'], 'extract_workers': 8}]}}


class ScannerCreated(ApiModel):
    """Returned ONCE on /POST /api/v1/scanners. The private_key_pem field
    isn't persisted on the api side — copy it now or rotate later."""

    id: uuid.UUID = Field(description="Scanner id (the JWT `kid` and `sub`).")
    name: str = Field(description="Scanner name.")
    pool: str = Field(description="Scanner pool.")
    public_key_pem: str = Field(description="Public key the server verifies JWTs with.")
    private_key_pem: str = Field(description="Private key, shown only in this response. Install it on the scanner host.")
    key_fingerprint: str = Field(description="Fingerprint of the public key.")
    protocol_version: int = Field(description="Protocol version this server speaks.")


# Agent defaults when a scanner has no override (mirror the Go agent).
DEFAULT_EXTRACT_WORKERS = 4
DEFAULT_MAX_CONCURRENT_UNITS = 1


class ScannerEffectiveConfig(ApiModel):
    tika_url: str = Field(description="Tika URL the agent uses; empty when extraction is off.")
    extract_workers: int = Field(description="Text extraction workers.")
    max_concurrent_units: int = Field(description="Work units walked at once.")


def effective_scanner_config(s: Scanner) -> ScannerEffectiveConfig:
    from akashic.services.runtime_settings import rt
    return ScannerEffectiveConfig(
        tika_url=s.tika_url if s.tika_url is not None else (rt().tika_url or ""),
        extract_workers=s.extract_workers or DEFAULT_EXTRACT_WORKERS,
        max_concurrent_units=s.max_concurrent_units or DEFAULT_MAX_CONCURRENT_UNITS,
    )


class ScannerSummary(ApiModel):
    id: uuid.UUID = Field(description="Scanner id.")
    name: str = Field(description="Scanner name.")
    pool: str = Field(description="Pool the scanner serves.")
    key_fingerprint: str = Field(description="Fingerprint of the registered public key.")
    hostname: str | None = Field(description="Host the agent reported at handshake.")
    version: str | None = Field(description="Agent build version.")
    protocol_version: int | None = Field(description="Protocol version the agent reported.")
    registered_at: datetime = Field(description="When the scanner was registered.")
    last_seen_at: datetime | None = Field(description="Last time the scanner made any request.")
    enabled: bool = Field(description="Whether the scanner may authenticate.")
    online: bool = Field(description="Whether it was seen within the last 90 seconds.")
    allowed_source_ids: list[uuid.UUID] | None = Field(default=None, description="Sources the scanner is restricted to; null for any.")
    allowed_scan_types: list[str] | None = Field(default=None, description="Scan types the scanner is restricted to; null for any.")
    # Admin-set per-scanner config; NULL = inherit. `effective_config`
    # is what the agent actually receives.
    tika_url: str | None = Field(default=None, description="Admin override of the Tika URL; null inherits the server setting.")
    extract_workers: int | None = Field(default=None, description="Admin override of the extraction workers; null inherits the default.")
    max_concurrent_units: int | None = Field(default=None, description="Admin override of concurrent units; null inherits the default.")
    effective_config: "ScannerEffectiveConfig | None" = Field(default=None, description="The configuration the agent actually receives.")

    model_config = {"from_attributes": True}


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
        tika_url=s.tika_url,
        extract_workers=s.extract_workers,
        max_concurrent_units=s.max_concurrent_units,
        effective_config=effective_scanner_config(s),
    )


# ── Admin CRUD ───────────────────────────────────────────────────────────


@router.post(
    "/scanners",
    response_model=ScannerCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Register a scanner",
    description=(
        "Creates a scanner and generates its Ed25519 keypair on the server. The private key is "
        "returned once and never stored; deliver it to the scanner host. Prefer claim tokens, which "
        "keep the private key on the scanner host.\n"
        "Requires scope `scanners:write`. Errors: 409 if the name is in use; 422 for a malformed "
        "body."
    ),
)
async def create_scanner(
    body: ScannerCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("scanners:write"),
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


@router.get(
    "/scanners",
    response_model=list[ScannerSummary],
    summary="List scanners",
    description=(
        "Every registered scanner, ordered by name, with its restrictions, reported version and "
        "whether it is online (seen within the last 90 seconds). `effective_config` is the "
        "configuration the agent actually receives.\n"
        "Requires scope `scanners:read`."
    ),
)
async def list_scanners(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("scanners:read"),
):
    rows = (await db.execute(select(Scanner).order_by(Scanner.name))).scalars().all()
    return [_to_summary(s) for s in rows]


class ScannerCounts(ApiModel):
    """Lightweight count summary for the Sources page banner — avoids
    re-fetching the whole scanner list per page mount."""

    registered: int = Field(description="Registered scanners.")
    online: int = Field(description="Scanners seen within the last 90 seconds.")


@router.get(
    "/scanners/summary",
    response_model=ScannerCounts,
    summary="Count scanners",
    description=(
        "How many scanners are registered and how many are online (seen within the last 90 seconds).\n"
        "Requires scope `scanners:read`."
    ),
)
async def scanners_summary(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("scanners:read"),
):
    rows = (await db.execute(select(Scanner))).scalars().all()
    return ScannerCounts(
        registered=len(rows),
        online=sum(1 for s in rows if _is_online(s)),
    )


@router.patch(
    "/scanners/{scanner_id}",
    response_model=ScannerSummary,
    summary="Update a scanner",
    description=(
        "Changes a scanner's name, pool, enabled flag, allowed sources and scan types and per-scanner "
        "settings. Omitted fields are left unchanged; the `clear_*` flags reset a restriction or "
        "setting to unrestricted or inherited. A disabled scanner cannot authenticate.\n"
        "Requires scope `scanners:write`. Errors: 400 for unknown source ids or scan types (allowed: "
        "`incremental`, `full`); 404 if the scanner does not exist; 422 for invalid values."
    ),
)
async def patch_scanner(
    scanner_id: Annotated[uuid.UUID, Path(description="Scanner to update.")],
    body: ScannerPatch,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("scanners:write"),
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
    for f in ("tika_url", "extract_workers", "max_concurrent_units"):
        v = getattr(body, f)
        if v is not None:
            setattr(scanner, f, v)
        elif getattr(body, f"clear_{f}"):
            setattr(scanner, f, None)
    await db.commit()
    await db.refresh(scanner)
    from akashic.services import scan_pubsub
    await scan_pubsub.publish_scanner_event(WsScannerUpdated(
        scanner_id=str(scanner.id),
    ))
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
    "/scanners/{scanner_id}/rotate",
    response_model=ScannerCreated,
    summary="Rotate a scanner key",
    description=(
        "Generates a new keypair for the scanner and returns the new private key once. The old key "
        "stops authenticating immediately, so the agent must be reconfigured.\n"
        "Requires scope `scanners:write`. Errors: 404 if the scanner does not exist."
    ),
)
async def rotate_scanner(
    scanner_id: Annotated[uuid.UUID, Path(description="Scanner whose key to replace.")],
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("scanners:write"),
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
    "/scanners/{scanner_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a scanner",
    description=(
        "Removes the scanner. Scans it was leasing go back to `pending` and unassigned so another "
        "scanner can take them. A running agent is refused from then on.\n"
        "Requires scope `scanners:write`. Errors: 404 if the scanner does not exist."
    ),
)
async def delete_scanner(
    scanner_id: Annotated[uuid.UUID, Path(description="Scanner to delete.")],
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("scanners:write"),
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
    from akashic.services import scan_pubsub
    await scan_pubsub.publish_scanner_event(WsScannerDeleted(
        scanner_id=str(scanner_id),
    ))


# ── Join tokens (self-registration) ──────────────────────────────────────


class ClaimTokenCreate(ApiModel):
    label: str = Field(min_length=1, max_length=64, description="Name for the token, used to name scanners enrolled with it.", examples=['branch-office'])
    pool: str = Field(default="default", min_length=1, max_length=64, description="Pool scanners enrolled with the token join.")
    ttl_minutes: int = Field(default=60, ge=1, le=60 * 24 * 30, description="Minutes until the token expires (1 to 43200, 30 days).")
    allowed_source_ids: list[uuid.UUID] | None = Field(default=None, description="Restrict enrolled scanners to these sources; null for any.")
    allowed_scan_types: list[str] | None = Field(default=None, description="Restrict enrolled scanners to these scan types (`incremental`, `full`); null for any.")

    model_config = {"json_schema_extra": {'examples': [{'label': 'branch-office', 'pool': 'default', 'ttl_minutes': 60}]}}


class ClaimSnippets(ApiModel):
    """Install commands with the claim token filled in."""

    shell: str = Field(description="Shell one-liner.")
    docker_run: str = Field(description="`docker run` command.")
    compose: str = Field(description="Docker Compose service.")
    k8s: str = Field(description="Kubernetes manifest.")
    env: str = Field(description="Environment file.")


class ClaimTokenCreated(ApiModel):
    """Returned ONCE on POST /api/v1/scanner-claim-tokens. The plaintext
    `token` field isn't persisted on the api side (we keep only its
    sha256 hash) — copy it now or revoke and regenerate."""

    id: uuid.UUID = Field(description="Token id.")
    label: str = Field(description="Token label.")
    pool: str = Field(description="Pool for enrolled scanners.")
    allowed_source_ids: list[uuid.UUID] | None = Field(description="Source restriction for enrolled scanners.")
    allowed_scan_types: list[str] | None = Field(description="Scan type restriction for enrolled scanners.")
    token: str = Field(description="The plaintext claim token, shown only in this response.")
    expires_at: datetime = Field(description="When the token expires.")
    snippets: ClaimSnippets = Field(description="Ready-to-paste install commands, with the token filled in.")


class ClaimTokenSummary(ApiModel):
    id: uuid.UUID = Field(description="Token id.")
    label: str = Field(description="Token label.")
    pool: str = Field(description="Pool for enrolled scanners.")
    allowed_source_ids: list[uuid.UUID] | None = Field(description="Source restriction for enrolled scanners.")
    allowed_scan_types: list[str] | None = Field(description="Scan type restriction for enrolled scanners.")
    status: str = Field(description="`active`, `used` or `expired`.")
    created_at: datetime = Field(description="When the token was created.")
    expires_at: datetime = Field(description="When the token expires (or expired, or was revoked).")
    used_at: datetime | None = Field(description="When the token was redeemed.")
    used_by_scanner_id: uuid.UUID | None = Field(description="Scanner created by redeeming it.")


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
    "/scanner-claim-tokens",
    response_model=ClaimTokenCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a scanner claim token",
    description=(
        "Mints a one-time token that lets a new scanner host enrol itself with `POST "
        "/scanners/claim`, and ready-to-paste install snippets. The token is returned once; only its "
        "hash is kept. It expires after `ttl_minutes` and can be revoked until used. Scanners created "
        "from it get its pool and restrictions. Records a `scanner_claim_token_created` audit event.\n"
        "Requires scope `scanners:write`. Errors: 400 for unknown source ids or scan types; 422 for a "
        "malformed body."
    ),
)
async def create_claim_token(
    body: ClaimTokenCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("scanners:write"),
):
    user = principal.user
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
    "/scanner-claim-tokens",
    response_model=list[ClaimTokenSummary],
    summary="List scanner claim tokens",
    description=(
        "Every claim token, newest first, with its derived status (`active`, `used` or `expired`) and "
        "the scanner that used it. The secret is never returned.\n"
        "Requires scope `scanners:read`."
    ),
)
async def list_claim_tokens(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("scanners:read"),
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
    "/scanner-claim-tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a scanner claim token",
    description=(
        "Expires an unused token immediately. Records a `scanner_claim_token_revoked` audit event.\n"
        "Requires scope `scanners:write`. Errors: 404 if the token does not exist; 410 if it was "
        "already used."
    ),
)
async def revoke_claim_token(
    token_id: Annotated[uuid.UUID, Path(description="Claim token to revoke.")],
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("scanners:write"),
):
    user = principal.user
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


# ── Eligibility-discovery for the scanner-side modal (v0.5.6) ────────────


class SourceReachabilityRow(ApiModel):
    source_id: uuid.UUID = Field(description="Source id.")
    source_name: str = Field(description="Source display name.")
    source_type: str = Field(description="Connector type.")
    host_name: str | None = Field(description="Host the source belongs to, if any.")
    currently_allowed: bool = Field(description="Whether the scanner may claim work for this source.")
    ok: bool | None = Field(description="Latest probe outcome; null when never probed.")
    last_probed_at: datetime | None = Field(description="When the latest probe completed.")
    step: str | None = Field(description="Probe step that failed, if any.")
    error: str | None = Field(description="Probe error message, if any.")


@router.get(
    "/scanners/{scanner_id}/source-reachability",
    response_model=list[SourceReachabilityRow],
    summary="Scanner reachability per source",
    description=(
        "For one scanner, its latest probe result against every source, and whether it is currently "
        "allowed to claim work for it. Sources never probed show `ok: null`. Does not run any probes.\n"
        "Requires scope `scanners:read`. Errors: 404 if the scanner does not exist."
    ),
)
async def list_scanner_source_reachability(
    scanner_id: Annotated[uuid.UUID, Path(description="Scanner to report on.")],
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("scanners:read"),
):
    """For a given scanner, return its latest probe result for every
    source. Feeds the scanner-side AllowedSourcesModal so the user
    can see "scanner X reaches source Y" before toggling allow/deny.
    """
    user = principal.user
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


class TestSourcesRequest(ApiModel):
    """Optional source_ids subset for POST /scanners/{id}/test-sources.

    Omit → test every source the scanner is currently allowed to claim.
    """

    source_ids: list[uuid.UUID] | None = Field(default=None, description="Sources to probe; omit to probe every source the scanner may claim.")

    model_config = {"json_schema_extra": {'examples': [{'source_ids': ['3fa85f64-5717-4562-b3fc-2c963f66afa6']}]}}


class TestSourcesResultRow(ApiModel):
    source_id: uuid.UUID = Field(description="Probed source.")
    ok: bool | None = Field(description="Probe outcome; null while pending.")
    step: str | None = Field(description="Probe step that failed, if any.")
    error: str | None = Field(description="Probe error message, if any.")
    pending: bool = Field(default=False, description="True when the scanner did not answer within 5 seconds.")
    completed_at: datetime | None = Field(default=None, description="When the probe finished.")


class TestSourcesResponse(ApiModel):
    results: list[TestSourcesResultRow] = Field(description="One row per probed source.")


@router.post(
    "/scanners/{scanner_id}/test-sources",
    response_model=TestSourcesResponse,
    summary="Test sources from a scanner",
    description=(
        "Probes sources from this scanner's point of view and stores the results. Without "
        "`source_ids`, every source the scanner may claim (allowed list and pool) is tested. Network "
        "sources are probed by the API; local sources are delegated to the scanner and wait up to 5 "
        "seconds, after which the row is `pending: true`.\n"
        "Requires scope `scanners:write`. Errors: 404 if the scanner does not exist; 422 for a "
        "malformed body."
    ),
)
async def test_scanner_sources(
    scanner_id: Annotated[uuid.UUID, Path(description="Scanner to probe from.")],
    body: TestSourcesRequest | None = None,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("scanners:write"),
):
    """Mirror of /api/v1/sources/{id}/test-scanners from the scanner's
    perspective — for the AllowedSourcesModal.

    For each (source, scanner) pair: non-local sources run inline via
    the API; local sources go through the long-poll dispatcher with
    a 5 s timeout per scanner. Slow probes return `pending=true`.
    """
    user = principal.user
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


