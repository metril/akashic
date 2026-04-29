"""Direct bcrypt password hashing.

Replaces the previous passlib.context.CryptContext usage. passlib is
effectively unmaintained (1.7.4 is the latest release, ~2020) and its
bcrypt version detection raises an internal AttributeError on bcrypt 4.x
that's silently caught — log noise we'd like to avoid.

Hashes are wire-compatible with passlib's bcrypt output ($2b$12$…), so
existing user.password_hash values continue to verify correctly.
"""
from __future__ import annotations

import secrets

import bcrypt

# bcrypt cost factor. 12 is the default passlib used and a reasonable
# 2026 baseline (~250ms on modern x86). Raising costs more CPU per login;
# lowering weakens against offline cracking.
_BCRYPT_ROUNDS = 12

# bcrypt silently truncates inputs beyond 72 bytes — auth-bypass risk if
# unchecked. The UserCreate schema rejects this at the API edge; the
# defensive check below makes hash_password safe to call from anywhere.
_BCRYPT_MAX_BYTES = 72

# Static dummy hash used to keep the verify() runtime constant when the
# user lookup misses. Without this, a non-existent username returns
# faster than an existing one (no bcrypt call) — a username-enumeration
# oracle. Generated once at module load with a known constant, NOT a
# secret: its value doesn't matter, only that bcrypt does work to
# verify against it.
_DUMMY_HASH = bcrypt.hashpw(b"akashic-dummy-input", bcrypt.gensalt(_BCRYPT_ROUNDS))


def hash_password(plaintext: str) -> str:
    """Returns a bcrypt hash suitable for storing in user.password_hash.

    Raises ValueError if plaintext exceeds 72 UTF-8 bytes — refusing to
    silently truncate. The API normally rejects oversize passwords at
    the schema layer; this guard backstops direct callers."""
    encoded = plaintext.encode("utf-8")
    if len(encoded) > _BCRYPT_MAX_BYTES:
        raise ValueError(
            f"Password exceeds {_BCRYPT_MAX_BYTES}-byte bcrypt limit"
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt(_BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(plaintext: str, stored_hash: str | None) -> bool:
    """Verify plaintext against a stored bcrypt hash.

    Returns False on every failure mode (None hash, malformed hash, no
    match) without raising. Always runs an actual bcrypt comparison —
    even when stored_hash is None — so the timing of "user doesn't
    exist" matches the timing of "user exists, wrong password".
    """
    if stored_hash:
        target = stored_hash.encode("utf-8")
    else:
        target = _DUMMY_HASH
    try:
        ok = bcrypt.checkpw(plaintext.encode("utf-8"), target)
    except Exception:  # noqa: BLE001
        # Malformed stored hash — every failure mode (ValueError, TypeError,
        # and bcrypt 4.2.x's pyo3_runtime.PanicException for some
        # syntactically-bcrypt-like-but-corrupt rows) is a verification
        # failure. Catch broadly so /login can never 500 on a bad row.
        # Still ran a comparison, so timing is consistent with the
        # success path.
        ok = False
    # If we ran against the dummy, force-fail without short-circuiting.
    if not stored_hash:
        ok = False
    # secrets.compare_digest is overkill for a bool — but it's a good
    # signal to readers that this branch is timing-sensitive.
    return secrets.compare_digest(b"\x01" if ok else b"\x00", b"\x01")
