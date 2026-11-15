"""Scanner protocol endpoints: the contract scanner agents speak.

Self-registration (claim), handshake, heartbeat, lease, complete, long-polls,
probe reports and OAuth token minting. Authenticated by the scanner's own
Ed25519 JWT (``verify_scanner_jwt``), except ``claim`` which is gated by a
one-time join token. Not a public integration surface; admin-side scanner
management lives in ``routers/scanners.py``.
"""
from __future__ import annotations

from typing import Annotated
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from akashic.routing import ApiRouter, raises
from fastapi import Depends, HTTPException, Path, Request, Response, status
from akashic.schemas.base import ApiModel
from pydantic import Field, field_validator

from akashic.services.rate_limit import make_limiter
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import create_ingest_token
from akashic.auth.scopes import roles_with_scope
from akashic.database import get_db
from akashic.openapi import SCANNER_TAG
from akashic.models.scan import Scan
from akashic.models.scanner import Scanner
from akashic.models.source import Source
from akashic.models.user import User
from akashic.routers.scanners import ScannerEffectiveConfig, effective_scanner_config
from akashic.protocol import ACCEPTED_MAX, ACCEPTED_MIN, PROTOCOL_VERSION
from akashic.schemas.reachability import ProbeReport, ProbeRequest
from akashic.services.scanner_auth import verify_scanner_jwt
from akashic.services.source_config import (
    effective_chunk_size,
    effective_max_parallel_scanners,
)

logger = logging.getLogger(__name__)

router = ApiRouter(tags=[SCANNER_TAG])


class HandshakeRequest(ApiModel):
    protocol_version: int = Field(description="Scanner protocol version the agent speaks. This server accepts version 2 only.", examples=[2])
    version: str | None = Field(default=None, description="Agent build version, e.g. `0.48.0`.")
    hostname: str | None = Field(default=None, description="Host the agent runs on.")
    # Legacy (v0.36.0): ignored; concurrency is admin-configured now.
    max_concurrent_units: int | None = Field(default=None, description="Work units the agent can walk at once; informational.")

    model_config = {"json_schema_extra": {'examples': [{'protocol_version': 2, 'version': '0.48.0', 'hostname': 'scanner01.example.com', 'max_concurrent_units': 2}]}}


class HandshakeResponse(ApiModel):
    config: ScannerEffectiveConfig | None = Field(default=None, description="Effective scanner configuration; present only when accepted.")
    accepted: bool = Field(description="Whether the agent's protocol version is supported. When false the response status is 426.")
    server_protocol_version: int = Field(description="Protocol version this server speaks.")
    accepted_min: int = Field(description="Lowest agent protocol version accepted.")
    accepted_max: int = Field(description="Highest agent protocol version accepted.")
    reason: str | None = Field(default=None, description="Why the handshake was rejected.")


class LeasedSource(ApiModel):
    id: uuid.UUID = Field(description="Source id.")
    type: str = Field(description="Connector type.")
    connection_config: dict = Field(description="Merged host and source connection config, secrets included. Free-form: keys depend on the source or host type.", json_schema_extra={"x-free-form": True})
    exclude_patterns: list[str] | None = Field(default=None, description="Glob patterns to skip while scanning.")
    # Cap on cooperating scanners per scan (default 1). Agents that
    # support unit-coordinated scanning use this to switch between the
    # legacy single-walker path and the work-units lease loop. Older
    # agents simply ignore the field. v0.35.0 — this is the *resolved*
    # value (source ?? host ?? 1); the agent receives a concrete int and
    # never has to know about host inheritance.
    max_parallel_scanners: int = Field(default=1, description="Resolved cap on cooperating scanners for this scan.")
    # v0.35.0 — work-unit entry budget (resolved source ?? host ?? 2000).
    # The agent passes it to scanner.Run as the shallow-split budget;
    # an older API that omits the field leaves it 0 and the scanner
    # falls back to its own default.
    scan_chunk_size: int = Field(default=2000, description="Resolved work-unit entry budget.")


