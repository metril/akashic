"""Personal API tokens (``akp_...``): minting, lookup and per-request resolution.

Only a sha256 of the secret is stored. A presented token is located by its
indexed prefix and then compared in constant time against the stored hash.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.scopes import scopes_for_role
from akashic.models.api_token import ApiToken
from akashic.models.user import User
from akashic.services.audit import record_event

TOKEN_MARKER = "akp_"
_PREFIX_LEN = len(TOKEN_MARKER) + 8
_BODY_LEN = 43  # base62 digits needed for 32 bytes (62**43 > 2**256)
_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
# last_used_at/ip are refreshed at most this often, so a busy token is not a write per request.
USAGE_THROTTLE = timedelta(seconds=60)


def is_api_token(value: str) -> bool:
    return value.startswith(TOKEN_MARKER)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> tuple[str, str, str]:
    """Return ``(plaintext, prefix, sha256_hex)`` for a fresh token."""
    n = int.from_bytes(secrets.token_bytes(32), "big")
    digits: list[str] = []
    while n:
        n, r = divmod(n, 62)
        digits.append(_BASE62[r])
    body = "".join(reversed(digits)).rjust(_BODY_LEN, "0")
    token = TOKEN_MARKER + body
    return token, token[:_PREFIX_LEN], hash_token(token)


async def _find(token: str, db: AsyncSession) -> ApiToken | None:
    rows = (
        await db.execute(select(ApiToken).where(ApiToken.token_prefix == token[:_PREFIX_LEN]))
    ).scalars().all()
    digest = hash_token(token)
    match: ApiToken | None = None
    for row in rows:  # compare every candidate: no early exit on the first hit
        if hmac.compare_digest(row.token_hash, digest):
            match = row
    return match


async def _touch(row: ApiToken, user: User, ip: str, now: datetime, db: AsyncSession, request) -> None:
    """Record usage, at most once per ``USAGE_THROTTLE``; audit the first use."""
    first = row.last_used_at is None
    if first:
        cond = ApiToken.last_used_at.is_(None)
    elif now - row.last_used_at >= USAGE_THROTTLE:
        cond = ApiToken.last_used_at < now - USAGE_THROTTLE
    else:
        return
    res = await db.execute(
        update(ApiToken).where(ApiToken.id == row.id, cond)
        .values(last_used_at=now, last_used_ip=ip)
    )
    await db.commit()
    if first and res.rowcount == 1:
        await record_event(
            db=db, user=user, event_type="api_token_first_used",
            payload={"token_id": str(row.id), "name": row.name, "ip": ip}, request=request,
        )


async def principal_from_token(token: str, db: AsyncSession, request=None):
    """Resolve an ``akp_`` token to a Principal; None when it grants nothing.

    Effective scopes are the token's scopes intersected with the owner's
    *current* role bundle, so a demoted owner's tokens shrink immediately.
    """
    from akashic.auth.dependencies import Principal  # circular: Principal lives there

    row = await _find(token, db)
    if row is None or row.revoked_at is not None:
        return None
    now = datetime.now(timezone.utc)
    if row.expires_at is not None and row.expires_at <= now:
        return None
    user = (await db.execute(select(User).where(User.id == row.user_id))).scalar_one_or_none()
    if user is None:
        return None
    client = getattr(request, "client", None)
    await _touch(row, user, getattr(client, "host", "") or "", now, db, request)
    return Principal(
        user=user,
        scopes=frozenset(row.scopes) & scopes_for_role(user.role),
        allowed_source_ids=frozenset(row.source_ids) if row.source_ids is not None else None,
        via="token",
    )


def token_id(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        return None
