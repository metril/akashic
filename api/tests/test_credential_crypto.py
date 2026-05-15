"""v0.29.5 — credential_crypto.{encrypt,decrypt}_credentials round-trip.

Verifies the at-rest encryption primitive used by
credential_profile.credentials_encrypted.
"""
from __future__ import annotations

import pytest

from akashic.services.credential_crypto import (
    InvalidToken,
    decrypt_credentials,
    encrypt_credentials,
)


def test_round_trip_preserves_dict():
    creds = {"username": "alice", "password": "s3cret", "domain": "EX"}
    ct = encrypt_credentials(creds)
    assert isinstance(ct, bytes)
    plain = decrypt_credentials(ct)
    assert plain == creds


def test_empty_dict_round_trips():
    ct = encrypt_credentials({})
    assert decrypt_credentials(ct) == {}


def test_none_input_treated_as_empty():
    ct = encrypt_credentials(None)  # type: ignore[arg-type]
    assert decrypt_credentials(ct) == {}


def test_str_input_accepted_for_decrypt():
    """The bytea column on asyncpg comes back as bytes, but a
    str-form ciphertext (caller-decoded) is also accepted."""
    ct = encrypt_credentials({"k": "v"})
    assert decrypt_credentials(ct.decode("ascii")) == {"k": "v"}


def test_tampered_ciphertext_raises():
    ct = encrypt_credentials({"k": "v"})
    tampered = bytearray(ct)
    tampered[10] ^= 0xFF  # flip a byte mid-ciphertext
    # InvalidToken is the typical Fernet failure; on some byte flips
    # the result is non-ASCII and we get UnicodeDecodeError. Either
    # way the point is "decrypt fails loudly, not silently".
    with pytest.raises((InvalidToken, UnicodeDecodeError, ValueError)):
        decrypt_credentials(bytes(tampered))


def test_decrypt_wrong_key_raises(monkeypatch):
    """Build one ciphertext with the current SECRET_KEY, swap the key,
    expect decrypt to refuse it."""
    from akashic.services import secret_encryption

    # Cache reset to force re-derivation under the new key.
    secret_encryption._cached_fernet = None
    secret_encryption._cached_for_key = None

    ct = encrypt_credentials({"k": "v"})

    # Patch settings.secret_key to a different value via the cached
    # primitive's internals.
    secret_encryption._cached_fernet = None
    secret_encryption._cached_for_key = None
    monkeypatch.setattr(
        secret_encryption.settings, "secret_key", "totally-different-key-value",
    )

    with pytest.raises(InvalidToken):
        decrypt_credentials(ct)

    # Reset cache so subsequent tests in the suite get the original key.
    secret_encryption._cached_fernet = None
    secret_encryption._cached_for_key = None


def test_non_str_to_decrypt_raises():
    with pytest.raises(TypeError):
        decrypt_credentials(12345)  # type: ignore[arg-type]
