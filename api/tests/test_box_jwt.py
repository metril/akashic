"""v0.19.0 — Box JWT app-auth helper tests.

Covers the assertion-shape contract (RS256 + kid, all required claims,
exp ≤ 60s) and the mint_access_token error path. End-to-end
exchange against Box's live token endpoint isn't tested here — that
needs a real Box developer-app + private-key pair, which we don't
ship in CI. Manual verification against a real tenant is documented
in the PR description.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt as jose_jwt

from akashic.services import box_jwt
from akashic.services.source_oauth import OAuthExchangeFailed


@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[str, str]:
    """Generate an in-memory RSA keypair to sign + verify against."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return private_pem, public_pem


def test_build_jwt_assertion_shape(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    assertion = box_jwt.build_jwt_assertion(
        client_id="client-id-123",
        enterprise_id="987654",
        public_key_id="kid-abc",
        private_key=private_pem,
    )
    # Decode without verifying signature first to inspect headers + claims.
    headers = jose_jwt.get_unverified_header(assertion)
    assert headers["alg"] == "RS256"
    assert headers["kid"] == "kid-abc"

    # Verify the signature against the matching public key — proves
    # the token actually got signed with the private half rather than
    # being unsigned-but-shaped-right.
    claims = jose_jwt.decode(
        assertion,
        public_pem,
        algorithms=["RS256"],
        audience="https://api.box.com/oauth2/token",
    )
    assert claims["iss"] == "client-id-123"
    assert claims["sub"] == "987654"
    assert claims["box_sub_type"] == "enterprise"
    assert claims["aud"] == "https://api.box.com/oauth2/token"
    # 30s window per Box's spec; allow ±5s for clock-fuzz tolerance.
    now = int(time.time())
    assert claims["iat"] <= now + 5
    assert claims["exp"] >= claims["iat"] + 25
    assert claims["exp"] <= claims["iat"] + 35
    # jti is a random nonce — just check it's there + non-empty.
    assert claims["jti"]


def test_build_jwt_assertion_handles_encrypted_pem(rsa_keypair):
    """An encrypted PEM goes through the cryptography decrypt path
    rather than handing the encrypted bytes to jose."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    encrypted_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(b"hunter2"),
    ).decode("ascii")
    public_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")

    assertion = box_jwt.build_jwt_assertion(
        client_id="cid",
        enterprise_id="ent",
        public_key_id="kid-x",
        private_key=encrypted_pem,
        private_key_passphrase="hunter2",
    )
    claims = jose_jwt.decode(
        assertion,
        public_pem,
        algorithms=["RS256"],
        audience="https://api.box.com/oauth2/token",
    )
    assert claims["iss"] == "cid"


@pytest.mark.asyncio
async def test_mint_access_token_rejects_missing_fields():
    with pytest.raises(OAuthExchangeFailed) as exc_info:
        await box_jwt.mint_access_token({"auth_mode": "jwt"})
    assert "missing JWT fields" in exc_info.value.detail


@pytest.mark.asyncio
async def test_mint_access_token_rejects_bad_private_key():
    with pytest.raises(OAuthExchangeFailed) as exc_info:
        await box_jwt.mint_access_token({
            "auth_mode": "jwt",
            "client_id": "cid",
            "client_secret": "secret",
            "enterprise_id": "ent",
            "public_key_id": "kid",
            "private_key": "not-a-real-pem",
        })
    assert "JWT signing failed" in exc_info.value.detail


@pytest.mark.asyncio
async def test_mint_access_token_exchange_success(rsa_keypair):
    """Stub the httpx POST so we can assert the Box-shaped exchange
    payload + the token-response handling."""
    private_pem, _ = rsa_keypair

    captured = {}

    class _StubResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "at-fresh", "expires_in": 3600}

    class _StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["data"] = kwargs.get("data")
            return _StubResponse()

    with patch("akashic.services.box_jwt.httpx.AsyncClient", _StubClient):
        token, expires_at = await box_jwt.mint_access_token({
            "auth_mode": "jwt",
            "client_id": "cid",
            "client_secret": "secret",
            "enterprise_id": "ent",
            "public_key_id": "kid",
            "private_key": private_pem,
        })

    assert token == "at-fresh"
    assert expires_at is not None
    assert captured["url"] == "https://api.box.com/oauth2/token"
    data = captured["data"]
    assert data["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    assert data["client_id"] == "cid"
    assert data["client_secret"] == "secret"
    assert data["assertion"]  # JWT body — exact contents covered by the
                              # build_jwt_assertion test above


@pytest.mark.asyncio
async def test_mint_access_token_propagates_provider_error(rsa_keypair):
    """A 4xx from Box's token endpoint surfaces as OAuthExchangeFailed
    so call sites that already handle OAuth refresh errors apply."""
    private_pem, _ = rsa_keypair

    class _StubResponse:
        status_code = 400
        text = '{"error":"invalid_grant","error_description":"bad jti"}'

        @staticmethod
        def json():
            return {"error": "invalid_grant"}

    class _StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, *_, **__):
            return _StubResponse()

    with patch("akashic.services.box_jwt.httpx.AsyncClient", _StubClient):
        with pytest.raises(OAuthExchangeFailed):
            await box_jwt.mint_access_token({
                "client_id": "cid",
                "client_secret": "secret",
                "enterprise_id": "ent",
                "public_key_id": "kid",
                "private_key": private_pem,
            })
