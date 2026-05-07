"""Source-level OAuth: authorize-code start, callback exchange, token refresh.

This module is the API-side glue between the OAuth provider (Google,
Microsoft, etc.), the encrypted-at-rest credential store, and the
scanners that need short-lived access tokens during a scan.

The flow:

  1. User clicks "Sign in with Google" (or whichever provider) in the
     UI. The frontend POSTs ``/sources/oauth/start`` and gets back an
     ``authorization_url``. It opens that URL in a popup.
  2. Provider redirects to ``/sources/oauth/callback?code=...&state=...``
     once the user consents. The callback exchanges the code for an
     access + refresh token pair, fetches the connected account's
     email/name, encrypts the refresh token, and persists the
     ``SourceOAuthCredential`` row.
  3. When a scanner needs to read files from this source, the API
     calls ``mint_access_token(credential)`` which returns a fresh
     access token — refreshing against the provider when the cached
     one is within the safety margin of expiry.

The refresh token never leaves the API. Scanners get an access-token
TTL'd by the provider (typically 1 hour); long scans re-mint mid-run.

State for the authorize → callback round-trip is a short-lived JWT
signed with ``settings.secret_key`` (no DB storage, no Redis). It
encodes the provider, the optional source_id we want to associate
with, and the initiating user's id so the callback can re-authenticate
the inbound request.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from jose import jwt as jose_jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.config import settings
from akashic.models.oauth_app_config import OAuthAppConfig
from akashic.models.oauth_credential import SourceOAuthCredential
from akashic.services.oauth_providers import OAuthProvider, get_provider
from akashic.services.secret_encryption import decrypt_secret, encrypt_secret


# -----------------------------------------------------------------------------
# State token (signed JWT, short-lived).
# -----------------------------------------------------------------------------

_STATE_ALG = "HS256"
_STATE_TTL_SECONDS = 600  # 10 minutes — covers slow consent flows comfortably.


def _state_secret() -> str:
    # Distinct from the data-encryption key derivation: HKDF goes through
    # secret_encryption; state-token signing uses the raw secret_key with
    # an aud claim namespace so a leaked oauth-state token can't be
    # replayed against any other JWT-bearing path.
    return settings.secret_key


def encode_state(
    *,
    provider: str,
    source_id: uuid.UUID | None,
    initiator_user_id: uuid.UUID,
    mode: str = "associate",
) -> str:
    now = int(time.time())
    payload = {
        "iss": "akashic-source-oauth",
        "aud": "akashic-source-oauth-callback",
        "iat": now,
        "exp": now + _STATE_TTL_SECONDS,
        "nonce": uuid.uuid4().hex,
        "provider": provider,
        "source_id": str(source_id) if source_id else None,
        "initiator": str(initiator_user_id),
        "mode": mode,
    }
    return jose_jwt.encode(payload, _state_secret(), algorithm=_STATE_ALG)


def decode_state(token: str) -> dict[str, Any]:
    return jose_jwt.decode(
        token,
        _state_secret(),
        algorithms=[_STATE_ALG],
        audience="akashic-source-oauth-callback",
    )


# -----------------------------------------------------------------------------
# App-config helpers.
# -----------------------------------------------------------------------------


async def get_app_config(db: AsyncSession, provider: str) -> OAuthAppConfig | None:
    result = await db.execute(
        select(OAuthAppConfig).where(OAuthAppConfig.provider == provider)
    )
    return result.scalar_one_or_none()


async def require_app_config(db: AsyncSession, provider: str) -> OAuthAppConfig:
    cfg = await get_app_config(db, provider)
    if cfg is None:
        raise OAuthAppNotConfigured(provider)
    return cfg


class OAuthAppNotConfigured(Exception):
    def __init__(self, provider: str) -> None:
        super().__init__(
            f"OAuth app for provider {provider!r} is not configured. "
            "Set client_id/client_secret in Settings → OAuth Providers."
        )
        self.provider = provider


class OAuthExchangeFailed(Exception):
    def __init__(self, provider: str, detail: str) -> None:
        super().__init__(f"OAuth exchange failed for {provider}: {detail}")
        self.provider = provider
        self.detail = detail


# -----------------------------------------------------------------------------
# Authorize URL.
# -----------------------------------------------------------------------------


def build_authorize_url(
    provider: OAuthProvider,
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
) -> str:
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(provider.scopes),
        "state": state,
    }
    params.update(provider.extra_auth_params)
    return f"{provider.auth_url}?{urlencode(params)}"


# -----------------------------------------------------------------------------
# Code exchange + refresh.
# -----------------------------------------------------------------------------


async def exchange_code(
    provider: OAuthProvider,
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
) -> dict[str, Any]:
    """Swap an authorization code for a token pair. Returns the raw token
    response from the provider (access_token, refresh_token?, expires_in,
    scope, ...). Caller is responsible for persistence + refresh-token
    presence checks."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            provider.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
            timeout=20,
        )
    if resp.status_code >= 400:
        raise OAuthExchangeFailed(provider.name, resp.text)
    return resp.json()


async def refresh_access_token(
    provider: OAuthProvider,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            provider.token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Accept": "application/json"},
            timeout=20,
        )
    if resp.status_code >= 400:
        raise OAuthExchangeFailed(provider.name, resp.text)
    return resp.json()


async def fetch_userinfo(
    provider: OAuthProvider, access_token: str
) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        # Dropbox accepts Bearer + GET on /2/users/get_current_account
        # even though its docs prefer POST. Microsoft Graph and Google
        # both require GET.
        resp = await client.get(
            provider.userinfo_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=15,
        )
    if resp.status_code >= 400:
        raise OAuthExchangeFailed(
            provider.name, f"userinfo {resp.status_code}: {resp.text}"
        )
    return resp.json()


