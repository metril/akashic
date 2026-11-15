"""Authentication router — OIDC, LDAP, and provider-discovery endpoints.

Endpoints
---------
GET  /api/v1/auth/providers          — list enabled auth providers
GET  /api/v1/auth/oidc/login         — redirect to OIDC provider
GET  /api/v1/auth/oidc/callback      — handle OIDC callback, set refresh cookie, 302 to SPA
POST /api/v1/auth/ldap/login         — LDAP username/password -> JWT
"""

import asyncio
import logging
import secrets

from akashic.routing import ApiRouter
from fastapi import Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from urllib.parse import urlencode
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import create_access_token
from akashic.auth import refresh as refresh_service
from akashic.services.runtime_settings import rt
from akashic.database import get_db
from akashic.models.user import User
from akashic.services.audit import record_event
from akashic.services.rate_limit import make_limiter
from akashic.schemas.user import TokenResponse


REFRESH_COOKIE = "akashic_refresh"


def _set_refresh_cookie(response: Response, plain_token: str) -> None:
    """HttpOnly + SameSite=Lax cookie. Path=/api/v1/auth so it's only
    sent to refresh / logout — minimizes accidental exposure on
    other endpoints. The Secure flag is gated on rt().cookie_secure
    (default True) so production deployments behind TLS get the flag
    out of the box; local-dev http:// setups must set
    COOKIE_SECURE=false explicitly (review A-N3)."""
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=plain_token,
        httponly=True,
        samesite="lax",
        secure=rt().cookie_secure,
        path="/api/v1/auth",
        max_age=rt().refresh_token_expire_days * 24 * 3600,
    )


_FLOW_COOKIES = ("oidc_state", "oidc_pkce", "oidc_nonce")


def _set_flow_cookie(resp: Response, key: str, value: str) -> None:
    resp.set_cookie(
        key=key,
        value=value,
        max_age=600,
        httponly=True,
        samesite="lax",
        # Secure on a TLS deployment (review A-I1) so passive sniffing
        # on a misconfigured-HTTP segment can't lift the flow cookies
        # and replay the OIDC callback.
        secure=rt().cookie_secure,
    )


def _spa_redirect(path: str, **params: str) -> RedirectResponse:
    """302 to a fixed SPA path (optionally with query params). Targets come
    only from config + fixed paths, never from request input."""
    url = rt().frontend_url.rstrip("/") + path
    if params:
        url += "?" + urlencode(params)
    resp = RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
    # The one-shot state cookie is spent either way once we leave the
    # callback; drop it with the same attributes it was set with.
    for key in _FLOW_COOKIES:
        resp.delete_cookie(
            key, httponly=True, samesite="lax", secure=rt().cookie_secure
        )
    return resp


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")

logger = logging.getLogger(__name__)

router = ApiRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LDAPLoginRequest(BaseModel):
    username: str = Field(description="LDAP username.")
    password: str = Field(min_length=1, description="LDAP password.")

    model_config = {"json_schema_extra": {'examples': [{'username': 'alice', 'password': 'correct horse battery staple'}]}}


class LogoutResponse(BaseModel):
    ok: bool = Field(description="Always true; logout is idempotent.")

    model_config = {"json_schema_extra": {'examples': [{'ok': True}]}}


class ProvidersResponse(BaseModel):
    local: bool = Field(default=True, description="Local username and password login; always available.")
    oidc: bool = Field(description="Whether OIDC single sign-on is enabled.")
    ldap: bool = Field(description="Whether LDAP login is enabled.")
    # True when the users table is empty — bootstrap registration via
    # POST /api/v1/users/register is open. Flips to False the moment the
    # first user is created and stays False forever (the register
    # endpoint enforces the same one-way door).
    setup_required: bool = Field(description="True while no user exists and first-run registration is open; it never becomes true again once the first user is created.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_oidc() -> None:
    if not rt().oidc_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OIDC authentication is not enabled",
        )


