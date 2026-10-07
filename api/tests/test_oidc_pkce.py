import base64
import hashlib
import time
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt

from akashic.auth import oidc
from akashic.config import settings


@pytest.fixture(autouse=True)
def _clear_cache():
    oidc.invalidate_cache()
    yield
    oidc.invalidate_cache()


def test_generate_pkce():
    v, c = oidc.generate_pkce()
    assert 43 <= len(v) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    assert c == expected and "=" not in c
    assert oidc.generate_pkce()[0] != v


@pytest.fixture
def idp(monkeypatch, httpx_mock):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    pub_jwk = jwk.construct(pub, algorithm="RS256").to_dict()
    pub_jwk.update(kid="k1", use="sig", alg="RS256")
    monkeypatch.setattr(
        oidc,
        "_get_discovery",
        AsyncMock(return_value={
            "token_endpoint": "https://idp.example/token",
            "issuer": "https://idp.example",
            "jwks_uri": "https://idp.example/jwks",
        }),
    )
    monkeypatch.setattr(oidc, "_get_jwks", AsyncMock(return_value={"keys": [pub_jwk]}))
    claims = {
        "sub": "u1",
        "iss": "https://idp.example",
        "aud": settings.oidc_client_id,
        "exp": int(time.time()) + 300,
        "nonce": "good",
    }
    tok = jwt.encode(claims, priv, algorithm="RS256", headers={"kid": "k1"})
    httpx_mock.add_response(url="https://idp.example/token", json={"id_token": tok}, is_reusable=True)
    return httpx_mock


@pytest.mark.asyncio
async def test_exchange_code_nonce_and_verifier(idp):
    claims = await oidc.exchange_code("c", code_verifier="v", nonce="good")
    assert claims["sub"] == "u1"
    body = parse_qs(idp.get_requests()[0].content.decode())
    assert body["code_verifier"] == ["v"]
    with pytest.raises(ValueError):
        await oidc.exchange_code("c", code_verifier="v", nonce="bad")


@pytest.mark.asyncio
async def test_exchange_code_rejects_token_without_nonce(idp, monkeypatch):
    # Reuse the fixture's key material but strip the nonce from the token.
    import jose.jwt as jose_jwt

    real_decode = jose_jwt.decode

    def decode_without_nonce(*a, **kw):
        claims = real_decode(*a, **kw)
        claims.pop("nonce", None)
        return claims

    monkeypatch.setattr(oidc.jose_jwt, "decode", decode_without_nonce)
    with pytest.raises(ValueError):
        await oidc.exchange_code("c", code_verifier="v", nonce="good")
