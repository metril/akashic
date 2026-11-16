"""Shared objectSid decoders (binary LDAP wire format, string, base64)."""
from __future__ import annotations

import base64
import binascii
from typing import Any


def decode_binary_sid(raw: Any) -> str | None:
    """Decode a raw LDAP objectSid (8 + 4n bytes, n <= 15) to ``S-1-...``.
    Returns None for anything malformed."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 8:
        return None
    revision = raw[0]
    sub_auth_count = raw[1]
    if len(raw) != 8 + 4 * sub_auth_count or sub_auth_count > 15:
        return None
    ident_auth = int.from_bytes(raw[2:8], "big")
    parts = [str(revision), str(ident_auth)]
    for i in range(sub_auth_count):
        offset = 8 + 4 * i
        parts.append(str(int.from_bytes(raw[offset:offset + 4], "little")))
    return "S-" + "-".join(parts)


def decode_object_sid(value: Any) -> str | None:
    """Normalize an objectSid-shaped value (string, list of one, or base64
    binary) into the canonical upper-case ``S-1-...`` string, else None."""
    if value is None:
        return None
    if isinstance(value, list):
        value = next((v for v in value if v), None)
        if value is None:
            return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if s.startswith("S-1-") or s.startswith("s-1-"):
        return s.upper()
    try:
        raw = base64.b64decode(s, validate=False)
    except (binascii.Error, ValueError):
        return None
    return decode_binary_sid(raw)
