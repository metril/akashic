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

from fastapi import APIRouter, Depends, HTTPException, Response, status
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


class ScannerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    pool: str = Field(default="default", min_length=1, max_length=64)


class ScannerPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    pool: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None


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
    await db.commit()
    await db.refresh(scanner)
    return _to_summary(scanner)


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
    lease_sql = text("""
        WITH next_scan AS (
            SELECT id FROM scans
             WHERE status IN ('pending', 'running')
               AND (assigned_scanner_id IS NULL OR lease_expires_at < now())
               AND (pool = :pool OR pool IS NULL)
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
    res = await db.execute(
        lease_sql,
        {
            "pool": scanner.pool,
            "scanner_id": scanner.id,
            "lease_seconds": _LEASE_DURATION_SECONDS,
        },
    )
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
