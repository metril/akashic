"""Scanner claim-token mint + redeem.

The plain `akcl_…` token crosses the wire exactly twice:
  1. server → admin (response to POST /api/scanner-claim-tokens)
  2. admin → scanner host → server (POST /api/scanners/claim body)

Only the sha256 of the plaintext is persisted. Hash-only storage
mirrors `auth/refresh.py` so a DB dump alone can't be replayed.

Token format: `akcl_` + url-safe-base64(32 random bytes). The
prefix matches GitHub's `ghp_*`/`gho_*` convention so leaked
tokens are obviously identifiable in logs and secret scanners.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.scanner_claim_token import ScannerClaimToken


TOKEN_PREFIX = "akcl_"


def _hash(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def _generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


async def mint_token(
    *,
    db: AsyncSession,
    label: str,
    pool: str,
    ttl_minutes: int,
    created_by_user_id: uuid.UUID | None,
    allowed_source_ids: list[uuid.UUID] | None = None,
    allowed_scan_types: list[str] | None = None,
) -> tuple[str, ScannerClaimToken]:
    plain = _generate_token()
    row = ScannerClaimToken(
        id=uuid.uuid4(),
        token_hash=_hash(plain),
        label=label,
        pool=pool,
        allowed_source_ids=allowed_source_ids,
        allowed_scan_types=allowed_scan_types,
        created_by_user_id=created_by_user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
    )
    db.add(row)
    await db.flush()
    return plain, row


class ClaimError(Exception):
    """Raised by `lookup_for_claim` to signal why a token can't be redeemed.

    The message is the operator-facing reason; HTTP layer maps the
    instance to a 401/410 with the message as the detail.
    """

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


async def lookup_for_claim(
    db: AsyncSession, plain_token: str,
) -> ScannerClaimToken:
    """Return the token row, or raise ClaimError with the appropriate
    HTTP status. Specifically:
      - garbage / unknown hash → 401 (don't leak hash existence)
      - expired or already used → 410 Gone with a clear reason
    """
    row = (await db.execute(
        select(ScannerClaimToken).where(
            ScannerClaimToken.token_hash == _hash(plain_token),
        )
    )).scalar_one_or_none()
    if row is None:
        raise ClaimError(401, "invalid claim token")
    now = datetime.now(timezone.utc)
    if row.used_at is not None:
        raise ClaimError(410, "claim token has already been used")
    if row.expires_at <= now:
        raise ClaimError(410, "claim token has expired")
    return row


def derive_status(row: ScannerClaimToken) -> str:
    """Compute the operator-facing status string for the list endpoint."""
    if row.used_at is not None:
        return "used"
    if row.expires_at <= datetime.now(timezone.utc):
        return "expired"
    return "active"
