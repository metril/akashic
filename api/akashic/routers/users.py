import uuid
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
from akashic.schemas.user import MeResponse, UserCreate, UserLogin, UserResponse, UserRoleUpdate, TokenResponse
from akashic.services.audit import record_event
from akashic.services.role_sync import lock_roles, other_admin_exists

router = ApiRouter(prefix="/users", tags=["users"])

_REGISTER_LOCK_KEY = 0x414B5348_01  # arbitrary constant: "AKSH"+1


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register the first admin",
    description=(
        "Bootstrap endpoint: creates the very first account, which becomes an `admin`. Public, but "
        "only works while no user exists; afterwards registration is closed and accounts are created "
        "by an admin with `POST /users/create`. Concurrent calls are serialized so only one becomes "
        "admin.\n"
        "Errors: 403 once any user exists; 409 if the username is taken; 422 for an invalid username "
        "or password (3-64 character username, 8-128 character password of at most 72 UTF-8 bytes)."
    ),
)
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


@router.post(
    "/create",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user",
    description=(
        "Creates a new local account with the given role (`viewer` unless `role` says otherwise).\n"
        "Requires scope `users:write`. Errors: 409 if the username is taken; 422 for an invalid "
        "username or password (3-64 character username, 8-128 character password of at most 72 UTF-8 "
        "bytes)."
    ),
)
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
        role=data.role,
        role_pinned=data.role == "admin",
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


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in with a password",
    description=(
        "Verifies a local username and password. Returns a short-lived JWT access token and sets the "
        "`akashic_refresh` HttpOnly cookie (scoped to `/api/v1/auth`) that `POST /auth/refresh` "
        "exchanges for new access tokens. Public.\n"
        "Rate limited to 10 attempts per minute per username. Errors: 401 for an unknown user or "
        "wrong password; 422 for a malformed body; 429 when rate limited."
    ),
)
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
    description=(
        "Every user account, ordered by username, with its role. Password hashes are never returned. "
        "Not paginated.\n"
        "Requires scope `users:read`. Not available to source-limited tokens."
    ),
)
async def list_users(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("users:read"),
) -> list[User]:
    return list((await db.execute(select(User).order_by(User.username))).scalars().all())


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="Change a user's role",
    description=(
        "Sets a user's role (`viewer` or `admin`) and/or whether it is pinned. Setting `role` pins the "
        "user unless `role_pinned` is also given; a pinned role is never changed by IdP group mapping "
        "(`oidc_admin_groups`, `ldap_admin_groups`). `role_pinned: false` hands the role back to the IdP "
        "at the next login. Takes effect immediately, API tokens included. Recorded as a "
        "`user_role_changed` audit event.\n"
        "Requires scope `users:write`. Errors: 404 for an unknown user; 409 when the change would leave "
        "no admin; 422 when neither `role` nor `role_pinned` is given."
    ),
)
async def update_user_role(
    user_id: uuid.UUID,
    data: UserRoleUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("users:write"),
) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    await lock_roles(db, user)
    old_role = user.role
    new_role = data.role or old_role
    if old_role == "admin" and new_role != "admin" and not await other_admin_exists(db, user):
        raise HTTPException(status_code=409, detail="Cannot demote the last admin")
    pinned = data.role_pinned if data.role_pinned is not None else (True if data.role else user.role_pinned)
    changed = new_role != old_role or pinned != user.role_pinned
    user.role = new_role
    user.role_pinned = pinned
    if changed:
        await record_event(
            db=db, user=principal.user, event_type="user_role_changed", request=request, commit=False,
            payload={
                "target_user_id": str(user.id), "username": user.username,
                "old": old_role, "new": new_role, "pinned": pinned, "via": "manual",
            },
        )
    await db.commit()
    await db.refresh(user)
    return user


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
