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
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import create_access_token
from akashic.config import settings
from akashic.database import get_db

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
async def get_providers() -> ProvidersResponse:
    """Return which authentication providers are currently enabled."""
    return ProvidersResponse(
        local=True,
        oidc=settings.oidc_enabled,
        ldap=settings.ldap_enabled,
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
    code: str = Query(..., description="Authorization code returned by the OIDC provider"),
    state: str | None = Query(None),
    oidc_state: str | None = Cookie(None),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Handle the OIDC callback: exchange the code for tokens and return a JWT."""
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

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token)


@router.post("/ldap/login", response_model=TokenResponse)
async def ldap_login(
    data: LDAPLoginRequest,
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

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token)
