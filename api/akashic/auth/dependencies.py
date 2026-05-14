import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import decode_access_token, decode_ingest_token
from akashic.database import get_db
from akashic.models.user import User, SourcePermission

security = HTTPBearer()


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
    credentials: HTTPAuthorizationCredentials = Depends(security),
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
_optional_bearer = HTTPBearer(auto_error=False)


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
    """Check if user has access to a source. Admins bypass all checks."""
    if user.role == "admin":
        return
    result = await db.execute(
        select(SourcePermission).where(
            SourcePermission.user_id == user.id,
            SourcePermission.source_id == source_id,
        )
    )
    perm = result.scalar_one_or_none()
    if perm is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this source")

    # Enforce access level hierarchy: read < write < admin
    level_hierarchy = {"read": 0, "write": 1, "admin": 2}
    user_level = level_hierarchy.get(perm.access_level, 0)
    required = level_hierarchy.get(required_level, 0)
    if user_level < required:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Requires {required_level} access to this source")


async def get_permitted_source_ids(user: User, db: AsyncSession) -> list[uuid.UUID] | None:
    """Return list of source IDs the user can access, or None if admin (no filtering needed)."""
    if user.role == "admin":
        return None  # Admin sees everything
    result = await db.execute(
        select(SourcePermission.source_id).where(SourcePermission.user_id == user.id)
    )
    return [row[0] for row in result.all()]
