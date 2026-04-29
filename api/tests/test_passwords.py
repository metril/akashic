"""Tests for the direct-bcrypt password helpers.

Covers:
- Round-trip: hash → verify succeeds, wrong-password fails.
- Back-compat: a hash produced by passlib's previous default config
  ($2b$12$…) still verifies via the new bcrypt.checkpw path. Captured
  from a real passlib.CryptContext(schemes=['bcrypt']) hash of 'secret'
  so this test passes without passlib being installed.
- Robustness: None / empty / malformed stored hashes don't raise; they
  return False.
- Timing-leak resistance: verify_password runs an actual bcrypt
  comparison even when stored_hash is None, to mask user-existence.
"""
from __future__ import annotations

import time

from akashic.auth.passwords import hash_password, verify_password


# A real bcrypt-12 hash of the string "secret". passlib's CryptContext
# (schemes=['bcrypt']) and bcrypt.hashpw produce wire-identical output,
# so this fixture stands in for an existing user.password_hash row that
# was created back when we used passlib.
_BCRYPT_HASH_OF_SECRET = "$2b$12$B26gkY0LM5U.WLdyiGmIzuWWQ61.0GhjoeLfeEQtQVrNDMtfQ62Ua"


import pytest


def test_roundtrip():
    h = hash_password("hunter2")
    assert verify_password("hunter2", h) is True
    assert verify_password("wrong", h) is False


def test_hash_password_rejects_oversize():
    """bcrypt silently truncates beyond 72 bytes — auth-bypass risk
    if we don't reject. Normally caught at the schema layer; this is
    the defense-in-depth backstop."""
    pw = "a" * 73
    with pytest.raises(ValueError, match="72-byte"):
        hash_password(pw)


def test_hash_password_rejects_oversize_utf8():
    """Char count vs byte count: a 19-char string of 4-byte emoji is
    76 UTF-8 bytes — must reject even though it's <72 chars."""
    pw = "🔐" * 19  # 19 chars × 4 bytes = 76 bytes
    with pytest.raises(ValueError, match="72-byte"):
        hash_password(pw)


def test_hash_password_accepts_72_bytes_exactly():
    pw = "a" * 72
    h = hash_password(pw)
    assert verify_password(pw, h) is True


def test_back_compat_with_existing_bcrypt_hash():
    """A bcrypt hash produced by passlib's CryptContext is plain bcrypt
    on the wire — bcrypt.checkpw verifies it without any migration of
    existing user.password_hash rows."""
    assert verify_password("secret", _BCRYPT_HASH_OF_SECRET) is True
    assert verify_password("wrong", _BCRYPT_HASH_OF_SECRET) is False


def test_verify_handles_none_hash():
    # Returns False without raising — a valid behavior for "user not found".
    assert verify_password("anything", None) is False


def test_verify_handles_empty_hash():
    assert verify_password("anything", "") is False


def test_verify_handles_malformed_hash():
    # Not a bcrypt hash at all — bcrypt.checkpw raises ValueError, our
    # wrapper catches it.
    assert verify_password("anything", "not-a-hash") is False


@pytest.mark.parametrize("missing_hash", [None, ""])
def test_verify_constant_time_against_missing_user(missing_hash):
    """When stored_hash is missing (None or empty), verify_password should
    still take ~bcrypt time, not return immediately. Without this masking,
    an attacker can enumerate usernames by timing the response.

    Both falsy values must hit the dummy-hash path. We assert the case
    takes at least 50ms — the same order as a real bcrypt compare. Far
    below the ~250ms full bcrypt-12 cost so we don't slow CI noticeably;
    the goal is to detect the short-circuit-when-falsy case.
    """
    real = hash_password("real-pw")

    t0 = time.perf_counter()
    verify_password("anything", real)
    real_dur = time.perf_counter() - t0

    t0 = time.perf_counter()
    verify_password("anything", missing_hash)
    miss_dur = time.perf_counter() - t0

    # Both should be on the same order of magnitude (within 5x). The
    # important assertion is that miss_dur isn't trivially small.
    assert miss_dur > 0.05, (
        f"verify({missing_hash!r}) returned in {miss_dur*1000:.1f}ms — "
        "too fast, suggests no bcrypt work"
    )
    assert miss_dur > real_dur / 5, (
        f"verify({missing_hash!r}) {miss_dur*1000:.1f}ms is much faster "
        f"than real verify {real_dur*1000:.1f}ms — leaks user existence"
    )