class LeasedScan(ApiModel):
    scan_id: uuid.UUID = Field(description="Leased scan id.")
    scan_type: str = Field(description="`full` or `incremental`.")
    source: LeasedSource = Field(description="Source to scan.")
    api_jwt: str | None = Field(description="Short-lived ingest token for the agent; null if none could be minted.")
    """Short-lived user JWT for the agent to use on /api/v1/ingest/batch
    and /api/v1/scans/{id}/heartbeat. None if the api couldn't determine
    a default user (no admin found) — agents treat this as a 5xx."""


_HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9.\-]{0,253}[a-zA-Z0-9])?$")

# Per-IP rate limit on POST /api/v1/scanners/claim (review A-I5). Five
# attempts per minute is generous for a real scanner (it'd never need
# to retry that fast) but throttles brute-force / token-spray traffic.
_claim_limiter = make_limiter(
    max_requests=5,
    window_seconds=60.0,
    message="too many claim attempts; try again shortly",
)


def _check_claim_rate_limit(request: Request) -> None:
    _claim_limiter.check(request)


class ClaimRequest(ApiModel):
    token: str = Field(min_length=8, max_length=128, description="One-time claim token from `POST /scanner-claim-tokens`.")
    public_key_pem: str = Field(min_length=1, description="The scanner's freshly generated Ed25519 public key, PEM encoded. The private key stays on the scanner.")
    hostname: str | None = Field(default=None, max_length=255, description="RFC 1123 host name of the scanner host.")
    agent_version: str | None = Field(default=None, max_length=32, description="Agent build version.")

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

    model_config = {"json_schema_extra": {'examples': [{'token': 'akcl_3f9a1c7d2e8b4a60', 'public_key_pem': '-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEA...\n-----END PUBLIC KEY-----\n', 'hostname': 'scanner01.example.com', 'agent_version': '0.48.0'}]}}


class ClaimResponse(ApiModel):
    scanner_id: uuid.UUID = Field(description="Id of the new scanner; use it as the JWT `kid` and `sub`.")
    name: str = Field(description="Generated scanner name.")
    pool: str = Field(description="Pool the scanner belongs to.")
    server_protocol_version: int = Field(description="Protocol version this server speaks.")


@router.post(
    "/scanners/claim",
    response_model=ClaimResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enrol a scanner with a claim token",
    description=(
        "Scanner protocol, step 0 of the flow `claim -> handshake -> lease -> heartbeat -> complete`. "
        "A scanner host holding a one-time claim token (from `POST /scanner-claim-tokens`) posts its "
        "own freshly generated Ed25519 public key; the server registers a scanner with the token's "
        "pool and restrictions, marks the token used and returns the scanner id. The private key "
        "never leaves the scanner. The name is `<token label>-<short id>`. Afterwards sign every "
        "request with that key (the `kid` header is the scanner id) and call the handshake. Records a "
        "`scanner_claim_token_redeemed` audit event.\n"
        "No bearer auth: the token is the credential. Rate limited to 5 attempts per minute per "
        "client.\n"
        "Errors: 400 if `public_key_pem` is invalid; 401 for an unknown token; 409 if the key is "
        "already registered; 410 if the token was already used or has expired; 422 for a malformed "
        "body; 429 when rate limited."
    ),
)
@raises(401, 410)  # ClaimError carries its own status (bad / expired / used token)
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


