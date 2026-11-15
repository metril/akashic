import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import decode_access_token, decode_ingest_token
from akashic.database import get_db
from akashic.models.user import User

security = HTTPBearer(
    scheme_name="userJWT", bearerFormat="JWT",
    description="Access token from `POST /api/auth/login` or `/api/auth/refresh`.",
)
_ingest_security = HTTPBearer(
    scheme_name="ingestJWT", bearerFormat="JWT",
    description="Ingest-scoped token leased to a scanner for one scan. Internal.",
)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def get_ingest_user(
    credentials: HTTPAuthorizationCredentials = Depends(_ingest_security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the user from a token minted by create_ingest_token.

    Distinct from get_current_user so an ingest-scoped token (handed
    to the scanner agent) cannot be presented to admin endpoints, and
    a regular user token cannot satisfy the ingest dependency.

    The user identity is still loaded — ingest needs an authenticated
    actor for ACL/audit purposes — but the audience boundary keeps
    each token confined to its issued purpose (review A-C1)."""
    payload = decode_ingest_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


# Optional-credentials bearer for the scanner_id extractor below.
# We deliberately don't raise on missing creds — the real auth gate
# is `get_ingest_user`; this dep is purely "if a valid ingest JWT
# carried a scanner_id claim, surface it". Tests that override
# `get_ingest_user` (and therefore never present a real Authorization
# header) must not 401 just because this companion dep also runs.
_optional_bearer = HTTPBearer(auto_error=False, scheme_name="ingestJWT", bearerFormat="JWT")


async def get_ingest_scanner_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
) -> uuid.UUID | None:
    """Extract the scanner_id claim (if any) from an ingest JWT.

    v0.28.2 — minted into the token at lease time by `_mint_ingest_jwt`
    so scan-progress POSTs can attribute heartbeat / log / stderr rows
    to the scanner that actually produced them, without trusting a
    client-supplied header.

    Returns None for legacy tokens that predate the claim (rows still
    persist, just with scanner_id NULL — the column is nullable) and
    for requests where no bearer is presented (auth is enforced by
    `get_ingest_user` separately; this dep only annotates).
    """
    if credentials is None:
        return None
    payload = decode_ingest_token(credentials.credentials)
    if payload is None:
        return None
    sid = payload.get("scanner_id")
    if sid is None:
        return None
    try:
        return uuid.UUID(sid)
    except (TypeError, ValueError):
        return None


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
    return user


async def check_source_access(
    source_id: uuid.UUID,
    user: User,
    db: AsyncSession,
    required_level: str = "read",
) -> None:
    """Any authenticated user may read any source; write/admin levels
    require the admin role. (File-level visibility is narrowed by
    filesystem-ACL filtering, not by per-source grants.)"""
    if required_level in ("write", "admin") and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires {required_level} access to this source",
        )