# -----------------------------------------------------------------------------
# High-level credential persistence.
# -----------------------------------------------------------------------------


async def store_credential_from_token_response(
    db: AsyncSession,
    *,
    provider: OAuthProvider,
    token_response: dict[str, Any],
    source_id: uuid.UUID | None,
    existing: SourceOAuthCredential | None = None,
) -> SourceOAuthCredential:
    """Persist (or update) the credential row given a code-exchange or
    refresh-exchange response. Fetches userinfo on first authorization
    so the UI has an "connected as ..." label to render."""

    refresh_token = token_response.get("refresh_token")
    access_token = token_response.get("access_token")
    if not access_token:
        raise OAuthExchangeFailed(provider.name, "missing access_token in response")

    expires_in = token_response.get("expires_in")
    expires_at: datetime | None = None
    if isinstance(expires_in, (int, float)):
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))

    if existing is None:
        if not refresh_token:
            raise OAuthExchangeFailed(
                provider.name,
                "provider did not return a refresh_token — re-check provider "
                "settings (Google requires access_type=offline + prompt=consent; "
                "Dropbox requires token_access_type=offline)",
            )

        userinfo = await fetch_userinfo(provider, access_token)
        cred = SourceOAuthCredential(
            source_id=source_id,
            provider=provider.name,
            refresh_token_encrypted=encrypt_secret(refresh_token),
            access_token_cached=encrypt_secret(access_token),
            access_token_expires_at=expires_at,
            scope=token_response.get("scope"),
            account_email=userinfo.get(provider.email_field),
            account_label=userinfo.get(provider.name_field),
        )
        db.add(cred)
        await db.commit()
        await db.refresh(cred)
        return cred

    # Updating an existing row from a refresh response. Most providers
    # don't return a new refresh_token on refresh; preserve the old one
    # unless one is actually returned.
    existing.access_token_cached = encrypt_secret(access_token)
    existing.access_token_expires_at = expires_at
    if refresh_token:
        existing.refresh_token_encrypted = encrypt_secret(refresh_token)
    await db.commit()
    await db.refresh(existing)
    return existing


# -----------------------------------------------------------------------------
# Mint a fresh access token for a leased source.
# -----------------------------------------------------------------------------


# Refresh when the cached token has fewer than this many seconds left.
# 60s buys plenty of margin for a long-running scan worker to grab a
# fresh token before in-flight API calls start 401'ing.
_REFRESH_LEAD_SECONDS = 60


async def mint_access_token(
    db: AsyncSession, credential: SourceOAuthCredential
) -> str:
    """Return a fresh access token for the given credential row.

    Uses the cached access token if it still has > _REFRESH_LEAD_SECONDS
    until expiry. Otherwise calls the provider's refresh endpoint,
    persists the new token + expiry, and returns it.
    """
    cached = credential.access_token_cached
    expires_at = credential.access_token_expires_at
    if cached and expires_at and expires_at > datetime.now(timezone.utc) + timedelta(
        seconds=_REFRESH_LEAD_SECONDS
    ):
        try:
            return decrypt_secret(cached)
        except Exception:
            # Encryption-key mismatch (rotated AKASHIC_SECRET_KEY) or
            # tampered row. Fall through to refresh below — the
            # encrypted refresh_token will hit the same problem and
            # surface a clean error to the caller.
            pass

    provider = get_provider(credential.provider)
    app = await require_app_config(db, credential.provider)
    refresh_token = decrypt_secret(credential.refresh_token_encrypted)
    client_secret = decrypt_secret(app.client_secret_encrypted)

    token_response = await refresh_access_token(
        provider,
        client_id=app.client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
    )
    await store_credential_from_token_response(
        db,
        provider=provider,
        token_response=token_response,
        source_id=credential.source_id,
        existing=credential,
    )
    new_access = token_response.get("access_token")
    if not new_access:
        raise OAuthExchangeFailed(
            credential.provider, "refresh response missing access_token"
        )
    return new_access


async def get_credential_for_source(
    db: AsyncSession, source_id: uuid.UUID
) -> SourceOAuthCredential | None:
    result = await db.execute(
        select(SourceOAuthCredential).where(
            SourceOAuthCredential.source_id == source_id
        )
    )
    return result.scalar_one_or_none()


async def mint_access_token_for_source(
    db: AsyncSession, source_id: uuid.UUID
) -> tuple[str, datetime | None] | None:
    """Return ``(fresh_access_token, expires_at)`` for an OAuth-attached
    source. Returns ``None`` when no credential is wired up — the caller
    treats the source as auth-disabled and skips access-token injection.

    Looks up the source's ``SourceOAuthCredential`` row and refreshes
    if expired. Called at scan-lease time and from the scanner-facing
    refresh endpoint. Auth failures propagate as ``OAuthExchangeFailed``.
    """
    cred = await get_credential_for_source(db, source_id)
    if cred is None:
        return None
    token = await mint_access_token(db, cred)
    return token, cred.access_token_expires_at


__all__ = [
    "OAuthAppNotConfigured",
    "OAuthExchangeFailed",
    "build_authorize_url",
    "decode_state",
    "encode_state",
    "exchange_code",
    "fetch_userinfo",
    "get_app_config",
    "get_credential_for_source",
    "mint_access_token",
    "mint_access_token_for_source",
    "refresh_access_token",
    "require_app_config",
    "store_credential_from_token_response",
]
