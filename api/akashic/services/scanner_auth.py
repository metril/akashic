"""Scanner-JWT verification dependency.

The agent presents `Authorization: Bearer <jwt>` on every call to the
lease / heartbeat / handshake / complete endpoints. The JWT is signed
with the scanner's Ed25519 private key (held only on the scanner
host); we verify it against the public key registered in the
`scanners` row.

`iss` is required to be `"scanner"` so a leaked user JWT can't
impersonate a scanner (and vice-versa). Token expiry is 5 minutes; we
accept ±30 s skew between the agent's clock and ours.
"""
from __future__ import annotations

import time
import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.database import get_db
from akashic.models.scanner import Scanner
from akashic.services.scanner_keys import peek_kid, verify_jwt


_CLOCK_SKEW_SECONDS = 30


def _bearer_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
        )
    return auth.split(" ", 1)[1].strip()


async def verify_scanner_jwt(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Scanner:
    """Resolve the calling scanner from a signed JWT. 401 on every
    failure — we never leak whether the kid was unknown vs the
    signature was wrong (timing aside, both look like 'invalid token')."""
    token = _bearer_token(request)

    kid = peek_kid(token)
    if not kid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token",
        )
    try:
        scanner_id = uuid.UUID(kid)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token",
        )

    result = await db.execute(select(Scanner).where(Scanner.id == scanner_id))
    scanner = result.scalar_one_or_none()
    if scanner is None or not scanner.enabled:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token",
        )

    try:
        claims = verify_jwt(scanner.public_key_pem, token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token",
        )

    if claims.get("iss") != "scanner":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token",
        )
    if str(claims.get("sub")) != str(scanner_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token",
        )
    now = int(time.time())
    exp = claims.get("exp")
    iat = claims.get("iat")
    if not isinstance(exp, int) or exp < now - _CLOCK_SKEW_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="token expired",
        )
    if isinstance(iat, int) and iat > now + _CLOCK_SKEW_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="token not yet valid",
        )

    return scanner