@router.post(
    "/scanners/handshake",
    response_model=HandshakeResponse,
    responses={426: {"model": HandshakeResponse, "description": "Protocol version not accepted; the body says which versions are."}},
    summary="Scanner handshake and version check",
    description=(
        "Scanner protocol, step 1 after enrolment and on every agent start. The agent sends its "
        "`protocol_version` (this server speaks and accepts protocol version 2 only; the accepted "
        "range is returned as `accepted_min` and `accepted_max`). If it is in range the response "
        "carries the scanner's effective configuration (Tika URL, extraction workers, concurrent "
        "units). Otherwise the server answers 426 with `accepted: false` and a `reason`, and the "
        "agent must upgrade before leasing work. The reported version, hostname and last-seen time "
        "are recorded even when rejected.\n"
        "Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an "
        "Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the "
        "scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock "
        "skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.\n"
        "Errors: 426 when the protocol version is outside the accepted range (body is a "
        "`HandshakeResponse`); 422 for a malformed body."
    ),
)
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
    # Push the freshly-reported metadata (online status, version,
    # concurrency) so admin views update without waiting for their poll.
    from akashic.services import scan_pubsub
    await scan_pubsub.publish_scanner_event({
        "kind": "scanner.updated",
        "scanner_id": str(scanner.id),
    })

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
        config=effective_scanner_config(scanner),
        accepted=True,
        server_protocol_version=PROTOCOL_VERSION,
        accepted_min=ACCEPTED_MIN,
        accepted_max=ACCEPTED_MAX,
    )


