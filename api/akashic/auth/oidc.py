"""OIDC/SSO authentication support (Authentik, Keycloak, Authelia, Google, etc.)

Uses the authorization code flow:
  1. Redirect the user to the provider via get_authorization_url(), with a
     PKCE S256 code_challenge (see generate_pkce()) and a nonce.
  2. Provider redirects back to the callback with a ?code= parameter.
  3. exchange_code() swaps the code (plus PKCE code_verifier) for tokens,
     decodes the ID token and verifies its nonce.
  4. get_or_create_user() resolves (or provisions) a local User record.
"""

import asyncio
import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt as jose_jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.config import settings
from akashic.models.user import User


# ---------------------------------------------------------------------------
# Discovery / JWKS helpers
# ---------------------------------------------------------------------------

_discovery_cache: dict | None = None
_discovery_fetched_at: float = 0
_jwks_cache: dict | None = None
_jwks_fetched_at: float = 0
# 30 minutes (was 24h, review notable). Shorter TTL means an IdP key
# rotation propagates within minutes, not the next day. JWKS endpoints
# are cheap to fetch; no operational reason to keep the cache long-lived.
_CACHE_TTL_SECONDS = 1800

# One shared client (connection reuse) and a lock so concurrent logins
# collapse into a single discovery / JWKS fetch.
_client: httpx.AsyncClient | None = None
_cache_lock: asyncio.Lock | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient()
    return _client


def _get_lock() -> asyncio.Lock:
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


async def aclose() -> None:
    """Close the shared HTTP client (called from the app lifespan)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _get_discovery() -> dict:
    """Fetch (and cache with TTL) the OIDC discovery document."""
    global _discovery_cache, _discovery_fetched_at
    async with _get_lock():
        now = time.monotonic()
        if _discovery_cache is None or (now - _discovery_fetched_at) > _CACHE_TTL_SECONDS:
            resp = await _get_client().get(settings.oidc_discovery_url, timeout=10)
            resp.raise_for_status()
            _discovery_cache = resp.json()
            _discovery_fetched_at = now
        return _discovery_cache


async def _get_jwks() -> dict:
    """Fetch (and cache with TTL) the provider's JWKS for ID-token verification."""
    global _jwks_cache, _jwks_fetched_at
    discovery = await _get_discovery()
    async with _get_lock():
        now = time.monotonic()
        if _jwks_cache is None or (now - _jwks_fetched_at) > _CACHE_TTL_SECONDS:
            resp = await _get_client().get(discovery["jwks_uri"], timeout=10)
            resp.raise_for_status()
            _jwks_cache = resp.json()
            _jwks_fetched_at = now
        return _jwks_cache


def invalidate_cache() -> None:
    """Force a refresh of the discovery / JWKS cache (useful in tests)."""
    global _discovery_cache, _jwks_cache, _discovery_fetched_at, _jwks_fetched_at
    _discovery_cache = None
    _jwks_cache = None
    _discovery_fetched_at = 0
    _jwks_fetched_at = 0


# ---------------------------------------------------------------------------
# Authorization URL
# ---------------------------------------------------------------------------


def generate_pkce() -> tuple[str, str]:
    """Return a PKCE ``(code_verifier, code_challenge)`` pair (S256)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


async def get_authorization_url(
    state: str | None = None,
    code_challenge: str | None = None,
    nonce: str | None = None,
) -> str:
    """Build the redirect URL that sends the user to the OIDC provider.

    A random *state* value is generated when one is not supplied; callers
    should persist it in a short-lived cookie or session so the callback can
    verify it. When *code_challenge* is given it is sent with
    ``code_challenge_method=S256``; *nonce*, when given, is echoed by the
    provider in the ID token so the callback can verify it.
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
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    if nonce:
        params["nonce"] = nonce
    return f"{authorization_endpoint}?{urlencode(params)}"


# ---------------------------------------------------------------------------
# Code exchange
# ---------------------------------------------------------------------------