def _require_ldap() -> None:
    if not rt().ldap_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LDAP authentication is not enabled",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/providers",
    response_model=ProvidersResponse,
    summary="List login providers",
    description=(
        "Public. Reports which authentication providers are enabled and whether first-run setup "
        "is still open (`setup_required`, true only while no user exists). The login page uses it "
        "to decide what to offer."
    ),
)
async def get_providers(db: AsyncSession = Depends(get_db)) -> ProvidersResponse:
    """Return which authentication providers are currently enabled, plus
    whether bootstrap registration is still open (no users yet)."""
    count_result = await db.execute(select(func.count(User.id)))
    user_count = count_result.scalar() or 0
    return ProvidersResponse(
        local=True,
        oidc=rt().oidc_enabled,
        ldap=rt().ldap_enabled,
        setup_required=user_count == 0,
    )


@router.get(
    "/oidc/login",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
    summary="Start OIDC login",
    responses={302: {"description": "Redirect to the OIDC provider; sets short-lived state cookies."}},
    description=(
        "Public. Redirects the browser (302) to the configured OIDC provider and sets short-lived "
        "state, PKCE and nonce cookies.\n"
        "Errors: 404 if OIDC is not enabled; 502 if the provider cannot be reached."
    ),
)
async def oidc_login() -> Response:
    """Redirect the user to the configured OIDC provider for authentication."""
    _require_oidc()

    from akashic.auth.oidc import generate_pkce, get_authorization_url

    state = secrets.token_urlsafe(32)
    verifier, challenge = generate_pkce()
    nonce = secrets.token_urlsafe(32)

    try:
        url = await get_authorization_url(
            state=state, code_challenge=challenge, nonce=nonce
        )
    except Exception as exc:
        logger.error("Failed to build OIDC authorization URL: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to reach OIDC provider",
        ) from exc

    response = RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
    _set_flow_cookie(response, "oidc_state", state)
    _set_flow_cookie(response, "oidc_pkce", verifier)
    _set_flow_cookie(response, "oidc_nonce", nonce)
    return response


