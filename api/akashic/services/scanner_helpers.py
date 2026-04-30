"""Tiny shared helpers for invoking the bundled akashic-scanner binary.

The Phase 14c group resolver, the Phase B1 source_test endpoint, and the
Phase B2 entry-content streamer all need the same `_scanner_binary_path`
lookup and the same stdin-creds JSON shape. Centralizing here so future
changes (e.g., a different env var, a different stdin payload) propagate.
"""
from __future__ import annotations

import json
import os
import shutil

SCANNER_BIN_ENV = "AKASHIC_SCANNER_BIN"


def scanner_binary_path() -> str | None:
    """Returns the akashic-scanner binary path, or None if not findable."""
    explicit = os.environ.get(SCANNER_BIN_ENV)
    if explicit and os.path.isfile(explicit):
        return explicit
    return shutil.which("akashic-scanner")


def stdin_creds_payload(
    password: str = "",
    key_passphrase: str = "",
    krb5_password: str = "",
) -> str:
    """Returns the JSON line the scanner reads from stdin for password +
    passphrase + krb5 password credentials. All fields are always included
    so /proc/<pid>/cmdline never sees them."""
    return json.dumps({
        "password": password,
        "key_passphrase": key_passphrase,
        "krb5_password": krb5_password,
    }) + "\n"