@router.post(
    "/scanners/{scanner_id}/heartbeat",
    response_model=ScannerEffectiveConfig,
    summary="Scanner liveness heartbeat",
    description=(
        "Scanner protocol. Marks the scanner online (it counts as online for 90 seconds) and returns "
        "its current effective configuration, so settings changed by an admin reach a running agent. "
        "Send roughly every 30 seconds. Separate from the per-scan heartbeat on `POST "
        "/scans/{scan_id}/heartbeat`.\n"
        "Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an "
        "Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the "
        "scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock "
        "skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.\n"
        "Errors: 403 if `scanner_id` is not the authenticated scanner."
    ),
)
async def scanner_heartbeat(
    scanner_id: Annotated[uuid.UUID, Path(description="The calling scanner's own id.")],
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
    # Live config delivery: the agent applies this every heartbeat.
    return effective_scanner_config(scanner)


# ── Lease + complete ─────────────────────────────────────────────────────


_LEASE_DURATION_SECONDS = 60


async def _mint_ingest_jwt(
    db: AsyncSession, scanner_id: uuid.UUID,
) -> str | None:
    """Pick any admin user and mint an ingest-audience JWT bound to
    their identity so the agent can call /api/v1/ingest/batch and the
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
        select(User).where(User.role.in_(roles_with_scope("sources:write"))).order_by(User.created_at).limit(1)
    )
    admin = res.scalar_one_or_none()
    if admin is None:
        return None
    return create_ingest_token(
        str(admin.id),
        scanner_id=str(scanner_id),
        expires_delta=timedelta(hours=24),
    )


class LeaseRequest(ApiModel):
    """Optional lease-poll body. v0.30.2 — the agent reports its
    running build version so `scanners.version` reflects the live
    binary even after an in-place image upgrade (the claim-time
    version otherwise goes stale). Older agents post an empty object;
    the field stays None and nothing changes."""
    agent_version: str | None = Field(default=None, max_length=32, description="Running agent build version; updates the version shown for the scanner.")

    model_config = {"json_schema_extra": {'examples': [{'agent_version': '0.48.0'}]}}


@router.post(
    "/scans/lease",
    response_model=LeasedScan | None,
    responses={204: {"description": "No pending work; retry later."}},
    summary="Lease a pending scan",
    description=(
        "Scanner protocol, step 2. Atomically claims one `pending` (or expired-lease) scan whose pool "
        "matches the scanner's pool, restricted to the scanner's allowed sources and scan types, "
        "oldest first. The lease lasts 60 seconds and is kept alive by the scan heartbeat. Marks the "
        "scan `running` and its source `scanning`. The response carries the merged source connection "
        "config (secrets included, plus a fresh OAuth `access_token` when the source uses OAuth) and "
        "`api_jwt`, a short-lived (24 h) ingest token for `POST /ingest/batch`, `/ingest/content` and "
        "the per-scan progress endpoints.\n"
        "Returns 204 when nothing is pending; poll again after a short wait. Poll the join long-poll "
        "`GET /scanners/{scanner_id}/scans/long-poll` as well when the source allows several parallel "
        "scanners.\n"
        "Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an "
        "Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the "
        "scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock "
        "skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner."
    ),
)
async def lease_scan(
    response: Response,
    db: AsyncSession = Depends(get_db),
    scanner: Scanner = Depends(verify_scanner_jwt),
    body: LeaseRequest | None = None,
):
    """Atomically claim one pending scan whose pool matches the
    leasing scanner's pool (or whose pool is NULL). Returns 204 with
    no body when there's nothing to do — agents back off and retry."""
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
    # on-demand probe (see /api/v1/sources/{id}/test-scanners) and the
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
    # the agent re-mints via POST /api/v1/scanners/oauth/access-token.
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


class CompleteRequest(ApiModel):
    status: str = Field(pattern="^(completed|failed|cancelled)$", description="Terminal status: `completed`, `failed` or `cancelled`.")
    error_message: str | None = Field(default=None, description="Failure detail, recorded on the scan.")

    model_config = {"json_schema_extra": {'examples': [{'status': 'completed'}]}}


@router.post(
    "/scans/{scan_id}/complete",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Complete a scan lease",
    description=(
        "Scanner protocol, final step. Releases the lease and records the terminal `status` "
        "(`completed`, `failed` or `cancelled`) with an optional error message; sets the source back "
        "to `online` (or `failed`), updates `last_scan_at` and the scanner's reachability result for "
        "a successful scan, and notifies subscribers. Idempotent for the same status. Only the "
        "scanner holding the lease may complete it.\n"
        "Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an "
        "Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the "
        "scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock "
        "skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.\n"
        "Errors: 403 if the caller does not hold the lease; 404 if the scan does not exist; 409 if "
        "the scan already ended with a different status; 422 for a malformed body."
    ),
)
async def complete_scan(
    scan_id: Annotated[uuid.UUID, Path(description="Scan whose lease the caller holds.")],
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
    # Don't let a late /complete overwrite a different terminal state
    # (e.g. a user cancel racing the scanner's own completion). A
    # matching status is allowed through: the final ingest batch
    # already marks single-scanner scans "completed" before the agent
    # posts /complete, and that call still has to release the lease,
    # flush counters/Meilisearch and record reachability.
    conflict = (
        scan.status in ("completed", "failed", "cancelled")
        and scan.status != body.status
    )
    if not conflict:
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
    if scan.cancellation_reason is None and not conflict:
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

    if conflict:
        # Counters flushed, Meili drained, lease released; the scan keeps
        # its existing terminal status.
        await db.commit()
        raise HTTPException(
            status_code=409, detail=f"scan already {scan.status}",
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
# triggers /api/v1/sources/{id}/test-scanners (or its scanner-side
# mirror), the API publishes one ProbeRequest per scanner. The agent
# runs the probe and POSTs back to /probes/{request_id}/report. The
# report fans out to (a) the dispatcher waiting on the originating
# /test-… call, and (b) any frontend subscribed to the per-source
# reachability event channel.


@router.get(
    "/scanners/{scanner_id}/probes/long-poll",
    response_model=ProbeRequest | None,
    responses={204: {"description": "No probe within the poll window; reconnect."}},
    summary="Long-poll for a reachability probe",
    description=(
        "Scanner protocol. Blocks up to 30 seconds until the server asks this scanner to test whether "
        "it can reach a (local) source, then returns the probe with the merged connection config; "
        "answer it with `POST /scanners/{scanner_id}/probes/{request_id}/report`. Returns 204 when "
        "none arrived; reconnect immediately. Also marks the scanner seen.\n"
        "Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an "
        "Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the "
        "scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock "
        "skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.\n"
        "Errors: 403 if `scanner_id` is not the authenticated scanner."
    ),
)
async def long_poll_probes(
    scanner_id: Annotated[uuid.UUID, Path(description="The calling scanner's own id.")],
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


@router.get(
    "/scanners/{scanner_id}/scans/long-poll",
    response_model=LeasedScan | None,
    responses={204: {"description": "No scan to join within the poll window; reconnect."}},
    summary="Long-poll for a scan to join",
    description=(
        "Scanner protocol, parallel scans. Blocks up to 30 seconds until a scan on a source with "
        "`max_parallel_scanners` above 1 needs more scanners, then returns the same payload as `POST "
        "/scans/lease` so the agent can dispatch it identically, then lease units with `POST "
        "/scans/{scan_id}/work/lease`. Returns 204 when there is nothing to join; reconnect "
        "immediately. Also marks the scanner seen.\n"
        "Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an "
        "Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the "
        "scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock "
        "skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.\n"
        "Errors: 403 if `scanner_id` is not the authenticated scanner."
    ),
)
async def long_poll_scan_joins(
    scanner_id: Annotated[uuid.UUID, Path(description="The calling scanner's own id.")],
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
    payload mirrors ``/api/v1/scans/lease`` exactly so the agent can reuse
    its dispatch code without branching.

    Auth: scanner JWT; scanner_id in the URL must match the JWT subject
    so a compromised scanner can't subscribe to another scanner's
    queue.
    """
    if scanner_id != scanner.id:
        raise HTTPException(status_code=403, detail="scanner_id mismatch")
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
    "/scanners/{scanner_id}/probes/{request_id}/report",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Report a probe result",
    description=(
        "Scanner protocol. Delivers the outcome of a probe received from the probe long-poll: stored "
        "as the scanner's latest reachability for the source and forwarded to whoever requested the "
        "test.\n"
        "Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an "
        "Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the "
        "scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock "
        "skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.\n"
        "Errors: 403 if `scanner_id` is not the authenticated scanner; 404 if the source does not "
        "exist; 422 for a malformed body."
    ),
)
async def report_probe_result(
    scanner_id: Annotated[uuid.UUID, Path(description="The calling scanner's own id.")],
    request_id: Annotated[uuid.UUID, Path(description="Probe request id from the long-poll payload.")],
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


# ── Scanner-facing OAuth refresh (v0.14.0) ──────────────────────────────────


class OAuthAccessTokenRequest(ApiModel):
    source_id: uuid.UUID = Field(description="Leased source to mint a token for.")

    model_config = {"json_schema_extra": {'examples': [{'source_id': '3fa85f64-5717-4562-b3fc-2c963f66afa6'}]}}


class OAuthAccessTokenResponse(ApiModel):
    access_token: str = Field(description="Fresh provider access token.")
    expires_at: datetime | None = Field(description="When the access token expires, if the provider says.")


@router.post(
    "/scanners/oauth/access-token",
    response_model=OAuthAccessTokenResponse,
    summary="Mint an OAuth access token for a leased source",
    description=(
        "Scanner protocol. Returns a fresh access token for an OAuth-backed source (for example "
        "Google Drive) when a long scan outlives the token delivered with the lease. Only a scanner "
        "currently holding a running scan or unit lease on the source may ask.\n"
        "Authenticated by the scanner's own per-request JWT (`Authorization: Bearer <jwt>`): an "
        "Ed25519-signed (`alg=EdDSA`) token with `iss=scanner`, `sub` and header `kid` set to the "
        "scanner id, a `jti` that may be used once, and `exp` at most 5 minutes ahead (30 s clock "
        "skew tolerated). 401 for any invalid, expired, replayed or unknown-scanner token, and for a disabled scanner.\n"
        "Errors: 403 if the scanner has no active lease on the source; 404 if the source has no "
        "connected OAuth credential; 502 if the provider refused the refresh; 422 for a malformed "
        "body."
    ),
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