@router.get(
    "/oidc/callback",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
    summary="Finish OIDC login",
    responses={302: {"description": "Redirect to the SPA; sets the refresh cookie, or `/login?error=<code>` on failure."}},
    description=(
        "Public. Exchanges the authorization code for tokens, provisions the user if needed, sets "
        "the HttpOnly refresh cookie and redirects (302) to the SPA's `/auth/callback`, which "
        "then calls `POST /api/v1/auth/refresh` for an access token. Records an "
        "`oidc_login_success` audit event. Failures redirect to `/login?error=<code>` with one of "
        "`sso_denied`, `sso_state`, `sso_failed` or `sso_provision`.\n"
        "Errors: 404 if OIDC is not enabled."
    ),
)
async def oidc_callback(
    code: str | None = Query(None, description="Authorization code returned by the OIDC provider."),
    state: str | None = Query(None, description="State value echoed by the provider; must match the `oidc_state` cookie."),
    error: str | None = Query(None, description="Error code returned by the provider when the user denied or the login failed."),
    oidc_state: str | None = Cookie(None, description="State cookie set by `/oidc/login`."),
    oidc_pkce: str | None = Cookie(None, description="PKCE verifier cookie set by `/oidc/login`."),
    oidc_nonce: str | None = Cookie(None, description="Nonce cookie set by `/oidc/login`."),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Handle the OIDC callback: exchange the code for tokens, mint a
    refresh token, set the HttpOnly refresh cookie, and 302 the browser
    to the SPA's /auth/callback (which trades the cookie for an access
    token via /api/v1/auth/refresh). Failures 302 to /login?error=<code>
    with one of sso_denied, sso_state, sso_failed, sso_provision."""
    _require_oidc()

    if error:
        logger.warning("OIDC provider returned error: %s", error)
        return _spa_redirect("/login", error="sso_denied")

    # Validate state parameter to prevent CSRF
    if not code or not state or not oidc_state or state != oidc_state:
        return _spa_redirect("/login", error="sso_state")
    if not oidc_pkce or not oidc_nonce:
        return _spa_redirect("/login", error="sso_state")

    from akashic.auth.oidc import exchange_code, get_or_create_user

    try:
        claims = await exchange_code(code, code_verifier=oidc_pkce, nonce=oidc_nonce)
    except Exception as exc:
        logger.warning("OIDC code exchange failed: %s", exc)
        return _spa_redirect("/login", error="sso_failed")

    try:
        user = await get_or_create_user(db, claims)
    except Exception as exc:
        logger.error("Failed to provision OIDC user: %s", exc)
        return _spa_redirect("/login", error="sso_provision")

    plain_refresh, _ = await refresh_service.mint(user.id, db)
    await record_event(
        db=db, user=user,
        event_type="oidc_login_success",
        payload={"sub": claims.get("sub"), "preferred_username": claims.get("preferred_username")},
    )
    await db.commit()

    resp = _spa_redirect("/auth/callback")
    _set_refresh_cookie(resp, plain_refresh)
    return resp


_ldap_login_limiter = make_limiter(
    max_requests=10,
    window_seconds=60.0,
    message="too many login attempts; wait a minute and try again",
)


@router.post(
    "/ldap/login",
    response_model=TokenResponse,
    summary="Log in with LDAP",
    description=(
        "Public. Authenticates by LDAP bind, provisions the user on first login, returns an "
        "access token and sets the HttpOnly refresh cookie. Records an `ldap_login_success` audit "
        "event. Rate limited to 10 attempts per minute per client and username.\n"
        "Errors: 401 for invalid credentials; 404 if LDAP is not enabled; 429 when rate limited; "
        "500 if the directory fails or the user cannot be provisioned."
    ),
)
async def ldap_login(
    request: Request,
    data: LDAPLoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Authenticate via LDAP bind and return a JWT on success."""
    _require_ldap()
    _ldap_login_limiter.check(request, data.username.lower())

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
    # LDAP login audit event (review A-I7) — without this, LDAP-
    # authenticated sessions are invisible to the audit trail and
    # admins can't see brute-force patterns or successful logins.
    # Mirrors the oidc_login_success event above.
    await record_event(
        db=db, user=user,
        event_type="ldap_login_success",
        payload={
            "username": data.username,
            "dn": ldap_info.get("dn") if isinstance(ldap_info, dict) else None,
        },
    )
    await db.commit()
    token = create_access_token({"sub": str(user.id)})
    _set_refresh_cookie(response, plain_refresh)
    return TokenResponse(access_token=token)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh the access token",
    description=(
        "Public, authenticated by the refresh cookie. Mints a new access token and rotates the "
        "refresh token on every call. A missing, expired, revoked or replayed refresh token "
        "returns 401, which clients should treat as a logout."
    ),
)
async def refresh_token(
    response: Response,
    akashic_refresh: str | None = Cookie(default=None, alias=REFRESH_COOKIE, description="HttpOnly refresh cookie set at login."),
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


@router.post(
    "/logout",
    response_model=LogoutResponse,
    summary="Log out",
    description=(
        "Public. Revokes the presented refresh token and clears the cookie. The short-lived "
        "access token keeps working until it expires. Idempotent: calling without a cookie "
        "succeeds."
    ),
)
async def logout(
    response: Response,
    akashic_refresh: str | None = Cookie(default=None, alias=REFRESH_COOKIE, description="HttpOnly refresh cookie set at login."),
    db: AsyncSession = Depends(get_db),
) -> LogoutResponse:
    """Revoke the presented refresh token and clear the cookie. The
    short-lived access JWT keeps working until its TTL expires; logout
    only kills the long-lived chain. Idempotent — calling without a
    cookie is a no-op success."""
    if akashic_refresh:
        await refresh_service.revoke(akashic_refresh, db)
        await db.commit()
    _clear_refresh_cookie(response)
    return LogoutResponse(ok=True)
