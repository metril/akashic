"""OIDC/SSO authentication support (Authentik, Keycloak, Authelia, Google, etc.)

Uses the authorization code flow:
  1. Redirect the user to the provider via get_authorization_url().
  2. Provider redirects back to the callback with a ?code= parameter.
  3. exchange_code() swaps the code for tokens and decodes the ID token.
  4. get_or_create_user() resolves (or provisions) a local User record.
"""

import secrets
from urllib.parse import urlencode

import httpx
from jose import jwt as jose_jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.config import settings
from akashic.models.user import User


# ---------------------------------------------------------------------------
# Discovery / JWKS helpers
# ---------------------------------------------------------------------------

_discovery_cache: dict | None = None
_jwks_cache: dict | None = None


async def _get_discovery() -> dict:
    """Fetch (and cache) the OIDC discovery document."""
    global _discovery_cache
    if _discovery_cache is None:
        async with httpx.AsyncClient() as client:
            resp = await client.get(settings.oidc_discovery_url, timeout=10)
            resp.raise_for_status()
            _discovery_cache = resp.json()
    return _discovery_cache


async def _get_jwks() -> dict:
    """Fetch (and cache) the provider's JWKS for ID-token verification."""
    global _jwks_cache
    if _jwks_cache is None:
        discovery = await _get_discovery()
        async with httpx.AsyncClient() as client:
            resp = await client.get(discovery["jwks_uri"], timeout=10)
            resp.raise_for_status()
            _jwks_cache = resp.json()
    return _jwks_cache


def invalidate_cache() -> None:
    """Force a refresh of the discovery / JWKS cache (useful in tests)."""
    global _discovery_cache, _jwks_cache
    _discovery_cache = None
    _jwks_cache = None


# ---------------------------------------------------------------------------
# Authorization URL
# ---------------------------------------------------------------------------


async def get_authorization_url(state: str | None = None) -> str:
    """Build the redirect URL that sends the user to the OIDC provider.

    A random *state* value is generated when one is not supplied; callers
    should persist it in a short-lived cookie or session so the callback can
    verify it.
    """
    discovery = await _get_discovery()
    authorization_endpoint = discovery["authorization_endpoint"]

    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": "openid email profile",
        "state": state or secrets.token_urlsafe(32),
    }
    return f"{authorization_endpoint}?{urlencode(params)}"


# ---------------------------------------------------------------------------
# Code exchange
# ---------------------------------------------------------------------------


async def exchange_code(code: str) -> dict:
    """Exchange an authorization code for tokens; return the decoded ID-token claims.

    The ID token signature is verified against the provider's JWKS.

    Raises:
        httpx.HTTPStatusError: if the token endpoint returns a non-2xx status.
        jose.JWTError: if the ID token is invalid / cannot be decoded.
    """
    discovery = await _get_discovery()
    token_endpoint = discovery["token_endpoint"]
    issuer = discovery["issuer"]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.oidc_redirect_uri,
                "client_id": settings.oidc_client_id,
                "client_secret": settings.oidc_client_secret,
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        token_response = resp.json()

    id_token = token_response.get("id_token")
    if not id_token:
        raise ValueError("No id_token in token response")

    jwks = await _get_jwks()

    claims: dict = jose_jwt.decode(
        id_token,
        jwks,
        algorithms=["RS256", "ES256", "RS384", "ES384", "RS512"],
        audience=settings.oidc_client_id,
        issuer=issuer,
    )
    return claims


# ---------------------------------------------------------------------------
# User provisioning
# ---------------------------------------------------------------------------


async def get_or_create_user(db: AsyncSession, id_token_claims: dict) -> User:
    """Find an existing user by external_id, or auto-provision a new one.

    The *sub* claim from the ID token is stored as ``external_id``.
    New users are given the ``viewer`` role.
    """
    sub = id_token_claims.get("sub")
    if not sub:
        raise ValueError("ID token missing 'sub' claim")

    # Try to find by (provider, external_id)
    result = await db.execute(
        select(User).where(
            User.auth_provider == "oidc",
            User.external_id == sub,
        )
    )
    user = result.scalar_one_or_none()

    if user is None:
        # Derive a username — prefer preferred_username, fall back to email
        # prefix, then sub itself.
        preferred = (
            id_token_claims.get("preferred_username")
            or id_token_claims.get("name")
            or (id_token_claims.get("email") or "").split("@")[0]
            or sub
        )
        # Ensure uniqueness: append a counter suffix if the name is taken
        username = preferred
        counter = 1
        while True:
            existing = await db.execute(select(User).where(User.username == username))
            if existing.scalar_one_or_none() is None:
                break
            username = f"{preferred}_{counter}"
            counter += 1

        user = User(
            username=username,
            email=id_token_claims.get("email"),
            role="viewer",
            auth_provider="oidc",
            external_id=sub,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    return user
