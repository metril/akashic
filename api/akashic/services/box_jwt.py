"""Box JWT app-auth — server-to-server access-token minting.

Per Box's "JWT App Auth" protocol (server authentication, no user
involvement):

  1. Operator creates a Custom App in the Box developer console with
     "Server Authentication (with JWT)" enabled, generates an RSA
     keypair, and gets the app authorized in the enterprise admin
     console.
  2. Operator pastes ``client_id``, ``client_secret``, ``enterprise_id``,
     ``public_key_id`` (kid), and the ``private_key`` PEM into the
     source's connection_config.
  3. At lease / probe time we sign a short-lived (≤30s) JWT with
     RS256, exchange it at Box's token endpoint, and inject the
     resulting access token into ``connection_config["access_token"]``
     so the scanner connector consumes the same shape as the OAuth
     path.

There's no refresh token in JWT app-auth — every mint generates a
fresh JWT. The access_token TTL is ~60 minutes; long scans re-mint
via the existing ``POST /api/scanners/oauth/access-token`` endpoint
(server-side that endpoint goes through ``mint_access_token_for_source``
which handles both auth modes transparently).
"""
from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from jose import jwt as jose_jwt

from akashic.services.source_oauth import OAuthExchangeFailed


_BOX_TOKEN_URL = "https://api.box.com/oauth2/token"
_JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"

# Box rejects assertions with exp > 60 seconds in the future. 30s is
# safely under the cap and gives the request plenty of time to land
# even with clock skew.
_ASSERTION_TTL_SECONDS = 30


def build_jwt_assertion(
    *,
    client_id: str,
    enterprise_id: str,
    public_key_id: str,
    private_key: str,
    private_key_passphrase: str | None = None,
) -> str:
    """Sign the ``urn:ietf:params:oauth:grant-type:jwt-bearer``
    assertion Box's token endpoint expects.

    Claims (per Box spec):

      iss          = client_id (the app's id)
      sub          = enterprise_id (the tenant the app is acting on
                                    behalf of)
      box_sub_type = "enterprise"  (we don't support user-impersonation
                                    in v0.19.0; that adds another claim
                                    + a per-source target user_id)
      aud          = Box token URL
      jti          = random nonce — Box rejects replays
      iat          = now
      exp          = now + 30s
    """
    if private_key_passphrase:
        # python-jose's RS256 path doesn't take a passphrase directly;
        # decrypt to PEM bytes via cryptography first.
        from cryptography.hazmat.primitives import serialization
        loaded = serialization.load_pem_private_key(
            private_key.encode("utf-8"),
            password=private_key_passphrase.encode("utf-8"),
        )
        # Re-serialise without encryption so jose can read it.
        signing_key = loaded.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
    else:
        signing_key = private_key
    now = int(time.time())
    claims = {
        "iss": client_id,
        "sub": enterprise_id,
        "box_sub_type": "enterprise",
        "aud": _BOX_TOKEN_URL,
        "jti": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + _ASSERTION_TTL_SECONDS,
    }
    return jose_jwt.encode(
        claims,
        signing_key,
        algorithm="RS256",
        headers={"kid": public_key_id},
    )


async def mint_access_token(
    cfg: dict[str, Any],
) -> tuple[str, datetime | None]:
    """Mint a Box access token for a JWT-app-auth source.

    ``cfg`` is the source's merged connection_config — the JWT-only
    fields are read directly from it. Returns
    ``(access_token, expires_at)`` on success; raises
    ``OAuthExchangeFailed`` (the same error type as OAuth refresh) on
    any failure so call sites that already handle OAuth failures
    apply uniformly.
    """
    client_id = (cfg.get("client_id") or "").strip()
    client_secret = (cfg.get("client_secret") or "").strip()
    enterprise_id = (cfg.get("enterprise_id") or "").strip()
    public_key_id = (cfg.get("public_key_id") or "").strip()
    private_key = (cfg.get("private_key") or "").strip()
    private_key_passphrase = (cfg.get("private_key_passphrase") or "").strip()

    missing = [
        name for name, val in [
            ("client_id", client_id),
            ("client_secret", client_secret),
            ("enterprise_id", enterprise_id),
            ("public_key_id", public_key_id),
            ("private_key", private_key),
        ] if not val
    ]
    if missing:
        raise OAuthExchangeFailed(
            "box",
            f"missing JWT fields: {', '.join(missing)}",
        )

    try:
        assertion = build_jwt_assertion(
            client_id=client_id,
            enterprise_id=enterprise_id,
            public_key_id=public_key_id,
            private_key=private_key,
            private_key_passphrase=private_key_passphrase or None,
        )
    except Exception as exc:
        raise OAuthExchangeFailed(
            "box", f"JWT signing failed: {exc}"
        ) from exc

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _BOX_TOKEN_URL,
            data={
                "grant_type": _JWT_BEARER_GRANT,
                "client_id": client_id,
                "client_secret": client_secret,
                "assertion": assertion,
            },
            headers={"Accept": "application/json"},
            timeout=20,
        )
    if resp.status_code >= 400:
        raise OAuthExchangeFailed("box", resp.text[:500])

    body = resp.json()
    access_token = body.get("access_token")
    if not access_token:
        raise OAuthExchangeFailed(
            "box", "token response missing access_token"
        )
    expires_in = body.get("expires_in")
    expires_at: datetime | None = None
    if isinstance(expires_in, (int, float)):
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=int(expires_in),
        )
    return access_token, expires_at


__all__ = [
    "build_jwt_assertion",
    "mint_access_token",
]
