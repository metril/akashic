from datetime import datetime, timezone

from akashic.routing import ApiRouter
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import Principal, get_principal, require_scope
from akashic.auth.jwt import create_access_token
from akashic.auth.passwords import hash_password, verify_password
from akashic.auth import refresh as refresh_service
from akashic.database import get_db
from akashic.models.user import User
from akashic.routers.auth import _set_refresh_cookie
from akashic.services.rate_limit import make_limiter
from akashic.schemas.user import MeResponse, UserCreate, UserLogin, UserResponse, TokenResponse

router = ApiRouter(prefix="/users", tags=["users"])

_REGISTER_LOCK_KEY = 0x414B5348_01  # arbitrary constant: "AKSH"+1


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate, db: AsyncSession = Depends(get_db)):
    # Serialize bootstrap: without the lock two concurrent registers both
    # see count==0 and both become admin. Released at commit/rollback.
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _REGISTER_LOCK_KEY})
    # Check if this is the first user (bootstrap)
    count_result = await db.execute(select(func.count(User.id)))
    user_count = count_result.scalar()

    if user_count > 0:
        # After bootstrap, registration requires admin auth
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration closed. Ask an admin to create your account.",
        )

    existing = await db.execute(select(User).where(User.username == data.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username taken")
    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        role="admin",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/create", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    data: UserCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("users:write"),
):
    """Admin-only: create a new user account."""
    existing = await db.execute(select(User).where(User.username == data.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username taken")
    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        role="viewer",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


_login_limiter = make_limiter(
    max_requests=10,
    window_seconds=60.0,
    message="too many login attempts; wait a minute and try again",
)


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    data: UserLogin,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    _login_limiter.check(request, data.username.lower())
    result = await db.execute(select(User).where(User.username == data.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    user.last_login_at = datetime.now(timezone.utc)
    plain_refresh, _ = await refresh_service.mint(user.id, db)
    await db.commit()
    token = create_access_token({"sub": str(user.id)})
    _set_refresh_cookie(response, plain_refresh)
    return TokenResponse(access_token=token)


@router.get(
    "",
    response_model=list[UserResponse],
    summary="List users",
    description="Every user account, ordered by username.",
)
async def list_users(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("users:read"),
) -> list[User]:
    return list((await db.execute(select(User).order_by(User.username))).scalars().all())


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Get the calling user",
    description=(
        "The authenticated user and the effective scopes of the credential used. "
        "Requires authentication but no particular scope."
    ),
)
async def get_me(principal: Principal = Depends(get_principal)) -> MeResponse:
    return MeResponse.model_validate(
        {**UserResponse.model_validate(principal.user).model_dump(), "scopes": sorted(principal.scopes)}
    )
