"""Symmetric secret encryption for at-rest secrets.

Used for OAuth refresh tokens, OAuth client_secrets, and any other
long-lived credential the API has to persist in clear that we want
encrypted at rest. Tokens leased to scanners are still ephemeral
access tokens (re-minted on demand) — those don't pass through this
module.

Key derivation:
  HKDF(SHA-256, settings.secret_key, salt="akashic-oauth-v1", length=32)
    -> base64-urlsafe -> Fernet

If the deployment changes ``AKASHIC_SECRET_KEY``, every value
encrypted with the old key becomes unreadable. There's no envelope-
encryption KMS hook today; rotation means re-running the OAuth
authorization flow per affected source. Documented in the deployment
runbook.
"""

from __future__ import annotations

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from akashic.config import settings


_HKDF_SALT = b"akashic-oauth-v1"
_HKDF_INFO = b"oauth-secret-encryption"

_cached_fernet: Fernet | None = None
_cached_for_key: str | None = None


def _build_fernet(secret_key: str) -> Fernet:
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    ).derive(secret_key.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(derived))


def _get_fernet() -> Fernet:
    global _cached_fernet, _cached_for_key
    key = settings.secret_key
    if _cached_fernet is None or _cached_for_key != key:
        _cached_fernet = _build_fernet(key)
        _cached_for_key = key
    return _cached_fernet


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a UTF-8 string. Output is the urlsafe-base64 Fernet token."""
    if not isinstance(plaintext, str):
        raise TypeError("encrypt_secret expects str")
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Inverse of ``encrypt_secret``. Raises ``InvalidToken`` on tamper/key mismatch."""
    if not isinstance(token, str):
        raise TypeError("decrypt_secret expects str")
    return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")


__all__ = ["encrypt_secret", "decrypt_secret", "InvalidToken"]