async def exchange_code(
    code: str, code_verifier: str | None = None, nonce: str | None = None
) -> dict:
    """Exchange an authorization code for tokens; return the decoded ID-token claims.

    The ID token signature is verified against the provider's JWKS. When
    *code_verifier* is given it is sent in the token request (PKCE); when
    *nonce* is given the ID token's ``nonce`` claim must match it.

    Raises:
        httpx.HTTPStatusError: if the token endpoint returns a non-2xx status.
        jose.JWTError: if the ID token is invalid / cannot be decoded.
        ValueError: if no id_token is returned or the nonce does not match.
    """
    discovery = await _get_discovery()
    token_endpoint = discovery["token_endpoint"]
    issuer = discovery["issuer"]

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.oidc_redirect_uri,
        "client_id": settings.oidc_client_id,
        "client_secret": settings.oidc_client_secret,
    }
    if code_verifier:
        data["code_verifier"] = code_verifier

    resp = await _get_client().post(
        token_endpoint,
        data=data,
        headers={"Accept": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    token_response = resp.json()

    id_token = token_response.get("id_token")
    if not id_token:
        raise ValueError("No id_token in token response")

    def _decode(jwks: dict) -> dict:
        return jose_jwt.decode(
            id_token,
            jwks,
            algorithms=["RS256", "ES256", "RS384", "ES384", "RS512"],
            audience=settings.oidc_client_id,
            issuer=issuer,
            # python-jose validates `at_hash` when present and requires
            # the access token to do so.
            access_token=token_response.get("access_token"),
        )

    try:
        claims: dict = _decode(await _get_jwks())
    except JWTError:
        # Possibly an IdP key rotation: drop the JWKS cache, refetch once.
        global _jwks_cache, _jwks_fetched_at
        _jwks_cache = None
        _jwks_fetched_at = 0
        claims = _decode(await _get_jwks())
    if nonce is not None and claims.get("nonce") != nonce:
        raise ValueError("ID token nonce mismatch")
    return claims


# ---------------------------------------------------------------------------
# User provisioning
# ---------------------------------------------------------------------------


async def get_or_create_user(db: AsyncSession, id_token_claims: dict) -> User:
    """Find an existing user by external_id, or auto-provision a new one.

    The *sub* claim from the ID token is stored as ``external_id``.
    New users are given the ``viewer`` role.

    After the user record is created/found, FsBindings are synced from
    the claims so Search/Browse permission filtering activates on the
    very first request — see api/akashic/auth/oidc_provisioning.py and
    docs/oidc-authentik.md.
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

        # Bounded retry: an IntegrityError may come from the
        # (provider, external_id) race (re-select finds the winner) or
        # from users.username (re-allocate a username and insert again).
        for _attempt in range(5):
            user = User(
                username=username,
                email=id_token_claims.get("email"),
                role="viewer",
                auth_provider="oidc",
                external_id=sub,
            )
            db.add(user)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                user = (await db.execute(
                    select(User).where(User.auth_provider == "oidc", User.external_id == sub)
                )).scalar_one_or_none()
                if user is not None:
                    break
                username = f"{preferred}_{counter}"
                counter += 1
                while (await db.execute(
                    select(User).where(User.username == username)
                )).scalar_one_or_none() is not None:
                    username = f"{preferred}_{counter}"
                    counter += 1
            else:
                await db.refresh(user)
                break
        else:
            raise RuntimeError("could not provision user: username allocation kept colliding")

    # Sync FsBindings from claims. The provisioning module is defensive
    # — it logs and swallows on bad claim shapes, so a malformed token
    # falls through to "you can log in but Browse/Search show
    # everything" rather than 500'ing the callback.
    from akashic.auth.oidc_provisioning import sync_fs_bindings_from_claims

    await sync_fs_bindings_from_claims(db, user, id_token_claims, settings)
    await db.commit()

    return user
