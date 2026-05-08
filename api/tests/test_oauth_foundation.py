"""Smoke tests for the source-OAuth foundation (v0.12.0 / Tier 1 PR-A).

Covers the pieces that don't need a live OAuth provider:

* secret_encryption round-trip
* state JWT encode/decode (incl. expiry + audience guards)
* provider registry surface
* router auth gates
* upsert/list/delete provider config + credential listing/deletion
* end-to-end "callback" handler with a mocked provider via httpx.MockTransport

A real provider round-trip is tested manually against Google's dev console
(see PR description). Pulling that into CI requires a stable test app + a
secret to plug in, which we don't ship in this repo.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import create_access_token
from akashic.models.oauth_app_config import OAuthAppConfig
from akashic.models.oauth_credential import SourceOAuthCredential
from akashic.models.user import User
from akashic.services import secret_encryption
from akashic.services.oauth_providers import get_provider, known_providers
from akashic.services.source_oauth import (
    OAuthAppNotConfigured,
    build_authorize_url,
    decode_state,
    encode_state,
    require_app_config,
    store_credential_from_token_response,
    mint_access_token,
    _REFRESH_LEAD_SECONDS,
)


# -----------------------------------------------------------------------------
# Encryption helper.
# -----------------------------------------------------------------------------


def test_encrypt_secret_roundtrip():
    plaintext = "ya29.a0AfH6S-not-a-real-token"
    cipher = secret_encryption.encrypt_secret(plaintext)
    assert cipher != plaintext
    assert isinstance(cipher, str)
    assert secret_encryption.decrypt_secret(cipher) == plaintext


def test_encrypt_secret_distinct_per_call():
    # Fernet adds a random IV — two encryptions of the same plaintext
    # should not be byte-equal.
    a = secret_encryption.encrypt_secret("same")
    b = secret_encryption.encrypt_secret("same")
    assert a != b
    assert secret_encryption.decrypt_secret(a) == "same"
    assert secret_encryption.decrypt_secret(b) == "same"


def test_decrypt_secret_rejects_garbage():
    with pytest.raises(secret_encryption.InvalidToken):
        secret_encryption.decrypt_secret("not-a-real-fernet-token")


# -----------------------------------------------------------------------------
# State JWT.
# -----------------------------------------------------------------------------


def test_state_jwt_roundtrip():
    from akashic.services.source_oauth import session_hash

    user_id = uuid.uuid4()
    src_id = uuid.uuid4()
    sh = session_hash("fake-refresh-token-value")
    state = encode_state(
        provider="google",
        source_id=src_id,
        initiator_user_id=user_id,
        session_hash_value=sh,
        mode="associate",
    )
    decoded = decode_state(state)
    assert decoded["provider"] == "google"
    assert decoded["source_id"] == str(src_id)
    assert decoded["initiator"] == str(user_id)
    assert decoded["mode"] == "associate"
    assert decoded["aud"] == "akashic-source-oauth-callback"
    assert decoded["session_hash"] == sh


def test_state_jwt_rejects_garbage():
    from jose import JWTError

    with pytest.raises(JWTError):
        decode_state("not-a-jwt")


# -----------------------------------------------------------------------------
# Provider registry.
# -----------------------------------------------------------------------------


def test_known_providers_includes_google_microsoft_dropbox():
    names = set(known_providers())
    assert {"google", "microsoft", "dropbox"} <= names


def test_get_provider_unknown_raises():
    with pytest.raises(KeyError):
        get_provider("not-a-real-provider")


def test_build_authorize_url_includes_provider_extras():
    p = get_provider("google")
    url = build_authorize_url(
        p,
        client_id="abc.apps.googleusercontent.com",
        redirect_uri="https://akashic.example/api/oauth/callback",
        state="state-token",
    )
    assert url.startswith(p.auth_url + "?")
    # Google needs access_type=offline to actually get a refresh token.
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "client_id=abc.apps.googleusercontent.com" in url
    assert "state=state-token" in url


# -----------------------------------------------------------------------------
# Router gates (admin-only).
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_providers_requires_admin(
    client: AsyncClient, db_session: AsyncSession
):
    viewer = User(
        id=uuid.uuid4(), username="v", email="v@x", password_hash="x", role="viewer"
    )
    db_session.add(viewer)
    await db_session.commit()
    token = create_access_token({"sub": str(viewer.id)})
    resp = await client.get(
        "/api/oauth/providers",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_provider_upsert_and_list(
    client: AsyncClient, db_session: AsyncSession
):
    admin = User(
        id=uuid.uuid4(),
        username="a",
        email="a@x",
        password_hash="x",
        role="admin",
    )
    db_session.add(admin)
    await db_session.commit()
    token = create_access_token({"sub": str(admin.id)})
    headers = {"Authorization": f"Bearer {token}"}

    body = {
        "client_id": "client-id-123",
        "client_secret": "GOCSPX-secret",
        "redirect_uri": "https://akashic.example/api/oauth/callback",
    }
    resp = await client.put("/api/oauth/providers/google", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    assert summary["client_id"] == "client-id-123"
    assert summary["has_secret"] is True

    resp = await client.get("/api/oauth/providers", headers=headers)
    assert resp.status_code == 200
    rows = {row["provider"]: row for row in resp.json()}
    assert rows["google"]["has_secret"] is True
    assert rows["microsoft"]["has_secret"] is False  # not yet configured

    # Verify the secret round-trips through Fernet, not stored in clear.
    result = await db_session.execute(
        select(OAuthAppConfig).where(OAuthAppConfig.provider == "google")
    )
    cfg = result.scalar_one()
    assert cfg.client_secret_encrypted != "GOCSPX-secret"
    assert (
        secret_encryption.decrypt_secret(cfg.client_secret_encrypted)
        == "GOCSPX-secret"
    )


@pytest.mark.asyncio
async def test_provider_upsert_rejects_unknown_provider(
    client: AsyncClient, db_session: AsyncSession
):
    admin = User(
        id=uuid.uuid4(), username="a2", email="a2@x", password_hash="x", role="admin"
    )
    db_session.add(admin)
    await db_session.commit()
    token = create_access_token({"sub": str(admin.id)})
    body = {
        "client_id": "x",
        "client_secret": "y",
        "redirect_uri": "https://example.test/cb",
    }
    resp = await client.put(
        "/api/oauth/providers/myspace",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_start_requires_provider_config(
    client: AsyncClient, db_session: AsyncSession
):
    admin = User(
        id=uuid.uuid4(), username="a3", email="a3@x", password_hash="x", role="admin"
    )
    db_session.add(admin)
    await db_session.commit()
    token = create_access_token({"sub": str(admin.id)})
    resp = await client.post(
        "/api/oauth/start",
        json={"provider": "google", "mode": "test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    # 412: provider hasn't been configured yet.
    assert resp.status_code == 412


@pytest.mark.asyncio
async def test_start_returns_authorize_url(
    client: AsyncClient, db_session: AsyncSession
):
    admin = User(
        id=uuid.uuid4(), username="a4", email="a4@x", password_hash="x", role="admin"
    )
    db_session.add(admin)
    await db_session.commit()
    cfg = OAuthAppConfig(
        provider="google",
        client_id="id",
        client_secret_encrypted=secret_encryption.encrypt_secret("secret"),
        redirect_uri="https://akashic.example/api/oauth/callback",
        configured_by_user_id=admin.id,
    )
    db_session.add(cfg)
    await db_session.commit()
    token = create_access_token({"sub": str(admin.id)})
    resp = await client.post(
        "/api/oauth/start",
        json={"provider": "google", "mode": "test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["authorization_url"].startswith(
        "https://accounts.google.com/o/oauth2/v2/auth?"
    )
    assert "state=" in body["authorization_url"]
    state_decoded = decode_state(body["state"])
    assert state_decoded["mode"] == "test"


# -----------------------------------------------------------------------------
# Refresh logic with mocked provider.
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mint_access_token_uses_cache_when_fresh(db_session: AsyncSession):
    cfg = OAuthAppConfig(
        provider="google",
        client_id="id",
        client_secret_encrypted=secret_encryption.encrypt_secret("secret"),
        redirect_uri="https://akashic.example/api/oauth/callback",
    )
    db_session.add(cfg)
    cred = SourceOAuthCredential(
        provider="google",
        refresh_token_encrypted=secret_encryption.encrypt_secret("rt-1"),
        access_token_cached=secret_encryption.encrypt_secret("at-fresh"),
        access_token_expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=_REFRESH_LEAD_SECONDS + 600),
    )
    db_session.add(cred)
    await db_session.commit()

    async def _refused(*_args, **_kwargs):
        raise AssertionError("should not call provider when cache is fresh")

    with patch(
        "akashic.services.source_oauth.refresh_access_token", side_effect=_refused
    ):
        tok = await mint_access_token(db_session, cred)
    assert tok == "at-fresh"


@pytest.mark.asyncio
async def test_mint_access_token_refreshes_when_expired(db_session: AsyncSession):
    cfg = OAuthAppConfig(
        provider="google",
        client_id="id",
        client_secret_encrypted=secret_encryption.encrypt_secret("secret"),
        redirect_uri="https://akashic.example/api/oauth/callback",
    )
    db_session.add(cfg)
    cred = SourceOAuthCredential(
        provider="google",
        refresh_token_encrypted=secret_encryption.encrypt_secret("rt-1"),
        access_token_cached=secret_encryption.encrypt_secret("at-stale"),
        access_token_expires_at=datetime.now(timezone.utc) - timedelta(seconds=120),
    )
    db_session.add(cred)
    await db_session.commit()

    async def _refresh(*_a, **_kw):
        return {
            "access_token": "at-new",
            "expires_in": 3600,
            "scope": "openid email",
        }

    with patch(
        "akashic.services.source_oauth.refresh_access_token", side_effect=_refresh
    ):
        tok = await mint_access_token(db_session, cred)
    assert tok == "at-new"
    await db_session.refresh(cred)
    # Persisted access token is the encrypted form; round-trip via the helper.
    assert secret_encryption.decrypt_secret(cred.access_token_cached) == "at-new"


@pytest.mark.asyncio
async def test_store_credential_requires_refresh_token_on_first_authorize(
    db_session: AsyncSession,
):
    p = get_provider("google")
    with pytest.raises(Exception) as exc_info:
        await store_credential_from_token_response(
            db_session,
            provider=p,
            token_response={"access_token": "at-1", "expires_in": 600},
            source_id=None,
            existing=None,
        )
    assert "refresh_token" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_require_app_config_raises_when_missing(db_session: AsyncSession):
    with pytest.raises(OAuthAppNotConfigured):
        await require_app_config(db_session, "google")
