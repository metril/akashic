"""Refresh-token issuance, rotation, and replay detection.

The contract callers rely on:

- `mint(user_id, db)` returns a (plain_token, RefreshToken) pair on
  every login. Plain token is what we set in an HttpOnly cookie; the
  hash is what we persist. The plain token is shown to the caller
  exactly once.

- `rotate(plain, db)` is called by /api/auth/refresh. On success it
  returns a new (plain_token, RefreshToken) and revokes the old row.
  On replay (the presented hash matches a row whose `revoked_at` is
  already set) we revoke the entire chain and return None — the
  caller turns that into a 401. Failing closed on replay matters more
  than legitimate-user friction.

- `revoke_chain(chain_id, db, reason)` is the explicit-logout path
  and the replay-detection cleanup path.

Hash form: sha256 hex. The peppered HMAC alternative would buy us
"DB-dump alone can't be replayed" but the access-token JWT signing
key is in the same DB row anyway — defending against partial dumps
isn't a useful goal here.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.config import settings
from akashic.models.refresh_token import RefreshToken


def _hash(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def _generate_token() -> str:
    # 256 bits of entropy in URL-safe form. Using token_urlsafe keeps
    # the cookie value compact and safe to put in a Set-Cookie header
    # without escaping.
    return secrets.token_urlsafe(32)


async def mint(
    user_id: uuid.UUID,
    db: AsyncSession,
    *,
    chain_id: uuid.UUID | None = None,
) -> tuple[str, RefreshToken]:
    """Issue a new refresh token. Pass `chain_id` to extend an existing
    chain (used by `rotate`); leave None for first-login mint."""
    plain = _generate_token()
    row = RefreshToken(
        id=uuid.uuid4(),
        user_id=user_id,
        chain_id=chain_id or uuid.uuid4(),
        token_hash=_hash(plain),
        expires_at=datetime.now(timezone.utc)
        + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(row)
    await db.flush()
    return plain, row


async def rotate(
    plain: str, db: AsyncSession,
) -> tuple[str, RefreshToken] | None:
    """Verify the presented refresh token, mark it as rotated, and mint
    a successor in the same chain. Returns None on any failure
    (expired / revoked / unknown / replay) — the auth router maps that
    to 401."""
    row = (await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == _hash(plain))
    )).scalar_one_or_none()
    if row is None:
        return None

    now = datetime.now(timezone.utc)
    if row.revoked_at is not None:
        # Replay: the presented token matched a row that's already
        # revoked. The most likely cause is a stolen token being used
        # after the legitimate user already rotated. Fail closed by
        # revoking the entire chain — both the attacker's session and
        # the legitimate one go away.
        await revoke_chain(row.chain_id, db, reason="replayed")
        return None
    if row.expires_at <= now:
        return None

    # Mint the successor first (so a crash mid-rotate doesn't leave
    # the user with a revoked token and no replacement).
    new_plain, new_row = await mint(row.user_id, db, chain_id=row.chain_id)
    row.revoked_at = now
    row.revoke_reason = "rotated"
    return new_plain, new_row


async def revoke_chain(
    chain_id: uuid.UUID, db: AsyncSession, *, reason: str,
) -> None:
    """Revoke every non-revoked row sharing this chain_id."""
    await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.chain_id == chain_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(timezone.utc), revoke_reason=reason)
    )


async def revoke(plain: str, db: AsyncSession) -> bool:
    """Revoke the token whose hash matches `plain`. Returns True if a
    row was actually revoked. Idempotent — re-presenting an already
    revoked token returns False without raising."""
    row = (await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == _hash(plain))
    )).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.now(timezone.utc)
    row.revoke_reason = "logout"
    return True
