import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import decode_access_token
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
