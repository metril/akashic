"""Symmetric encryption for credential_profile.credentials at rest (v0.29.5).

Reuses the OAuth refresh-token encryption primitive
(:mod:`akashic.services.secret_encryption`) so a single key derivation
(HKDF over ``settings.secret_key``) protects every sensitive at-rest
surface. Adds a thin layer that handles dict ↔ bytes via JSON so the
``credential_profiles.credentials_encrypted`` column gets a single
bytea blob rather than a per-field encrypted map.

If ``settings.secret_key`` rotates, every previously-encrypted profile
becomes unreadable — same property as the OAuth credentials. The
deployment runbook should warn about this.
"""
from __future__ import annotations

import json

from akashic.services.secret_encryption import (
    InvalidToken,
    decrypt_secret,
    encrypt_secret,
)

__all__ = [
    "encrypt_credentials",
    "decrypt_credentials",
    "InvalidToken",
]


def encrypt_credentials(creds: dict) -> bytes:
    """Encrypt a credential dict. Output is the urlsafe-base64 Fernet
    token as bytes (suitable for a ``bytea`` column).

    Empty / None input encrypts to a valid token for ``{}`` — the
    column never contains an "is this set?" sentinel; presence of a
    non-NULL value means "this profile is encrypted".
    """
    payload = json.dumps(creds or {}, sort_keys=True, ensure_ascii=False)
    return encrypt_secret(payload).encode("ascii")


def decrypt_credentials(token: bytes | str) -> dict:
    """Inverse of :func:`encrypt_credentials`. Raises ``InvalidToken``
    on tamper / wrong key / corrupted ciphertext.

    Accepts ``bytes`` (the natural shape of the bytea column read via
    asyncpg) or ``str`` (memoryview-decoded or already-string) for
    convenience.
    """
    if isinstance(token, (bytes, bytearray, memoryview)):
        token = bytes(token).decode("ascii")
    if not isinstance(token, str):
        raise TypeError("decrypt_credentials expects bytes or str")
    plain = decrypt_secret(token)
    obj = json.loads(plain)
    if not isinstance(obj, dict):
        raise InvalidToken("decrypted payload is not a JSON object")
    return obj
