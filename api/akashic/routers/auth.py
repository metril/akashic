"""Authentication router — OIDC, LDAP, and provider-discovery endpoints.

Endpoints
---------
GET  /api/auth/providers          — list enabled auth providers
GET  /api/auth/oidc/login         — redirect to OIDC provider
GET  /api/auth/oidc/callback      — handle OIDC callback, return JWT
POST /api/auth/ldap/login         — LDAP username/password -> JWT
"""

import asyncio
import logging
import secrets

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import create_access_token
from akashic.auth import refresh as refresh_service
from akashic.config import settings
from akashic.database import get_db
from akashic.models.user import User
from akashic.services.audit import record_event


REFRESH_COOKIE = "akashic_refresh"


def _set_refresh_cookie(response: Response, plain_token: str) -> None:
    """HttpOnly + SameSite=Lax cookie. Path=/api/auth so it's only
    sent to refresh / logout — minimizes accidental exposure on
    other endpoints."""
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=plain_token,
        httponly=True,
        samesite="lax",
        secure=False,  # Deployments behind TLS should set Secure via reverse-proxy.
        path="/api/auth",
        max_age=settings.refresh_token_expire_days * 24 * 3600,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LDAPLoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ProvidersResponse(BaseModel):
    local: bool = True
    oidc: bool
    ldap: bool
    # True when the users table is empty — bootstrap registration via
    # POST /api/users/register is open. Flips to False the moment the
    # first user is created and stays False forever (the register
    # endpoint enforces the same one-way door).
    setup_required: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_oidc() -> None:
    if not settings.oidc_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OIDC authentication is not enabled",
        )


def _require_ldap() -> None:
    if not settings.ldap_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LDAP authentication is not enabled",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/providers", response_model=ProvidersResponse)
async def get_providers(db: AsyncSession = Depends(get_db)) -> ProvidersResponse:
    """Return which authentication providers are currently enabled, plus
    whether bootstrap registration is still open (no users yet)."""
    count_result = await db.execute(select(func.count(User.id)))
    user_count = count_result.scalar() or 0
    return ProvidersResponse(
        local=True,
        oidc=settings.oidc_enabled,
        ldap=settings.ldap_enabled,
        setup_required=user_count == 0,
    )


@router.get("/oidc/login")
async def oidc_login() -> Response:
    """Redirect the user to the configured OIDC provider for authentication."""
    _require_oidc()

    from akashic.auth.oidc import get_authorization_url

    state = secrets.token_urlsafe(32)

    try:
        url = await get_authorization_url(state=state)
    except Exception as exc:
        logger.error("Failed to build OIDC authorization URL: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to reach OIDC provider",
        ) from exc

    response = RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="oidc_state",
        value=state,
        max_age=600,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/oidc/callback", response_model=TokenResponse)
async def oidc_callback(
    response: Response,
    code: str = Query(..., description="Authorization code returned by the OIDC provider"),
    state: str | None = Query(None),
    oidc_state: str | None = Cookie(None),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Handle the OIDC callback: exchange the code for tokens, mint
    access + refresh, set the refresh cookie, and return the access
    token to the SPA."""
    _require_oidc()

    # Validate state parameter to prevent CSRF
    if not state or not oidc_state or state != oidc_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or missing state parameter",
        )

    from akashic.auth.oidc import exchange_code, get_or_create_user

    try:
        claims = await exchange_code(code)
    except Exception as exc:
        logger.warning("OIDC code exchange failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OIDC authentication failed",
        ) from exc

    try:
        user = await get_or_create_user(db, claims)
    except Exception as exc:
        logger.error("Failed to provision OIDC user: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User provisioning failed",
        ) from exc

    plain_refresh, _ = await refresh_service.mint(user.id, db)
    await record_event(
        db=db, user=user,
        event_type="oidc_login_success",
        payload={"sub": claims.get("sub"), "preferred_username": claims.get("preferred_username")},
    )
    await db.commit()

    token = create_access_token({"sub": str(user.id)})
    _set_refresh_cookie(response, plain_refresh)
    return TokenResponse(access_token=token)


@router.post("/ldap/login", response_model=TokenResponse)
async def ldap_login(
    data: LDAPLoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Authenticate via LDAP bind and return a JWT on success."""
    _require_ldap()

    from akashic.auth.ldap import authenticate_ldap, get_or_create_user

    # python-ldap is synchronous — run in thread pool to avoid blocking
    try:
        ldap_info = await asyncio.to_thread(
            authenticate_ldap, data.username, data.password
        )
    except Exception as exc:
        logger.error("LDAP authentication error for user %s: %s", data.username, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="LDAP authentication error",
        ) from exc

    if ldap_info is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid LDAP credentials",
        )

    try:
        user = await get_or_create_user(db, ldap_info)
    except Exception as exc:
        logger.error("Failed to provision LDAP user %s: %s", data.username, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User provisioning failed",
        ) from exc

    plain_refresh, _ = await refresh_service.mint(user.id, db)
    await db.commit()
    token = create_access_token({"sub": str(user.id)})
    _set_refresh_cookie(response, plain_refresh)
    return TokenResponse(access_token=token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    response: Response,
    akashic_refresh: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Mint a new access token from a valid refresh cookie. Rotates the
    refresh row on every call. Returns 401 on missing/expired/replayed
    tokens — the web client treats that as a hard logout."""
    if not akashic_refresh:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No refresh token",
        )

    rotated = await refresh_service.rotate(akashic_refresh, db)
    if rotated is None:
        # Could be expired, unknown, or replay-detected. Either way we
        # clear the bad cookie so the next request doesn't re-trigger.
        await db.commit()
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token invalid",
        )

    new_plain, new_row = rotated
    await db.commit()
    access = create_access_token({"sub": str(new_row.user_id)})
    _set_refresh_cookie(response, new_plain)
    return TokenResponse(access_token=access)


@router.post("/logout")
async def logout(
    response: Response,
    akashic_refresh: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Revoke the presented refresh token and clear the cookie. The
    short-lived access JWT keeps working until its TTL expires; logout
    only kills the long-lived chain. Idempotent — calling without a
    cookie is a no-op success."""
    if akashic_refresh:
        await refresh_service.revoke(akashic_refresh, db)
        await db.commit()
    _clear_refresh_cookie(response)
    return {"ok": True}
